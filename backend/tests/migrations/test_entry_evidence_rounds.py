import runpy
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from alembic import command


def _config(database_url: str) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_entry_evidence_round_migration_preserves_legacy_observation_and_links_audit(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "entry-evidence-rounds.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    command.upgrade(config, "0009_company_identity_review")
    company_id = str(uuid4())
    manifest_version = "a" * 64
    legacy_observation_id = str(uuid4())
    created_at = "2026-08-09 00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, canonical_name, normalized_name, normalized_website, funding_stage, "
                "scale, created_at, updated_at) VALUES "
                "(:id, 'Example', 'example', '', 'unknown', 'unknown', :at, :at)"
            ),
            {"id": company_id, "at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO company_manifests "
                "(version, config_fingerprint, member_count, canonical_quota, frozen_at) "
                "VALUES (:version, :fingerprint, 1, '{}', :at)"
            ),
            {"version": manifest_version, "fingerprint": "b" * 64, "at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO company_manifest_members "
                "(id, manifest_version, company_id, position, canonical_name, primary_category) "
                "VALUES (:id, :version, :company_id, 1, 'Example', 'foundation_models')"
            ),
            {"id": str(uuid4()), "version": manifest_version, "company_id": company_id},
        )
        connection.execute(
            text(
                "INSERT INTO entry_discovery_observations "
                "(id, manifest_version, company_id, method, status, requires_rendering, "
                "error_code, observed_at) VALUES "
                "(:id, :version, :company_id, 'official_navigation', 'not_found', 0, "
                "'recruitment_entry_not_found', :at)"
            ),
            {
                "id": legacy_observation_id,
                "version": manifest_version,
                "company_id": company_id,
                "at": created_at,
            },
        )

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert {
        "entry_discovery_rounds",
        "entry_evidence_audit_samples",
        "entry_evidence_audit_findings",
    } <= set(inspector.get_table_names())
    assert {column["name"] for column in inspector.get_columns("entry_discovery_observations")} >= {
        "discovery_round_id",
        "predecessor_observation_id",
    }
    assert {
        index["name"] for index in inspector.get_indexes("entry_discovery_observations")
    } >= {
        "ix_discovery_observations_round_status",
        "ix_discovery_observations_predecessor",
    }

    round_id = str(uuid4())
    successor_id = str(uuid4())
    sample_id = str(uuid4())
    finding_id = str(uuid4())
    with engine.begin() as connection:
        legacy = connection.execute(
            text(
                "SELECT status, error_code, discovery_round_id, predecessor_observation_id "
                "FROM entry_discovery_observations WHERE id = :id"
            ),
            {"id": legacy_observation_id},
        ).one()
        assert legacy == ("not_found", "recruitment_entry_not_found", None, None)
        connection.execute(
            text(
                "INSERT INTO entry_discovery_rounds "
                "(id, manifest_version, name, config_fingerprint, model_fingerprint, started_at) "
                "VALUES (:id, :version, 'entry-evidence-2026-08-09', :config, :model, :at)"
            ),
            {
                "id": round_id,
                "version": manifest_version,
                "config": "c" * 64,
                "model": "d" * 64,
                "at": "2026-08-09 00:01:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO entry_discovery_observations "
                "(id, manifest_version, discovery_round_id, predecessor_observation_id, "
                "company_id, method, status, source_id, platform, requires_rendering, "
                "observed_at) VALUES "
                "(:id, :version, :round_id, :predecessor_id, :company_id, "
                "'official_navigation', 'accepted', 'public_registry', 'custom', 0, :at)"
            ),
            {
                "id": successor_id,
                "version": manifest_version,
                "round_id": round_id,
                "predecessor_id": legacy_observation_id,
                "company_id": company_id,
                "at": "2026-08-09 00:02:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO entry_evidence_audit_samples "
                "(id, discovery_round_id, observation_id, source_id, platform, selected_at) "
                "VALUES (:id, :round_id, :observation_id, 'public_registry', 'custom', :at)"
            ),
            {
                "id": sample_id,
                "round_id": round_id,
                "observation_id": successor_id,
                "at": "2026-08-09 00:03:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO entry_evidence_audit_findings "
                "(id, audit_sample_id, severe_error, reason, audited_at) "
                "VALUES (:id, :sample_id, 1, 'Wrong legal entity', :at)"
            ),
            {
                "id": finding_id,
                "sample_id": sample_id,
                "at": "2026-08-09 00:04:00+00:00",
            },
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO entry_evidence_audit_samples "
                    "(id, discovery_round_id, observation_id, source_id, platform, selected_at) "
                    "VALUES (:id, :round_id, :legacy_id, 'public_registry', 'custom', :at)"
                ),
                {
                    "id": str(uuid4()),
                    "round_id": round_id,
                    "legacy_id": legacy_observation_id,
                    "at": "2026-08-09 00:03:00+00:00",
                },
            )
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []

    command.downgrade(config, "0009_company_identity_review")
    inspector = inspect(engine)
    assert {
        "entry_discovery_rounds",
        "entry_evidence_audit_samples",
        "entry_evidence_audit_findings",
    }.isdisjoint(inspector.get_table_names())
    assert {column["name"] for column in inspector.get_columns("entry_discovery_observations")}.isdisjoint(
        {"discovery_round_id", "predecessor_observation_id"}
    )
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, status, error_code FROM entry_discovery_observations "
                "ORDER BY observed_at"
            )
        ).all()
        assert rows == [
            (legacy_observation_id, "not_found", "recruitment_entry_not_found"),
            (successor_id, "accepted", None),
        ]
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_entry_evidence_round_migration_emits_named_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0010_entry_evidence_rounds.py"
    )
    migration = runpy.run_path(str(migration_path))
    output = StringIO()
    context = MigrationContext.configure(
        dialect=postgresql.dialect(),
        opts={"as_sql": True, "output_buffer": output},
    )
    migration["upgrade"].__globals__["op"] = Operations(context)

    migration["upgrade"]()

    sql = " ".join(output.getvalue().split())
    assert migration["revision"] == "0010_entry_evidence_rounds"
    assert migration["down_revision"] == "0009_company_identity_review"
    for constraint_name in (
        "fk_discovery_round_predecessor_manifest",
        "fk_discovery_observation_round_manifest",
        "fk_discovery_observation_predecessor_identity",
        "fk_evidence_audit_sample_observation_round",
        "uq_discovery_observation_round_company",
        "uq_evidence_audit_finding_sample",
    ):
        assert f"CONSTRAINT {constraint_name}" in sql
    for index_name in (
        "ix_discovery_rounds_manifest_started",
        "ix_discovery_observations_round_status",
        "ix_discovery_observations_predecessor",
        "ix_evidence_audit_samples_stratum",
        "ix_evidence_audit_findings_severe_audited",
    ):
        assert f"CREATE INDEX {index_name}" in sql


def test_entry_evidence_round_sqlite_offline_sql_preserves_legacy_indexes() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0010_entry_evidence_rounds.py"
    )
    migration = runpy.run_path(str(migration_path))
    output = StringIO()
    context = MigrationContext.configure(
        dialect=sqlite.dialect(),
        opts={"as_sql": True, "output_buffer": output},
    )
    migration["upgrade"].__globals__["op"] = Operations(context)

    migration["upgrade"]()

    sql = " ".join(output.getvalue().split())
    assert "CREATE INDEX ix_discovery_observations_manifest_status" in sql
    assert "CREATE INDEX ix_discovery_observations_company_observed" in sql

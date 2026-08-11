import runpy
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from alembic import command


def _config(database_url: str) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_integrity_migration_backfills_quarantine_and_blocks_mutation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "entry-evidence-integrity.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    command.upgrade(config, "0010_entry_evidence_rounds")
    company_id = str(uuid4())
    manifest_version = "a" * 64
    round_id = str(uuid4())
    observation_id = str(uuid4())
    sample_id = str(uuid4())
    finding_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, canonical_name, normalized_name, normalized_website, funding_stage, "
                "scale, created_at, updated_at) VALUES "
                "(:id, 'Example', 'example', '', 'unknown', 'unknown', :at, :at)"
            ),
            {"id": company_id, "at": "2026-08-10 00:00:00+00:00"},
        )
        connection.execute(
            text(
                "INSERT INTO company_manifests "
                "(version, config_fingerprint, member_count, canonical_quota, frozen_at) "
                "VALUES (:version, :fingerprint, 1, '{}', :at)"
            ),
            {
                "version": manifest_version,
                "fingerprint": "b" * 64,
                "at": "2026-08-10 00:00:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO entry_discovery_rounds "
                "(id, manifest_version, name, config_fingerprint, model_fingerprint, started_at) "
                "VALUES (:id, :version, 'integrity-round', :config, :model, :at)"
            ),
            {
                "id": round_id,
                "version": manifest_version,
                "config": "c" * 64,
                "model": "d" * 64,
                "at": "2026-08-10 00:01:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO entry_discovery_observations "
                "(id, manifest_version, discovery_round_id, company_id, method, status, "
                "source_id, platform, requires_rendering, observed_at) VALUES "
                "(:id, :version, :round_id, :company_id, 'entry_evidence_model', "
                "'accepted', 'public_registry', 'moka', 0, :at)"
            ),
            {
                "id": observation_id,
                "version": manifest_version,
                "round_id": round_id,
                "company_id": company_id,
                "at": "2026-08-10 00:02:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO entry_evidence_audit_samples "
                "(id, discovery_round_id, observation_id, source_id, platform, selected_at) "
                "VALUES (:id, :round_id, :observation_id, 'public_registry', 'moka', :at)"
            ),
            {
                "id": sample_id,
                "round_id": round_id,
                "observation_id": observation_id,
                "at": "2026-08-10 00:03:00+00:00",
            },
        )
        connection.execute(
            text(
                "INSERT INTO entry_evidence_audit_findings "
                "(id, audit_sample_id, severe_error, reason, audited_at) "
                "VALUES (:id, :sample_id, 1, 'Wrong entity', :at)"
            ),
            {
                "id": finding_id,
                "sample_id": sample_id,
                "at": "2026-08-10 00:04:00+00:00",
            },
        )

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert "entry_evidence_quarantines" in inspector.get_table_names()
    assert {
        "membership_fingerprint",
        "intended_member_count",
    } <= {column["name"] for column in inspector.get_columns("entry_discovery_rounds")}
    assert {
        "public_evidence",
        "model_assessment",
        "independent_validation",
        "prompt_fingerprint",
        "schema_fingerprint",
        "policy_fingerprint",
        "registry_fingerprint",
    } <= {column["name"] for column in inspector.get_columns("entry_discovery_observations")}

    with engine.begin() as connection:
        quarantine = connection.execute(
            text("SELECT observation_id, audit_finding_id FROM entry_evidence_quarantines")
        ).one()
        assert quarantine == (observation_id, finding_id)
        mutations = (
            ("UPDATE entry_discovery_rounds SET name='changed' WHERE id=:id", round_id),
            (
                "UPDATE entry_discovery_observations SET method='changed' WHERE id=:id",
                observation_id,
            ),
            ("DELETE FROM entry_evidence_audit_samples WHERE id=:id", sample_id),
            ("DELETE FROM entry_evidence_audit_findings WHERE id=:id", finding_id),
            (
                "DELETE FROM entry_evidence_quarantines WHERE observation_id=:id",
                observation_id,
            ),
            ("DELETE FROM company_manifests WHERE version=:id", manifest_version),
        )
        for statement, identifier in mutations:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text(statement), {"id": identifier})
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_integrity_migration_emits_postgresql_restrict_and_trigger_ddl() -> None:
    migration_path = (
        Path(__file__).parents[2] / "alembic" / "versions" / "0011_entry_evidence_integrity.py"
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
    assert migration["revision"] == "0011_entry_evidence_integrity"
    assert migration["down_revision"] == "0010_entry_evidence_rounds"
    assert "ON DELETE RESTRICT" in sql
    assert "CREATE FUNCTION prevent_entry_evidence_mutation()" in sql
    assert "CREATE TRIGGER trg_round_observations_immutable" in sql
    assert "CREATE TABLE entry_evidence_quarantines" in sql

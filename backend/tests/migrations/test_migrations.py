import os
import re
import runpy
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError

from alembic import command

EXPECTED_TABLES = {
    "candidate_facts",
    "candidate_reviews",
    "companies",
    "company_aliases",
    "company_manifest_members",
    "company_manifests",
    "company_identity_review_decisions",
    "company_identity_review_items",
    "source_documents",
    "company_sources",
    "entry_discovery_observations",
    "job_postings",
    "job_sources",
    "regulatory_filings",
    "collection_requests",
    "crawl_runs",
    "job_entries",
    "job_collection_snapshots",
}

REVIEW_TABLES = {
    "company_identity_review_decisions",
    "company_identity_review_items",
}


def test_initial_migration_widens_postgresql_alembic_revision_column() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0001_initial_schema.py"
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
    assert (
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"
        in sql
    )


def test_claim_token_backfill_compiles_uuid_cast_for_postgresql() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0004_crawl_run_claim_token.py"
    )
    migration = runpy.run_path(str(migration_path))

    compiled = str(
        migration["BACKFILL_RUNNING_CLAIMS"].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "SET claim_token=CAST(crawl_runs.id AS VARCHAR(36))" in compiled


def test_initial_migration_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES | {"alembic_version"}
    assert "claim_token" in {
        column["name"] for column in inspector.get_columns("crawl_runs")
    }
    assert {column["name"] for column in inspector.get_columns("job_entries")} >= {
        "id",
        "company_id",
        "url",
        "normalized_url",
        "provider",
        "platform",
        "requires_rendering",
        "status",
        "failure_count",
        "last_checked_at",
        "last_success_at",
        "created_at",
        "updated_at",
    }
    assert {
        column["name"] for column in inspector.get_columns("job_collection_snapshots")
    } >= {
        "id",
        "job_entry_id",
        "crawl_run_id",
        "status",
        "lifecycle_applied",
        "pagination_complete",
        "empty_confirmed",
        "reported_total",
        "observed_count",
        "pages_fetched",
        "content_fingerprint",
        "command_hash",
        "error_code",
        "started_at",
        "completed_at",
        "created_at",
    }
    assert {column["name"] for column in inspector.get_columns("job_sources")} >= {
        "job_entry_id",
        "last_seen_snapshot_id",
        "missing_complete_snapshots",
        "lifecycle_managed",
    }
    assert {index["name"] for index in inspector.get_indexes("companies")} >= {
        "ix_companies_normalized_name",
        "ix_companies_industry",
        "ix_companies_sub_industry",
        "ix_companies_funding_stage",
        "ix_companies_scale",
        "ix_companies_city",
    }
    assert {index["name"] for index in inspector.get_indexes("company_aliases")} == {
        "ix_company_aliases_normalized_alias"
    }
    assert {index["name"] for index in inspector.get_indexes("job_postings")} == {
        "ix_job_postings_company_active"
    }
    assert {index["name"] for index in inspector.get_indexes("job_sources")} >= {
        "ix_job_sources_entry_active",
        "ix_job_sources_posting_active",
    }
    assert {index["name"] for index in inspector.get_indexes("collection_requests")} == {
        "ix_collection_requests_status_query",
        "uq_collection_requests_active_query",
    }
    assert {index["name"] for index in inspector.get_indexes("source_documents")} == {
        "uq_source_document_provider_url_hash_without_external_id"
    }
    with engine.connect() as connection:
        index_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'uq_collection_requests_active_query'"
            )
        )
    assert index_sql is not None
    assert "WHERE status IN ('queued', 'running')" in index_sql
    with engine.connect() as connection:
        source_index_sql = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = 'uq_source_document_provider_url_hash_without_external_id'"
            )
        )
    assert source_index_sql is not None
    assert "WHERE external_id IS NULL" in source_index_sql

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO crawl_runs (
                    id, run_type, status, providers_attempted, created_at
                ) VALUES (
                    :id, :run_type, :status, :providers_attempted, :created_at
                )
                """
            ),
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "run_type": "discovery",
                "status": "queued",
                "providers_attempted": "[]",
                "created_at": "2026-07-31 00:00:00+00:00",
            },
        )
        counters = connection.execute(
            text(
                "SELECT documents_found, jobs_found, jobs_written "
                "FROM crawl_runs WHERE id = :id"
            ),
            {"id": "00000000-0000-0000-0000-000000000001"},
        ).one()
    assert counters == (0, 0, 0)

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}

    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {"alembic_version"}


def test_claim_token_migration_backfills_existing_running_runs(tmp_path: Path) -> None:
    database_path = tmp_path / "claim-token-migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    run_id = "00000000-0000-0000-0000-000000000042"
    command.upgrade(config, "0003_source_document_null_external_identity")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO crawl_runs "
                "(id, run_type, status, providers_attempted, created_at) "
                "VALUES (:id, 'discovery', 'running', '[]', :created_at)"
            ),
            {"id": run_id, "created_at": "2026-08-04 00:00:00+00:00"},
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT claim_token FROM crawl_runs WHERE id = :id"),
            {"id": run_id},
        ) == run_id


def test_job_type_extension_migration_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "job-type-migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    company_id = str(uuid4())
    command.upgrade(config, "0004_crawl_run_claim_token")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, canonical_name, normalized_name, funding_stage, scale, "
                "created_at, updated_at) "
                "VALUES (:id, 'Example', 'example', 'unknown', 'unknown', "
                ":created_at, :created_at)"
            ),
            {"id": company_id, "created_at": "2026-08-04 00:00:00+00:00"},
        )

    command.upgrade(config, "head")

    with engine.begin() as connection:
        for job_type in ("part_time", "temporary"):
            connection.execute(
                text(
                    "INSERT INTO job_postings "
                    "(id, company_id, title, normalized_title, job_type, city, "
                    "description, is_active, created_at, updated_at) "
                    "VALUES (:id, :company_id, :title, :title, :job_type, '', '', 1, "
                    ":created_at, :created_at)"
                ),
                {
                    "id": str(uuid4()),
                    "company_id": company_id,
                    "title": f"{job_type}engineer",
                    "job_type": job_type,
                    "created_at": "2026-08-04 00:00:00+00:00",
                },
            )
        assert set(
            connection.scalars(text("SELECT job_type FROM job_postings"))
        ) == {"part_time", "temporary"}

    command.downgrade(config, "0004_crawl_run_claim_token")
    with engine.connect() as connection:
        assert set(
            connection.scalars(text("SELECT job_type FROM job_postings"))
        ) == {"unknown"}
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO job_postings "
                "(id, company_id, title, normalized_title, job_type, city, "
                "description, is_active, created_at, updated_at) "
                "VALUES (:id, :company_id, 'parttime', 'parttime', 'part_time', "
                "'', '', 1, :created_at, :created_at)"
            ),
            {
                "id": str(uuid4()),
                "company_id": company_id,
                "created_at": "2026-08-04 00:00:00+00:00",
            },
        )

    command.upgrade(config, "head")


def test_job_type_extension_preserves_sqlite_foreign_key_dependents(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "job-type-dependent-migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0004_crawl_run_claim_token")
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: Any, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    company_id = str(uuid4())
    job_id = str(uuid4())
    source_id = str(uuid4())
    created_at = "2026-08-04 00:00:00+00:00"
    with engine.begin() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        dependent_tables = {
            table_name
            for table_name in inspect(connection).get_table_names()
            for foreign_key in inspect(connection).get_foreign_keys(table_name)
            if foreign_key["referred_table"] == "job_postings"
        }
        assert dependent_tables == {"job_sources"}
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, canonical_name, normalized_name, funding_stage, scale, "
                "created_at, updated_at) VALUES "
                "(:id, 'Example', 'example', 'unknown', 'unknown', "
                ":created_at, :created_at)"
            ),
            {"id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_postings "
                "(id, company_id, title, normalized_title, job_type, city, "
                "description, is_active, created_at, updated_at) VALUES "
                "(:id, :company_id, 'Engineer', 'engineer', 'full_time', '', '', 1, "
                ":created_at, :created_at)"
            ),
            {"id": job_id, "company_id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_sources "
                "(id, job_posting_id, provider, source_raw_id, apply_url, "
                "first_seen_at, last_seen_at, is_active) VALUES "
                "(:id, :job_id, 'official', 'job-1', 'https://example.com/job-1', "
                ":created_at, :created_at, 1)"
            ),
            {"id": source_id, "job_id": job_id, "created_at": created_at},
        )

    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0005_extend_job_type_values.py"
    )
    migration = runpy.run_path(str(migration_path))
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        migration["upgrade"].__globals__["op"] = Operations(context)

        migration["upgrade"]()

        assert connection.scalar(text("SELECT count(*) FROM job_postings")) == 1
        assert connection.scalar(text("SELECT count(*) FROM job_sources")) == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        connection.execute(
            text("UPDATE job_postings SET job_type = 'part_time' WHERE id = :id"),
            {"id": job_id},
        )

        migration["downgrade"]()

        assert connection.scalar(text("SELECT job_type FROM job_postings")) == "unknown"
        assert connection.scalar(text("SELECT count(*) FROM job_sources")) == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_job_type_extension_emits_postgresql_constraint_ddl() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0005_extend_job_type_values.py"
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
    assert "ALTER TABLE job_postings DROP CONSTRAINT job_type" in sql
    assert "ALTER TABLE job_postings ADD CONSTRAINT job_type CHECK" in sql
    assert "'part_time'" in sql
    assert "'temporary'" in sql


def test_job_type_extension_emits_sqlite_offline_ddl() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0005_extend_job_type_values.py"
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
    assert "CREATE TABLE _alembic_tmp_job_postings" in sql
    assert "'part_time'" in sql


def test_job_entries_migration_round_trip_preserves_legacy_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "job-entry-migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    company_id = str(uuid4())
    crawl_run_id = str(uuid4())
    entry_id = str(uuid4())
    created_at = "2026-08-05 00:00:00+00:00"

    command.upgrade(config, "0005_extend_job_type_values")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, canonical_name, normalized_name, funding_stage, scale, "
                "created_at, updated_at) VALUES "
                "(:id, 'Example', 'example', 'unknown', 'unknown', :created_at, :created_at)"
            ),
            {"id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO crawl_runs "
                "(id, company_id, run_type, status, providers_attempted, created_at) "
                "VALUES (:id, :company_id, 'company_refresh', 'queued', '[]', :created_at)"
            ),
            {"id": crawl_run_id, "company_id": company_id, "created_at": created_at},
        )

    command.upgrade(config, "0006_job_entries_and_snapshots")
    engine.dispose()
    with engine.connect() as connection:
        entry_foreign_keys = {
            row[2]: row[6]
            for row in connection.execute(text("PRAGMA foreign_key_list(job_entries)"))
        }
        snapshot_foreign_keys = {
            row[2]: row[6]
            for row in connection.execute(
                text("PRAGMA foreign_key_list(job_collection_snapshots)")
            )
        }
    assert entry_foreign_keys == {"companies": "CASCADE"}
    assert snapshot_foreign_keys == {"crawl_runs": "SET NULL", "job_entries": "CASCADE"}
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO job_entries "
                "(id, company_id, url, normalized_url, provider, platform, requires_rendering, "
                "status, failure_count, created_at, updated_at) VALUES "
                "(:id, :company_id, 'https://example.com/jobs', 'https://example.com/jobs', "
                "'official', 'custom', 0, 'unknown', 0, :created_at, :created_at)"
            ),
            {"id": entry_id, "company_id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_collection_snapshots "
                "(id, job_entry_id, crawl_run_id, status, pagination_complete, empty_confirmed, "
                "observed_count, pages_fetched, command_hash, started_at, completed_at, created_at) "
                "VALUES (:id, :entry_id, :crawl_run_id, 'succeeded', 1, 1, 0, 0, :command_hash, "
                ":created_at, :created_at, :created_at)"
            ),
            {
                "id": str(uuid4()),
                "entry_id": entry_id,
                "crawl_run_id": crawl_run_id,
                "command_hash": "a" * 64,
                "created_at": created_at,
            },
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO job_entries "
                    "(id, company_id, url, normalized_url, provider, platform, requires_rendering, "
                    "status, failure_count, created_at, updated_at) VALUES "
                    "(:id, :company_id, 'https://other.example/jobs', 'https://example.com/jobs', "
                    "'official', 'custom', 0, 'unknown', 0, :created_at, :created_at)"
                ),
                {"id": str(uuid4()), "company_id": company_id, "created_at": created_at},
            )

    command.downgrade(config, "0005_extend_job_type_values")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM companies")) == 1
        assert connection.scalar(text("SELECT count(*) FROM crawl_runs")) == 1
        assert set(inspect(connection).get_table_names()).isdisjoint(
            {"job_entries", "job_collection_snapshots"}
        )


def test_job_source_snapshot_lifecycle_round_trip_preserves_legacy_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "job-source-snapshot-lifecycle.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    company_id = str(uuid4())
    job_id = str(uuid4())
    source_id = str(uuid4())
    entry_id = str(uuid4())
    snapshot_id = str(uuid4())
    created_at = "2026-08-05 00:00:00+00:00"

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: Any, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    command.upgrade(config, "0006_job_entries_and_snapshots")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, canonical_name, normalized_name, funding_stage, scale, "
                "created_at, updated_at) VALUES "
                "(:id, 'Example', 'example', 'unknown', 'unknown', :created_at, :created_at)"
            ),
            {"id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_postings "
                "(id, company_id, title, normalized_title, job_type, city, "
                "description, is_active, created_at, updated_at) VALUES "
                "(:id, :company_id, 'Engineer', 'engineer', 'full_time', '', '', 1, "
                ":created_at, :created_at)"
            ),
            {"id": job_id, "company_id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_sources "
                "(id, job_posting_id, provider, source_raw_id, apply_url, "
                "first_seen_at, last_seen_at, is_active) VALUES "
                "(:id, :job_id, 'official', 'job-1', 'https://example.com/job-1', "
                ":created_at, :created_at, 1)"
            ),
            {"id": source_id, "job_id": job_id, "created_at": created_at},
        )

    command.upgrade(config, "0007_job_source_snapshot_lifecycle")
    assert {index["name"] for index in inspect(engine).get_indexes("job_sources")} >= {
        "ix_job_sources_entry_active",
        "ix_job_sources_posting_active",
    }
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT job_entry_id, last_seen_snapshot_id, missing_complete_snapshots, "
                "lifecycle_managed "
                "FROM job_sources WHERE id = :id"
            ),
            {"id": source_id},
        ).one() == (None, None, 0, 0)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        connection.execute(
            text(
                "INSERT INTO job_entries "
                "(id, company_id, url, normalized_url, provider, platform, requires_rendering, "
                "status, failure_count, created_at, updated_at) VALUES "
                "(:id, :company_id, 'https://example.com/jobs', 'https://example.com/jobs', "
                "'official', 'custom', 0, 'unknown', 0, :created_at, :created_at)"
            ),
            {"id": entry_id, "company_id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_collection_snapshots "
                "(id, job_entry_id, status, pagination_complete, empty_confirmed, "
                "observed_count, pages_fetched, command_hash, started_at, completed_at, created_at) "
                "VALUES (:id, :entry_id, 'succeeded', 1, 1, 0, 0, :command_hash, "
                ":created_at, :created_at, :created_at)"
            ),
            {
                "id": snapshot_id,
                "entry_id": entry_id,
                "command_hash": "b" * 64,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "UPDATE job_sources SET job_entry_id = :entry_id, "
                "last_seen_snapshot_id = :snapshot_id, missing_complete_snapshots = 2, "
                "lifecycle_managed = 1 "
                "WHERE id = :source_id"
            ),
            {"entry_id": entry_id, "snapshot_id": snapshot_id, "source_id": source_id},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text("UPDATE job_sources SET job_entry_id = :id WHERE id = :source_id"),
                {"id": str(uuid4()), "source_id": source_id},
            )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "UPDATE job_sources SET last_seen_snapshot_id = :id "
                    "WHERE id = :source_id"
                ),
                {"id": str(uuid4()), "source_id": source_id},
            )

    command.downgrade(config, "0006_job_entries_and_snapshots")
    assert {
        index["name"] for index in inspect(engine).get_indexes("job_sources")
    }.isdisjoint({"ix_job_sources_entry_active", "ix_job_sources_posting_active"})
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, job_posting_id, source_document_id, provider, source_raw_id, "
                "apply_url, first_seen_at, last_seen_at, is_active FROM job_sources WHERE id = :id"
            ),
            {"id": source_id},
        ).one()
        assert row[:6] == (
            source_id,
            job_id,
            None,
            "official",
            "job-1",
            "https://example.com/job-1",
        )
        assert row[8] == 1
        assert row[6:8] == (created_at, created_at)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def _expect_integrity_error(
    connection: Any, statement: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(text(statement), parameters)


def test_gate1_manifest_discovery_round_trip_preserves_stage3a_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "gate1-manifest-discovery.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    company_id = str(uuid4())
    other_company_id = str(uuid4())
    job_id = str(uuid4())
    source_id = str(uuid4())
    entry_id = str(uuid4())
    snapshot_id = str(uuid4())
    candidate_id = str(uuid4())
    manifest_version = "a" * 64
    created_at = "2026-08-06 00:00:00+00:00"

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: Any, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    command.upgrade(config, "0007_job_source_snapshot_lifecycle")
    with engine.begin() as connection:
        for row_id, canonical_name, normalized_name in (
            (company_id, "Example", "example"),
            (other_company_id, "Other", "other"),
        ):
            connection.execute(
                text(
                    "INSERT INTO companies "
                    "(id, canonical_name, normalized_name, funding_stage, scale, "
                    "created_at, updated_at) VALUES "
                    "(:id, :canonical_name, :normalized_name, 'unknown', 'unknown', "
                    ":created_at, :created_at)"
                ),
                {
                    "id": row_id,
                    "canonical_name": canonical_name,
                    "normalized_name": normalized_name,
                    "created_at": created_at,
                },
            )
        connection.execute(
            text(
                "INSERT INTO job_postings "
                "(id, company_id, title, normalized_title, job_type, city, "
                "description, is_active, created_at, updated_at) VALUES "
                "(:id, :company_id, 'Engineer', 'engineer', 'full_time', '', '', 1, "
                ":created_at, :created_at)"
            ),
            {"id": job_id, "company_id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_entries "
                "(id, company_id, url, normalized_url, provider, platform, "
                "requires_rendering, status, failure_count, created_at, updated_at) "
                "VALUES (:id, :company_id, 'https://example.com/jobs', "
                "'https://example.com/jobs', 'official', 'custom', 0, 'unknown', 0, "
                ":created_at, :created_at)"
            ),
            {"id": entry_id, "company_id": company_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO job_collection_snapshots "
                "(id, job_entry_id, status, pagination_complete, empty_confirmed, "
                "observed_count, pages_fetched, command_hash, started_at, completed_at, "
                "created_at) VALUES (:id, :entry_id, 'succeeded', 1, 1, 0, 0, "
                ":command_hash, :created_at, :created_at, :created_at)"
            ),
            {
                "id": snapshot_id,
                "entry_id": entry_id,
                "command_hash": "b" * 64,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO job_sources "
                "(id, job_posting_id, job_entry_id, last_seen_snapshot_id, "
                "missing_complete_snapshots, lifecycle_managed, provider, "
                "source_raw_id, apply_url, first_seen_at, last_seen_at, is_active) "
                "VALUES (:id, :job_id, :entry_id, :snapshot_id, 0, 1, 'official', "
                "'job-1', 'https://example.com/job-1', :created_at, :created_at, 1)"
            ),
            {
                "id": source_id,
                "job_id": job_id,
                "entry_id": entry_id,
                "snapshot_id": snapshot_id,
                "created_at": created_at,
            },
        )

    command.upgrade(config, "0008_gate1_manifest_discovery")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= EXPECTED_TABLES - REVIEW_TABLES
    assert {
        index["name"]
        for index in inspector.get_indexes("job_entries")
        if index["unique"]
    } >= {"uq_job_entries_id_company"}
    assert {constraint["name"] for constraint in inspector.get_unique_constraints("candidate_facts")} >= {
        "uq_candidate_fact_evidence"
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("company_manifest_members")
    } >= {"uq_manifest_member_position", "uq_manifest_member_company"}

    with engine.begin() as connection:
        candidate_insert = (
            "INSERT INTO candidate_facts "
            "(id, stable_evidence_id, canonical_name, normalized_name, aliases, "
            "primary_category, source_id, source_url, retrieved_at, evidence_summary, "
            "confidence_tier, confidence_reason, decision_status, company_id, "
            "created_at, updated_at) VALUES "
            "(:id, :evidence_id, 'Example', 'example', '[\"Example\"]', "
            "'foundation_models', 'source_a', 'https://source.example/item', "
            ":created_at, 'Public evidence', 'high', 'Primary source', "
            "'review_required', :company_id, :created_at, :created_at)"
        )
        connection.execute(
            text(candidate_insert),
            {
                "id": candidate_id,
                "evidence_id": "c" * 64,
                "company_id": company_id,
                "created_at": created_at,
            },
        )
        _expect_integrity_error(
            connection,
            candidate_insert,
            {
                "id": str(uuid4()),
                "evidence_id": "c" * 64,
                "company_id": company_id,
                "created_at": created_at,
            },
        )
        _expect_integrity_error(
            connection,
            candidate_insert,
            {
                "id": str(uuid4()),
                "evidence_id": "d" * 64,
                "company_id": str(uuid4()),
                "created_at": created_at,
            },
        )

        review_insert = (
            "INSERT INTO candidate_reviews "
            "(id, candidate_fact_id, prior_status, action, resulting_status, "
            "resulting_company_id, reason, decided_at) VALUES "
            "(:id, :candidate_id, 'review_required', 'accept', 'accepted', "
            ":company_id, 'Evidence reviewed', :created_at)"
        )
        connection.execute(
            text(review_insert),
            {
                "id": str(uuid4()),
                "candidate_id": candidate_id,
                "company_id": company_id,
                "created_at": created_at,
            },
        )
        _expect_integrity_error(
            connection,
            review_insert,
            {
                "id": str(uuid4()),
                "candidate_id": str(uuid4()),
                "company_id": company_id,
                "created_at": created_at,
            },
        )
        _expect_integrity_error(
            connection,
            review_insert,
            {
                "id": str(uuid4()),
                "candidate_id": candidate_id,
                "company_id": str(uuid4()),
                "created_at": created_at,
            },
        )

        connection.execute(
            text(
                "INSERT INTO company_manifests "
                "(version, config_fingerprint, member_count, canonical_quota, frozen_at) "
                "VALUES (:version, :fingerprint, 2, '{}', :created_at)"
            ),
            {
                "version": manifest_version,
                "fingerprint": "e" * 64,
                "created_at": created_at,
            },
        )
        member_insert = (
            "INSERT INTO company_manifest_members "
            "(id, manifest_version, company_id, position, canonical_name, "
            "primary_category) VALUES "
            "(:id, :version, :company_id, :position, :canonical_name, "
            "'foundation_models')"
        )
        connection.execute(
            text(member_insert),
            {
                "id": str(uuid4()),
                "version": manifest_version,
                "company_id": company_id,
                "position": 1,
                "canonical_name": "Example",
            },
        )
        for parameters in (
            {
                "id": str(uuid4()),
                "version": manifest_version,
                "company_id": other_company_id,
                "position": 1,
                "canonical_name": "Other",
            },
            {
                "id": str(uuid4()),
                "version": manifest_version,
                "company_id": company_id,
                "position": 2,
                "canonical_name": "Example",
            },
            {
                "id": str(uuid4()),
                "version": "f" * 64,
                "company_id": other_company_id,
                "position": 2,
                "canonical_name": "Other",
            },
            {
                "id": str(uuid4()),
                "version": manifest_version,
                "company_id": str(uuid4()),
                "position": 2,
                "canonical_name": "Missing",
            },
        ):
            _expect_integrity_error(connection, member_insert, parameters)

        observation_insert = (
            "INSERT INTO entry_discovery_observations "
            "(id, manifest_version, company_id, method, status, requires_rendering, "
            "job_entry_id, observed_at) VALUES "
            "(:id, :version, :company_id, 'official_site', 'accepted', 0, "
            ":entry_id, :created_at)"
        )
        connection.execute(
            text(observation_insert),
            {
                "id": str(uuid4()),
                "version": manifest_version,
                "company_id": company_id,
                "entry_id": entry_id,
                "created_at": created_at,
            },
        )
        for parameters in (
            {
                "id": str(uuid4()),
                "version": "f" * 64,
                "company_id": company_id,
                "entry_id": None,
                "created_at": created_at,
            },
            {
                "id": str(uuid4()),
                "version": manifest_version,
                "company_id": str(uuid4()),
                "entry_id": None,
                "created_at": created_at,
            },
            {
                "id": str(uuid4()),
                "version": manifest_version,
                "company_id": other_company_id,
                "entry_id": entry_id,
                "created_at": created_at,
            },
        ):
            _expect_integrity_error(connection, observation_insert, parameters)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= EXPECTED_TABLES
    assert {
        index["name"] for index in inspector.get_indexes("companies")
    }.isdisjoint({"ix_companies_normalized_name_trgm"})
    assert {
        index["name"] for index in inspector.get_indexes("company_aliases")
    }.isdisjoint({"ix_company_aliases_normalized_alias_trgm"})
    with engine.connect() as connection:
        for table_name in (
            "companies",
            "job_entries",
            "job_collection_snapshots",
            "job_sources",
            "candidate_facts",
            "candidate_reviews",
            "company_manifests",
            "company_manifest_members",
            "entry_discovery_observations",
        ):
            assert connection.scalar(text(f"SELECT count(*) FROM {table_name}")) >= 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []

    command.downgrade(config, "0008_gate1_manifest_discovery")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()).isdisjoint(REVIEW_TABLES)
    with engine.connect() as connection:
        for table_name in (
            "companies",
            "job_entries",
            "job_collection_snapshots",
            "job_sources",
            "candidate_facts",
            "candidate_reviews",
            "company_manifests",
            "company_manifest_members",
            "entry_discovery_observations",
        ):
            assert connection.scalar(text(f"SELECT count(*) FROM {table_name}")) >= 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []

    command.downgrade(config, "0007_job_source_snapshot_lifecycle")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()).isdisjoint(
        {
            "candidate_facts",
            "candidate_reviews",
            "company_manifests",
            "company_manifest_members",
            "entry_discovery_observations",
        }
    )
    assert {
        index["name"] for index in inspector.get_indexes("job_entries")
    }.isdisjoint({"uq_job_entries_id_company"})
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM companies WHERE id IN (:first, :second)"),
            {"first": company_id, "second": other_company_id},
        ) == 2
        assert connection.scalar(
            text("SELECT count(*) FROM job_entries WHERE id = :id"), {"id": entry_id}
        ) == 1
        assert connection.scalar(
            text("SELECT count(*) FROM job_collection_snapshots WHERE id = :id"),
            {"id": snapshot_id},
        ) == 1
        assert connection.execute(
            text(
                "SELECT job_entry_id, last_seen_snapshot_id FROM job_sources "
                "WHERE id = :id"
            ),
            {"id": source_id},
        ).one() == (entry_id, snapshot_id)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_gate1_manifest_discovery_emits_named_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0008_gate1_manifest_discovery.py"
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
    assert "CONSTRAINT uq_job_entries_id_company UNIQUE (id, company_id)" in sql
    for constraint_name in (
        "fk_candidate_facts_company_id",
        "fk_candidate_reviews_candidate_fact_id",
        "fk_candidate_reviews_resulting_company_id",
        "fk_manifest_members_manifest_version",
        "fk_manifest_members_company_id",
        "fk_discovery_observations_manifest_version",
        "fk_discovery_observations_company_id",
        "fk_discovery_observation_entry_company",
        "uq_candidate_fact_evidence",
        "uq_manifest_member_position",
        "uq_manifest_member_company",
    ):
        assert f"CONSTRAINT {constraint_name}" in sql
    for index_name in (
        "ix_candidate_facts_decision_category",
        "ix_candidate_reviews_candidate_decided",
        "ix_manifest_members_company",
        "ix_discovery_observations_manifest_status",
        "ix_discovery_observations_company_observed",
    ):
        assert f"CREATE INDEX {index_name}" in sql


def test_0009_postgresql_sql_contains_bounded_similarity_indexes() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0009_company_identity_review.py"
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
    assert migration["revision"] == "0009_company_identity_review"
    assert migration["down_revision"] == "0008_gate1_manifest_discovery"
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in sql
    assert (
        "CREATE INDEX ix_companies_normalized_name_trgm ON companies "
        "USING gist (normalized_name gist_trgm_ops)" in sql
    )
    assert (
        "CREATE INDEX ix_company_aliases_normalized_alias_trgm ON company_aliases "
        "USING gist (normalized_alias gist_trgm_ops)" in sql
    )
    assert "ALTER TABLE companies ADD COLUMN normalized_website VARCHAR(1000)" in sql
    assert (
        "ALTER TABLE regulatory_filings ADD COLUMN "
        "normalized_filing_number VARCHAR(255)" in sql
    )
    assert (
        "CREATE INDEX ix_companies_normalized_website "
        "ON companies (normalized_website)" in sql
    )
    assert (
        "CREATE INDEX ix_regulatory_filings_normalized_filing_number "
        "ON regulatory_filings (normalized_filing_number)" in sql
    )
    for constraint_name in (
        "identity_review_status",
        "identity_review_action",
        "ck_identity_review_item_hash_format",
        "ck_identity_review_item_status_resolution",
        "ck_identity_review_decision_hash_format",
        "ck_identity_review_decision_reason_length",
        "ck_identity_review_decision_action_target",
        "uq_identity_review_item_stable_hash",
        "uq_identity_review_decision_hash",
        "uq_identity_review_decision_item",
        "fk_company_identity_review_items_first_crawl_run_id",
        "fk_company_identity_review_decisions_review_item_id",
        "fk_company_identity_review_decisions_target_company_id",
        "fk_company_identity_review_decisions_resulting_company_id",
    ):
        assert f"CONSTRAINT {constraint_name}" in sql
    for column_name in (
        "aliases",
        "legal_identifiers",
        "public_evidence_refs",
        "candidate_matches",
        "review_reasons",
    ):
        assert f"{column_name} JSON NOT NULL" in sql
    assert (
        "CREATE INDEX ix_company_identity_review_items_status_created "
        "ON company_identity_review_items (status, created_at)" in sql
    )
    assert sql.count("ON DELETE RESTRICT") == 4


def test_0009_postgresql_downgrade_owns_only_review_objects() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0009_company_identity_review.py"
    )
    migration = runpy.run_path(str(migration_path))
    output = StringIO()
    context = MigrationContext.configure(
        dialect=postgresql.dialect(),
        opts={"as_sql": True, "output_buffer": output},
    )
    migration["downgrade"].__globals__["op"] = Operations(context)

    migration["downgrade"]()

    sql = " ".join(output.getvalue().split())
    assert "DROP INDEX IF EXISTS ix_companies_normalized_name_trgm" in sql
    assert "DROP INDEX IF EXISTS ix_company_aliases_normalized_alias_trgm" in sql
    assert "DROP TABLE company_identity_review_decisions" in sql
    assert "DROP TABLE company_identity_review_items" in sql
    assert "DROP INDEX ix_companies_normalized_website" in sql
    assert "DROP INDEX ix_regulatory_filings_normalized_filing_number" in sql
    assert "ALTER TABLE companies DROP COLUMN normalized_website" in sql
    assert (
        "ALTER TABLE regulatory_filings DROP COLUMN normalized_filing_number" in sql
    )
    assert "DROP EXTENSION" not in sql
    assert "CASCADE" not in sql
    for shared_object in (
        "DROP TABLE companies",
        "DROP TABLE company_aliases",
        "DROP TABLE crawl_runs",
        "DROP INDEX ix_companies_normalized_name",
        "DROP INDEX ix_company_aliases_normalized_alias",
    ):
        assert shared_object not in sql


def test_0009_sqlite_sql_skips_postgresql_similarity_objects() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0009_company_identity_review.py"
    )
    migration = runpy.run_path(str(migration_path))
    output = StringIO()
    context = MigrationContext.configure(
        dialect=sqlite.dialect(),
        opts={"as_sql": True, "output_buffer": output},
    )
    operations = Operations(context)
    migration["upgrade"].__globals__["op"] = operations

    migration["upgrade"]()

    sql = " ".join(output.getvalue().split())
    assert "CREATE TABLE company_identity_review_items" in sql
    assert "CREATE TABLE company_identity_review_decisions" in sql
    assert "pg_trgm" not in sql
    assert "gist_trgm_ops" not in sql
    assert "ix_companies_normalized_name_trgm" not in sql
    assert "ix_company_aliases_normalized_alias_trgm" not in sql
    assert "ALTER TABLE companies ADD COLUMN normalized_website VARCHAR(1000)" in sql
    assert (
        "ALTER TABLE regulatory_filings ADD COLUMN "
        "normalized_filing_number VARCHAR(255)" in sql
    )


def test_0009_backfills_normalized_evidence_for_legacy_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "identity-evidence-backfill.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    company_id = str(uuid4())
    filing_id = str(uuid4())
    created_at = "2026-08-07 00:00:00+00:00"

    command.upgrade(config, "0008_gate1_manifest_discovery")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, canonical_name, normalized_name, funding_stage, scale, website, "
                "created_at, updated_at) VALUES "
                "(:id, 'Legacy Company', 'legacycompany', 'unknown', 'unknown', "
                ":website, :created_at, :created_at)"
            ),
            {
                "id": company_id,
                "website": "HTTPS://Legacy.Example/path?campaign=old#fragment",
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO regulatory_filings "
                "(id, company_id, filing_type, filing_number, filing_name, "
                "created_at, updated_at) VALUES "
                "(:id, :company_id, 'business_license', :filing_number, "
                "'Legacy filing', :created_at, :created_at)"
            ),
            {
                "id": filing_id,
                "company_id": company_id,
                "filing_number": "  Ｋ\u3000Straße\t42 ",
                "created_at": created_at,
            },
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT normalized_website FROM companies WHERE id = :id"),
            {"id": company_id},
        ) == "https://legacy.example/path"
        assert connection.scalar(
            text(
                "SELECT normalized_filing_number FROM regulatory_filings "
                "WHERE id = :id"
            ),
            {"id": filing_id},
        ) == "kstrasse42"
        assert {index["name"] for index in inspect(connection).get_indexes("companies")} >= {
            "ix_companies_normalized_website"
        }
        assert {
            index["name"]
            for index in inspect(connection).get_indexes("regulatory_filings")
        } >= {"ix_regulatory_filings_normalized_filing_number"}

    command.downgrade(config, "0008_gate1_manifest_discovery")
    inspector = inspect(engine)
    assert "normalized_website" not in {
        column["name"] for column in inspector.get_columns("companies")
    }
    assert "normalized_filing_number" not in {
        column["name"] for column in inspector.get_columns("regulatory_filings")
    }


def test_gate1_manifest_discovery_default_sqlite_offline_upgrade_completes() -> None:
    output = StringIO()
    config = Config(Path(__file__).parents[2] / "alembic.ini", output_buffer=output)
    config.set_main_option("sqlalchemy.url", "sqlite://")

    command.upgrade(config, "head", sql=True)

    sql = " ".join(output.getvalue().split())
    assert "CREATE UNIQUE INDEX uq_job_entries_id_company ON job_entries (id, company_id)" in sql
    for table_name in (
        "candidate_facts",
        "candidate_reviews",
        "company_manifests",
        "company_manifest_members",
        "entry_discovery_observations",
    ):
        assert f"CREATE TABLE {table_name}" in sql
    assert "CONSTRAINT fk_discovery_observation_entry_company" in sql
    assert "FOREIGN KEY(job_entry_id, company_id)" in sql
    assert "REFERENCES job_entries (id, company_id) ON DELETE RESTRICT" in sql


def test_gate1_manifest_discovery_default_sqlite_offline_downgrade_completes() -> None:
    output = StringIO()
    config = Config(Path(__file__).parents[2] / "alembic.ini", output_buffer=output)
    config.set_main_option("sqlalchemy.url", "sqlite://")

    command.downgrade(
        config,
        "0008_gate1_manifest_discovery:0007_job_source_snapshot_lifecycle",
        sql=True,
    )

    sql = " ".join(output.getvalue().split())
    for table_name in (
        "entry_discovery_observations",
        "company_manifest_members",
        "company_manifests",
        "candidate_reviews",
        "candidate_facts",
    ):
        assert f"DROP TABLE {table_name}" in sql
    assert "DROP INDEX uq_job_entries_id_company" in sql


def test_job_source_snapshot_lifecycle_emits_named_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0007_job_source_snapshot_lifecycle.py"
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
    assert "lifecycle_managed BOOLEAN DEFAULT false NOT NULL" in sql
    assert "ADD CONSTRAINT fk_job_sources_job_entry_id FOREIGN KEY(job_entry_id)" in sql
    assert "REFERENCES job_entries (id) ON DELETE SET NULL" in sql
    assert (
        "ADD CONSTRAINT fk_job_sources_last_seen_snapshot_id "
        "FOREIGN KEY(last_seen_snapshot_id)" in sql
    )
    assert "REFERENCES job_collection_snapshots (id) ON DELETE SET NULL" in sql
    assert "CREATE INDEX ix_job_sources_entry_active ON job_sources" in sql
    assert (
        "CREATE INDEX ix_job_sources_posting_active ON job_sources "
        "(job_posting_id, is_active)" in sql
    )


def test_job_snapshot_applied_state_emits_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "0006_job_entries_and_snapshots.py"
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
    assert "lifecycle_applied BOOLEAN DEFAULT false NOT NULL" in sql


def test_job_source_snapshot_lifecycle_default_sqlite_offline_upgrade_completes() -> None:
    output = StringIO()
    config = Config(Path(__file__).parents[2] / "alembic.ini", output_buffer=output)
    config.set_main_option("sqlalchemy.url", "sqlite://")

    command.upgrade(config, "head", sql=True)

    sql = " ".join(output.getvalue().split())
    assert "lifecycle_applied BOOLEAN DEFAULT false NOT NULL" in sql
    assert "CREATE TABLE _alembic_tmp_job_sources" in sql
    assert "job_entry_id CHAR(36)" in sql
    assert "last_seen_snapshot_id CHAR(36)" in sql
    assert "missing_complete_snapshots INTEGER DEFAULT 0 NOT NULL" in sql
    assert "lifecycle_managed BOOLEAN DEFAULT false NOT NULL" in sql
    assert "FOREIGN KEY(job_entry_id) REFERENCES job_entries (id) ON DELETE SET NULL" in sql
    assert (
        "FOREIGN KEY(last_seen_snapshot_id) REFERENCES job_collection_snapshots (id) "
        "ON DELETE SET NULL" in sql
    )
    assert "CREATE INDEX ix_job_sources_entry_active ON job_sources" in sql
    assert (
        "CREATE INDEX ix_job_sources_posting_active ON job_sources "
        "(job_posting_id, is_active)" in sql
    )


def test_job_source_snapshot_lifecycle_default_sqlite_offline_downgrade_completes() -> None:
    output = StringIO()
    config = Config(Path(__file__).parents[2] / "alembic.ini", output_buffer=output)
    config.set_main_option("sqlalchemy.url", "sqlite://")

    command.downgrade(
        config,
        "0007_job_source_snapshot_lifecycle:0006_job_entries_and_snapshots",
        sql=True,
    )

    sql = " ".join(output.getvalue().split())
    assert "CREATE TABLE _alembic_tmp_job_sources" in sql
    assert "FOREIGN KEY(job_posting_id) REFERENCES job_postings (id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(source_document_id) REFERENCES source_documents (id) ON DELETE SET NULL" in sql
    assert "missing_complete_snapshots" not in sql
    assert "lifecycle_managed" not in sql
    assert "ix_job_sources_entry_active" not in sql
    assert "ix_job_sources_posting_active" not in sql


def _quoted_isolated_schema_name(schema_name: str) -> str:
    if not re.fullmatch(r"stage3a_test_[0-9a-f]{32}", schema_name):
        raise ValueError("invalid isolated PostgreSQL schema name")
    return f'"{schema_name}"'


def _isolated_postgresql_url(database_url: str, schema_name: str) -> URL:
    _quoted_isolated_schema_name(schema_name)
    return make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )


def _set_alembic_sqlalchemy_url(config: Config, database_url: URL) -> None:
    config.set_main_option(
        "sqlalchemy.url",
        database_url.render_as_string(hide_password=False).replace("%", "%%"),
    )


def _isolated_schema_cleanup_sql(schema_name: str) -> tuple[str, str]:
    quoted_schema_name = _quoted_isolated_schema_name(schema_name)
    return (
        f"DROP TABLE {quoted_schema_name}.\"alembic_version\"",
        f"DROP SCHEMA {quoted_schema_name}",
    )


def _isolated_revision_widen_sql(schema_name: str) -> str:
    quoted_schema_name = _quoted_isolated_schema_name(schema_name)
    return (
        f"ALTER TABLE {quoted_schema_name}.\"alembic_version\" "
        "ALTER COLUMN version_num TYPE VARCHAR(128)"
    )


def _cleanup_isolated_postgresql_schema(
    admin_engine: Any, config: Config, schema_name: str
) -> None:
    widen_revision_sql = _isolated_revision_widen_sql(schema_name)
    with admin_engine.begin() as connection:
        inspector = inspect(connection)
        if not inspector.has_schema(schema_name):
            return
        if inspector.has_table("alembic_version", schema=schema_name):
            connection.execute(text(widen_revision_sql))

    command.downgrade(config, "base")
    drop_version_sql, drop_schema_sql = _isolated_schema_cleanup_sql(schema_name)
    with admin_engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("alembic_version", schema=schema_name):
            connection.execute(text(drop_version_sql))
        assert inspect(connection).get_table_names(schema=schema_name) == []
        connection.execute(text(drop_schema_sql))


def test_isolated_postgresql_url_is_safe_for_alembic_config() -> None:
    schema_name = "stage3a_test_0123456789abcdef0123456789abcdef"
    schema_url = _isolated_postgresql_url("postgresql://localhost/test", schema_name)
    config = Config()

    _set_alembic_sqlalchemy_url(config, schema_url)

    assert config.get_main_option("sqlalchemy.url") == schema_url.render_as_string(
        hide_password=False
    )


def test_credentialed_postgresql_url_preserves_password_for_alembic_config() -> None:
    database_url = URL.create(
        "postgresql",
        username="reader",
        password="p@ss%word",
        host="localhost",
        database="coverage",
    )
    config = Config()

    _set_alembic_sqlalchemy_url(config, database_url)

    configured_url = config.get_main_option("sqlalchemy.url")
    assert configured_url == database_url.render_as_string(hide_password=False)
    assert "***" not in configured_url


def test_isolated_schema_cleanup_sql_is_validated_and_non_cascading() -> None:
    schema_name = "stage3a_test_0123456789abcdef0123456789abcdef"

    drop_version_sql, drop_schema_sql = _isolated_schema_cleanup_sql(schema_name)
    widen_revision_sql = _isolated_revision_widen_sql(schema_name)

    assert drop_version_sql == f'DROP TABLE "{schema_name}"."alembic_version"'
    assert drop_schema_sql == f'DROP SCHEMA "{schema_name}"'
    assert widen_revision_sql == (
        f'ALTER TABLE "{schema_name}"."alembic_version" '
        "ALTER COLUMN version_num TYPE VARCHAR(128)"
    )
    assert "CASCADE" not in (
        f"{widen_revision_sql} {drop_version_sql} {drop_schema_sql}"
    )
    with pytest.raises(ValueError, match="invalid isolated PostgreSQL schema name"):
        _isolated_schema_cleanup_sql("public")
    with pytest.raises(ValueError, match="invalid isolated PostgreSQL schema name"):
        _isolated_revision_widen_sql("public")


@pytest.mark.postgresql
def test_cleanup_widens_legacy_revision_column_before_downgrade() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"stage3a_test_{uuid4().hex}"
    quoted_schema_name = _quoted_isolated_schema_name(schema_name)
    schema_url = _isolated_postgresql_url(database_url, schema_name)
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    _set_alembic_sqlalchemy_url(config, schema_url)
    admin_engine = create_engine(database_url)
    schema_created = False
    primary_error: BaseException | None = None

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema_name}"))
        schema_created = True
        command.upgrade(config, "0005_extend_job_type_values")
        schema_engine = create_engine(schema_url)
        try:
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE alembic_version ALTER COLUMN version_num "
                        "TYPE VARCHAR(32)"
                    )
                )
        finally:
            schema_engine.dispose()

        _cleanup_isolated_postgresql_schema(admin_engine, config, schema_name)
        schema_created = False
        with admin_engine.connect() as connection:
            assert inspect(connection).has_schema(schema_name) is False
        _cleanup_isolated_postgresql_schema(admin_engine, config, schema_name)

        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema_name}"))
        schema_created = True
        _cleanup_isolated_postgresql_schema(admin_engine, config, schema_name)
        schema_created = False
        with admin_engine.connect() as connection:
            assert inspect(connection).has_schema(schema_name) is False
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if schema_created:
            try:
                with admin_engine.begin() as connection:
                    inspector = inspect(connection)
                    if inspector.has_table("alembic_version", schema=schema_name):
                        connection.execute(
                            text(_isolated_revision_widen_sql(schema_name))
                        )
                _cleanup_isolated_postgresql_schema(
                    admin_engine, config, schema_name
                )
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    "isolated PostgreSQL test rescue also failed: "
                    f"{cleanup_error!r}"
                )
        admin_engine.dispose()


@pytest.mark.postgresql
def test_job_entries_postgresql_schema_round_trip() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"stage3a_test_{uuid4().hex}"
    quoted_schema_name = _quoted_isolated_schema_name(schema_name)
    schema_url = _isolated_postgresql_url(database_url, schema_name)
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    _set_alembic_sqlalchemy_url(config, schema_url)
    admin_engine = create_engine(database_url)
    schema_created = False
    primary_error: BaseException | None = None

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema_name}"))
        schema_created = True

        command.upgrade(config, "0005_extend_job_type_values")
        legacy_schema_engine = create_engine(schema_url)
        try:
            with legacy_schema_engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE alembic_version ALTER COLUMN version_num "
                        "TYPE VARCHAR(32)"
                    )
                )
        finally:
            legacy_schema_engine.dispose()
        command.upgrade(config, "0007_job_source_snapshot_lifecycle")
        schema_engine = create_engine(schema_url)
        try:
            with schema_engine.begin() as connection:
                company_id = str(uuid4())
                entry_id = str(uuid4())
                snapshot_id = str(uuid4())
                job_id = str(uuid4())
                source_id = str(uuid4())
                created_at = "2026-08-05 00:00:00+00:00"
                connection.execute(
                    text(
                        "INSERT INTO companies "
                        "(id, canonical_name, normalized_name, funding_stage, scale, "
                        "created_at, updated_at) VALUES "
                        "(:id, 'Postgres Example', 'postgres example', 'unknown', 'unknown', "
                        ":created_at, :created_at)"
                    ),
                    {"id": company_id, "created_at": created_at},
                )
                connection.execute(
                    text(
                        "INSERT INTO job_postings "
                        "(id, company_id, title, normalized_title, job_type, city, "
                        "description, is_active, created_at, updated_at) VALUES "
                        "(:id, :company_id, 'Engineer', 'engineer', 'full_time', '', '', true, "
                        ":created_at, :created_at)"
                    ),
                    {"id": job_id, "company_id": company_id, "created_at": created_at},
                )
                connection.execute(
                    text(
                        "INSERT INTO job_entries "
                        "(id, company_id, url, normalized_url, provider, platform, requires_rendering, "
                        "status, failure_count, created_at, updated_at) VALUES "
                        "(:id, :company_id, 'https://example.com/jobs', 'https://example.com/jobs', "
                        "'official', 'custom', false, 'unknown', 0, :created_at, :created_at)"
                    ),
                    {"id": entry_id, "company_id": company_id, "created_at": created_at},
                )
                connection.execute(
                    text(
                        "INSERT INTO job_collection_snapshots "
                        "(id, job_entry_id, status, pagination_complete, empty_confirmed, "
                        "observed_count, pages_fetched, command_hash, started_at, completed_at, created_at) "
                        "VALUES (:id, :entry_id, 'succeeded', true, true, 0, 0, :command_hash, "
                        ":created_at, :created_at, :created_at)"
                    ),
                    {
                        "id": snapshot_id,
                        "entry_id": entry_id,
                        "command_hash": "b" * 64,
                        "created_at": created_at,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO job_sources "
                        "(id, job_posting_id, job_entry_id, last_seen_snapshot_id, "
                        "provider, source_raw_id, apply_url, first_seen_at, last_seen_at, "
                        "is_active) VALUES "
                        "(:id, :job_id, :entry_id, :snapshot_id, 'legacy', "
                        "'legacy-job-1', 'https://example.com/legacy-job-1', "
                        ":created_at, :created_at, true)"
                    ),
                    {
                        "id": source_id,
                        "job_id": job_id,
                        "entry_id": entry_id,
                        "snapshot_id": snapshot_id,
                        "created_at": created_at,
                    },
                )
                assert connection.scalar(
                    text(
                        "SELECT lifecycle_applied FROM job_collection_snapshots "
                        "WHERE job_entry_id = :entry_id"
                    ),
                    {"entry_id": entry_id},
                ) is False
                assert connection.scalar(
                    text(
                        "SELECT lifecycle_managed FROM job_sources WHERE id = :source_id"
                    ),
                    {"source_id": source_id},
                ) is False
                assert connection.scalar(text("SELECT count(*) FROM job_entries")) == 1
                assert connection.scalar(text("SELECT count(*) FROM job_collection_snapshots")) == 1
        finally:
            schema_engine.dispose()

        command.upgrade(config, "head")
        schema_engine = create_engine(schema_url)
        try:
            with schema_engine.begin() as connection:
                inspector = inspect(connection)
                assert set(inspector.get_table_names()) >= EXPECTED_TABLES
                assert {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints("job_entries")
                } >= {"uq_job_entries_id_company"}
                other_company_id = str(uuid4())
                manifest_version = "a" * 64
                connection.execute(
                    text(
                        "INSERT INTO companies "
                        "(id, canonical_name, normalized_name, funding_stage, scale, "
                        "created_at, updated_at) VALUES "
                        "(:id, 'Postgres Other', 'postgres other', 'unknown', 'unknown', "
                        ":created_at, :created_at)"
                    ),
                    {"id": other_company_id, "created_at": created_at},
                )
                connection.execute(
                    text(
                        "INSERT INTO company_manifests "
                        "(version, config_fingerprint, member_count, canonical_quota, "
                        "frozen_at) VALUES (:version, :fingerprint, 1, '{}', :created_at)"
                    ),
                    {
                        "version": manifest_version,
                        "fingerprint": "b" * 64,
                        "created_at": created_at,
                    },
                )
                observation_insert = (
                    "INSERT INTO entry_discovery_observations "
                    "(id, manifest_version, company_id, method, status, "
                    "requires_rendering, job_entry_id, observed_at) VALUES "
                    "(:id, :version, :company_id, 'official_site', 'accepted', false, "
                    ":entry_id, :created_at)"
                )
                connection.execute(
                    text(observation_insert),
                    {
                        "id": str(uuid4()),
                        "version": manifest_version,
                        "company_id": company_id,
                        "entry_id": entry_id,
                        "created_at": created_at,
                    },
                )
                _expect_integrity_error(
                    connection,
                    observation_insert,
                    {
                        "id": str(uuid4()),
                        "version": manifest_version,
                        "company_id": other_company_id,
                        "entry_id": entry_id,
                        "created_at": created_at,
                    },
                )
        finally:
            schema_engine.dispose()

        command.downgrade(config, "0007_job_source_snapshot_lifecycle")
        schema_engine = create_engine(schema_url)
        try:
            with schema_engine.connect() as connection:
                assert connection.scalar(
                    text("SELECT count(*) FROM companies WHERE id = :id"),
                    {"id": company_id},
                ) == 1
                assert connection.scalar(
                    text("SELECT count(*) FROM job_entries WHERE id = :id"),
                    {"id": entry_id},
                ) == 1
                assert connection.scalar(
                    text("SELECT count(*) FROM job_collection_snapshots WHERE id = :id"),
                    {"id": snapshot_id},
                ) == 1
                source_relationship = connection.execute(
                    text(
                        "SELECT job_posting_id, job_entry_id, last_seen_snapshot_id "
                        "FROM job_sources WHERE id = :id"
                    ),
                    {"id": source_id},
                ).one()
                assert tuple(str(value) for value in source_relationship) == (
                    job_id,
                    entry_id,
                    snapshot_id,
                )
                assert set(inspect(connection).get_table_names()).isdisjoint(
                    {
                        "candidate_facts",
                        "candidate_reviews",
                        "company_manifests",
                        "company_manifest_members",
                        "entry_discovery_observations",
                    }
                )
        finally:
            schema_engine.dispose()

    except BaseException as error:
        primary_error = error
        raise
    finally:
        if schema_created:
            try:
                _cleanup_isolated_postgresql_schema(admin_engine, config, schema_name)
            except BaseException as cleanup_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    "isolated PostgreSQL cleanup also failed: "
                    f"{cleanup_error!r}"
                )
        admin_engine.dispose()

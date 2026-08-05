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
    "companies",
    "company_aliases",
    "source_documents",
    "company_sources",
    "job_postings",
    "job_sources",
    "regulatory_filings",
    "collection_requests",
    "crawl_runs",
    "job_entries",
    "job_collection_snapshots",
}


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
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT job_entry_id, last_seen_snapshot_id, missing_complete_snapshots "
                "FROM job_sources WHERE id = :id"
            ),
            {"id": source_id},
        ).one() == (None, None, 0)
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
                "last_seen_snapshot_id = :snapshot_id, missing_complete_snapshots = 2 "
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
    assert "ADD CONSTRAINT fk_job_sources_job_entry_id FOREIGN KEY(job_entry_id)" in sql
    assert "REFERENCES job_entries (id) ON DELETE SET NULL" in sql
    assert (
        "ADD CONSTRAINT fk_job_sources_last_seen_snapshot_id "
        "FOREIGN KEY(last_seen_snapshot_id)" in sql
    )
    assert "REFERENCES job_collection_snapshots (id) ON DELETE SET NULL" in sql
    assert "CREATE INDEX ix_job_sources_entry_active ON job_sources" in sql


def test_job_source_snapshot_lifecycle_default_sqlite_offline_upgrade_completes() -> None:
    output = StringIO()
    config = Config(Path(__file__).parents[2] / "alembic.ini", output_buffer=output)
    config.set_main_option("sqlalchemy.url", "sqlite://")

    command.upgrade(config, "head", sql=True)

    sql = " ".join(output.getvalue().split())
    assert "CREATE TABLE _alembic_tmp_job_sources" in sql
    assert "job_entry_id CHAR(36)" in sql
    assert "last_seen_snapshot_id CHAR(36)" in sql
    assert "missing_complete_snapshots INTEGER DEFAULT 0 NOT NULL" in sql
    assert "FOREIGN KEY(job_entry_id) REFERENCES job_entries (id) ON DELETE SET NULL" in sql
    assert (
        "FOREIGN KEY(last_seen_snapshot_id) REFERENCES job_collection_snapshots (id) "
        "ON DELETE SET NULL" in sql
    )
    assert "CREATE INDEX ix_job_sources_entry_active ON job_sources" in sql


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
    assert "ix_job_sources_entry_active" not in sql


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


def _cleanup_isolated_postgresql_schema(
    admin_engine: Any, config: Config, schema_name: str
) -> None:
    command.downgrade(config, "base")
    drop_version_sql, drop_schema_sql = _isolated_schema_cleanup_sql(schema_name)
    with admin_engine.begin() as connection:
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

    assert drop_version_sql == f'DROP TABLE "{schema_name}"."alembic_version"'
    assert drop_schema_sql == f'DROP SCHEMA "{schema_name}"'
    assert "CASCADE" not in f"{drop_version_sql} {drop_schema_sql}"
    with pytest.raises(ValueError, match="invalid isolated PostgreSQL schema name"):
        _isolated_schema_cleanup_sql("public")


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

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema_name}"))
        schema_created = True

        command.upgrade(config, "0005_extend_job_type_values")
        command.upgrade(config, "head")
        schema_engine = create_engine(schema_url)
        try:
            with schema_engine.begin() as connection:
                company_id = str(uuid4())
                entry_id = str(uuid4())
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
                        "id": str(uuid4()),
                        "entry_id": entry_id,
                        "command_hash": "b" * 64,
                        "created_at": created_at,
                    },
                )
                assert connection.scalar(text("SELECT count(*) FROM job_entries")) == 1
                assert connection.scalar(text("SELECT count(*) FROM job_collection_snapshots")) == 1
        finally:
            schema_engine.dispose()

        _cleanup_isolated_postgresql_schema(admin_engine, config, schema_name)
        schema_created = False
    finally:
        if schema_created:
            _cleanup_isolated_postgresql_schema(admin_engine, config, schema_name)
        admin_engine.dispose()

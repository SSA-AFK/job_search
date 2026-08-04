import runpy
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
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

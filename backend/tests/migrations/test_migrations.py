from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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


def test_initial_migration_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES | {"alembic_version"}
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

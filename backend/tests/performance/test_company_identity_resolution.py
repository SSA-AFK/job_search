import asyncio
import os
import re
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.company_identity.contracts import CompanyIdentityInput, IdentityResolutionKind
from app.company_identity.repository import SqlAlchemyCompanyIdentityRepository
from app.company_identity.resolver import CompanyIdentityResolver


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


def _drop_isolated_schema(connection, quoted_schema: str) -> None:
    if re.fullmatch(r'"identity_resolution_[0-9a-f]{6,64}"', quoted_schema) is None:
        raise ValueError("invalid isolated schema name")
    statements = (
        f"DROP INDEX IF EXISTS {quoted_schema}.ix_company_aliases_normalized_alias_trgm",
        f"DROP INDEX IF EXISTS {quoted_schema}.ix_companies_normalized_name_trgm",
        f"DROP TABLE IF EXISTS {quoted_schema}.company_aliases",
        f"DROP TABLE IF EXISTS {quoted_schema}.companies",
        f"DROP SCHEMA {quoted_schema}",
    )
    for statement in statements:
        connection.execute(text(statement))


def _isolated_postgresql_url(database_url: str, schema_name: str) -> URL:
    if re.fullmatch(r"identity_resolution_[0-9a-f]{6,64}", schema_name) is None:
        raise ValueError("invalid isolated schema name")
    return make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name},public"}
    )


def _boundary_company_id(value: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{value:012d}")


def _install_pg_trgm_extension(connection) -> None:
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public"))


def _create_pg_trgm_indexes(connection) -> None:
    statements = (
        (
            "CREATE INDEX ix_companies_normalized_name_trgm "
            "ON companies USING gist (normalized_name public.gist_trgm_ops)"
        ),
        (
            "CREATE INDEX ix_company_aliases_normalized_alias_trgm "
            "ON company_aliases USING gist (normalized_alias public.gist_trgm_ops)"
        ),
    )
    for statement in statements:
        connection.execute(text(statement))


@pytest.mark.performance
@pytest.mark.postgresql
def test_isolated_schema_cleanup_drops_only_owned_objects_without_cascade() -> None:
    connection = _RecordingConnection()

    _drop_isolated_schema(connection, '"identity_resolution_abc123"')

    assert connection.statements == [
        (
            'DROP INDEX IF EXISTS "identity_resolution_abc123".'
            "ix_company_aliases_normalized_alias_trgm"
        ),
        (
            'DROP INDEX IF EXISTS "identity_resolution_abc123".'
            "ix_companies_normalized_name_trgm"
        ),
        'DROP TABLE IF EXISTS "identity_resolution_abc123".company_aliases',
        'DROP TABLE IF EXISTS "identity_resolution_abc123".companies',
        'DROP SCHEMA "identity_resolution_abc123"',
    ]


@pytest.mark.performance
@pytest.mark.postgresql
def test_isolated_schema_url_includes_public_after_validated_owned_schema() -> None:
    schema_name = "identity_resolution_abc123"

    schema_url = _isolated_postgresql_url(
        "postgresql://localhost/company_search", schema_name
    )

    assert schema_url.query["options"] == f"-csearch_path={schema_name},public"
    with pytest.raises(ValueError, match="invalid isolated schema name"):
        _isolated_postgresql_url(
            "postgresql://localhost/company_search",
            "identity_resolution_abc123,public",
        )
    with pytest.raises(ValueError, match="invalid isolated schema name"):
        _isolated_postgresql_url("postgresql://localhost/company_search", "public")


@pytest.mark.performance
@pytest.mark.postgresql
def test_boundary_company_id_matches_decimal_text_seed() -> None:
    company_id = _boundary_company_id(10)

    assert company_id == UUID("00000000-0000-0000-0000-000000000010")
    assert company_id != UUID(int=10)


@pytest.mark.performance
@pytest.mark.postgresql
def test_pg_trgm_fixture_ddl_uses_stable_public_schema() -> None:
    connection = _RecordingConnection()

    _install_pg_trgm_extension(connection)
    _create_pg_trgm_indexes(connection)

    assert connection.statements == [
        "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public",
        (
            "CREATE INDEX ix_companies_normalized_name_trgm "
            "ON companies USING gist (normalized_name public.gist_trgm_ops)"
        ),
        (
            "CREATE INDEX ix_company_aliases_normalized_alias_trgm "
            "ON company_aliases USING gist (normalized_alias public.gist_trgm_ops)"
        ),
    ]


@pytest.mark.performance
@pytest.mark.postgresql
def test_ten_thousand_company_resolution_uses_bounded_trigram_recall() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"identity_resolution_{uuid4().hex}"
    schema_url = _isolated_postgresql_url(database_url, schema_name)
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_engine(database_url)
    schema_engine = None
    schema_created = False
    try:
        with admin_engine.begin() as connection:
            _install_pg_trgm_extension(connection)
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
            schema_created = True

        schema_engine = create_engine(schema_url)
        with schema_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE companies ("
                    "id uuid PRIMARY KEY, canonical_name text NOT NULL, "
                    "normalized_name text NOT NULL, website text)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE company_aliases ("
                    "id uuid PRIMARY KEY, company_id uuid NOT NULL REFERENCES companies(id), "
                    "alias text NOT NULL, normalized_alias text NOT NULL)"
                )
            )
            _create_pg_trgm_indexes(connection)
            connection.execute(
                text(
                    "INSERT INTO companies (id, canonical_name, normalized_name) "
                    "SELECT md5('company-' || value)::uuid, "
                    "'Benchmark Company ' || lpad(value::text, 5, '0'), "
                    "'benchmarkcompany' || lpad(value::text, 5, '0') "
                    "FROM generate_series(1, 10000) AS value"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO company_aliases (id, company_id, alias, normalized_alias) "
                    "SELECT md5('alias-' || value)::uuid, md5('company-' || value)::uuid, "
                    "'Benchmark Alias ' || lpad(value::text, 5, '0'), "
                    "'benchmarkalias' || lpad(value::text, 5, '0') "
                    "FROM generate_series(1, 10000) AS value"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO companies (id, canonical_name, normalized_name) "
                    "SELECT ('00000000-0000-0000-0000-' || "
                    "lpad(value::text, 12, '0'))::uuid, "
                    "'KNN Boundary ' || chr(96 + value), "
                    "'knnboundary' || chr(96 + value) "
                    "FROM generate_series(1, 25) AS value"
                )
            )
            connection.execute(text("ANALYZE companies"))
            connection.execute(text("ANALYZE company_aliases"))

            assert connection.scalar(text("SELECT count(*) FROM companies")) == 10_025
            assert connection.scalar(
                text(
                    "SELECT count(DISTINCT normalized_name <-> 'knnboundaryz') "
                    "FROM companies WHERE normalized_name LIKE 'knnboundary%'"
                )
            ) == 1
            plan = "\n".join(
                connection.execute(
                    text(
                        "EXPLAIN (COSTS OFF) "
                        "SELECT id, canonical_name, normalized_name FROM companies "
                        "ORDER BY normalized_name <-> 'benchmarkcompany05000x', "
                        "normalized_name, id LIMIT 20"
                    )
                ).scalars()
            )
            assert "ix_companies_normalized_name_trgm" in plan

        statements: list[str] = []
        event.listen(
            schema_engine,
            "before_cursor_execute",
            lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(
                " ".join(statement.upper().split())
            ),
        )
        with Session(schema_engine) as session:
            repository = SqlAlchemyCompanyIdentityRepository(session, similarity_limit=20)
            result = asyncio.run(
                CompanyIdentityResolver(
                    repository
                ).resolve(CompanyIdentityInput(canonical_name="Benchmark Company 05000x"))
            )
            boundary_matches = asyncio.run(
                repository.find_similar_names(frozenset({"knnboundaryz"}), limit=20)
            )

        assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
        assert 1 <= len(result.candidate_matches) <= 20
        assert tuple(match.company_id for match in boundary_matches) == tuple(
            _boundary_company_id(value) for value in range(1, 21)
        )
        assert all(
            "SELECT COMPANIES.ID, COMPANIES.NORMALIZED_NAME FROM COMPANIES"
            not in statement
            for statement in statements
        )
        similarity_statements = [
            statement for statement in statements if "<->" in statement and "ORDER BY" in statement
        ]
        assert len(similarity_statements) == 4
        assert all("LIMIT" in statement for statement in similarity_statements)
        assert sum("JOIN COMPANY_ALIASES" in statement for statement in similarity_statements) == 2
        assert sum("JOIN COMPANY_ALIASES" not in statement for statement in similarity_statements) == 2
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                _drop_isolated_schema(connection, quoted_schema)
        admin_engine.dispose()

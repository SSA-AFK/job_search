import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.company_identity.contracts import CompanyIdentityInput, IdentityResolutionKind
from app.company_identity.repository import SqlAlchemyCompanyIdentityRepository
from app.company_identity.resolver import CompanyIdentityResolver


@pytest.mark.performance
@pytest.mark.postgresql
def test_ten_thousand_company_resolution_uses_bounded_trigram_recall() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"identity_resolution_{uuid4().hex}"
    assert schema_name.isidentifier()
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_engine(database_url)
    schema_engine = None
    schema_created = False
    try:
        with admin_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
            schema_created = True

        schema_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema_name}"}
        )
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
            connection.execute(
                text(
                    "CREATE INDEX ix_companies_normalized_name_trgm "
                    "ON companies USING gist (normalized_name gist_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_company_aliases_normalized_alias_trgm "
                    "ON company_aliases USING gist (normalized_alias gist_trgm_ops)"
                )
            )
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
            connection.execute(text("ANALYZE companies"))
            connection.execute(text("ANALYZE company_aliases"))

            assert connection.scalar(text("SELECT count(*) FROM companies")) == 10_000
            plan = "\n".join(
                connection.execute(
                    text(
                        "EXPLAIN (COSTS OFF) "
                        "SELECT id, canonical_name, normalized_name FROM companies "
                        "ORDER BY normalized_name <-> 'benchmarkcompany05000x' LIMIT 20"
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
            result = asyncio.run(
                CompanyIdentityResolver(
                    SqlAlchemyCompanyIdentityRepository(session, similarity_limit=20)
                ).resolve(CompanyIdentityInput(canonical_name="Benchmark Company 05000x"))
            )

        assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
        assert 1 <= len(result.candidate_matches) <= 20
        assert all(
            "SELECT COMPANIES.ID, COMPANIES.NORMALIZED_NAME FROM COMPANIES"
            not in statement
            for statement in statements
        )
        similarity_statements = [statement for statement in statements if "<->" in statement]
        assert len(similarity_statements) == 2
        assert all("ORDER BY" in statement and "LIMIT" in statement for statement in similarity_statements)
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
        admin_engine.dispose()

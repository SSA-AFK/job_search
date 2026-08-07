import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.company_identity.contracts import (
    CompanyIdentityInput,
    IdentityReviewAction,
    IdentityReviewStatus,
)
from app.company_identity.models import (
    CompanyIdentityReviewDecision,
    CompanyIdentityReviewItem,
)
from app.company_identity.repository import SqlAlchemyCompanyIdentityRepository
from app.models import Base, Company, CompanyAlias, CrawlRun, RegulatoryFiling
from app.models.enums import CollectionStatus, FilingType, RunType

COMPANY_A = UUID("00000000-0000-0000-0000-000000000001")
COMPANY_B = UUID("00000000-0000-0000-0000-000000000002")
COMPANY_C = UUID("00000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)


def company(
    company_id: UUID,
    canonical_name: str,
    *,
    website: str | None = None,
) -> Company:
    return Company(
        id=company_id,
        canonical_name=canonical_name,
        normalized_name=canonical_name,
        website=website,
        funding_stage="unknown",
        scale="unknown",
    )


def test_exact_ownership_uses_one_query_across_canonical_names_and_aliases() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    with Session(engine) as session:
        session.add_all(
            (
                company(COMPANY_A, "openai"),
                company(COMPANY_B, "example"),
                CompanyAlias(
                    company_id=COMPANY_B,
                    alias="OpenAI",
                    normalized_alias="openai",
                ),
            )
        )
        session.commit()
        statements.clear()

        owners = asyncio.run(
            SqlAlchemyCompanyIdentityRepository(session).find_exact_name_owners(
                frozenset({"openai", "missing"})
            )
        )

    assert tuple((owner.company_id, owner.normalized_name) for owner in owners) == (
        (COMPANY_A, "openai"),
        (COMPANY_B, "openai"),
    )
    assert len(statements) == 1
    assert "UNION" in statements[0].upper()


def test_sqlite_reports_similarity_unavailable_without_querying_companies() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    with Session(engine) as session:
        repository = SqlAlchemyCompanyIdentityRepository(session)
        statements.clear()

        candidates = asyncio.run(
            repository.find_similar_names(frozenset({"openai"}), limit=20)
        )

    assert repository.similarity_search_available() is False
    assert candidates == ()
    assert statements == []


class _EmptyResult:
    def all(self) -> list[object]:
        return []

    def __iter__(self):
        return iter(())


class _PostgreSQLRecordingSession:
    def __init__(self, *, similarity_capable: bool = True) -> None:
        self.bind = SimpleNamespace(dialect=postgresql.dialect())
        self.statements: list[object] = []
        self.similarity_capable = similarity_capable
        self.capability_checks = 0
        self.capability_statements: list[object] = []

    def execute(self, statement: object) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()

    def scalar(self, statement: object) -> bool:
        self.capability_checks += 1
        self.capability_statements.append(statement)
        return self.similarity_capable


def test_postgresql_similarity_sql_is_knn_bounded_per_source() -> None:
    session = _PostgreSQLRecordingSession()
    repository = SqlAlchemyCompanyIdentityRepository(session, similarity_limit=20)  # type: ignore[arg-type]

    candidates = asyncio.run(
        repository.find_similar_names(frozenset({"openal"}), limit=20)
    )

    assert candidates == ()
    assert len(session.statements) == 2
    compiled_statements = tuple(
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).upper()
        for statement in session.statements
    )
    for compiled_sql in compiled_statements:
        assert "<->" in compiled_sql
        assert "ORDER BY" in compiled_sql
        assert "LIMIT 20" in compiled_sql
        assert "SELECT COMPANIES.ID, COMPANIES.NORMALIZED_NAME FROM COMPANIES" not in " ".join(
            compiled_sql.split()
        )
    assert any("FROM COMPANIES" in sql for sql in compiled_statements)
    assert any("COMPANY_ALIASES" in sql for sql in compiled_statements)


def test_postgresql_missing_trigram_capability_is_cached_as_unavailable() -> None:
    session = _PostgreSQLRecordingSession(similarity_capable=False)
    repository = SqlAlchemyCompanyIdentityRepository(session)  # type: ignore[arg-type]

    assert repository.similarity_search_available() is False
    assert repository.similarity_search_available() is False
    assert session.capability_checks == 1
    capability_sql = str(session.capability_statements[0]).lower()
    assert "pg_catalog.pg_extension" in capability_sql
    assert "pg_catalog.pg_operator" in capability_sql
    assert "pg_trgm" in capability_sql


def test_postgresql_knn_boundary_order_is_stable_before_limit() -> None:
    session = _PostgreSQLRecordingSession()
    repository = SqlAlchemyCompanyIdentityRepository(session, similarity_limit=20)  # type: ignore[arg-type]

    asyncio.run(repository.find_similar_names(frozenset({"same-distance"}), limit=20))

    assert len(session.statements) == 2
    compiled_statements = tuple(
        " ".join(
            str(
                statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            .upper()
            .split()
        )
        for statement in session.statements
    )
    canonical_sql = next(
        sql for sql in compiled_statements if "FROM COMPANIES" in sql and "JOIN" not in sql
    )
    alias_sql = next(sql for sql in compiled_statements if "JOIN COMPANY_ALIASES" in sql)

    assert canonical_sql.count("LIMIT") == 1
    assert (
        "ORDER BY COMPANIES.NORMALIZED_NAME <-> 'SAME-DISTANCE', "
        "COMPANIES.NORMALIZED_NAME, COMPANIES.ID LIMIT 20" in canonical_sql
    )
    assert alias_sql.count("LIMIT") == 1
    assert (
        "ORDER BY COMPANY_ALIASES.NORMALIZED_ALIAS <-> 'SAME-DISTANCE', "
        "COMPANY_ALIASES.NORMALIZED_ALIAS, COMPANIES.ID, COMPANY_ALIASES.ID LIMIT 20"
        in alias_sql
    )


def resolved_review(
    session: Session,
    *,
    company_id: UUID,
    stable_hash: str,
    website: str | None,
    recruitment_identity: str | None,
    legal_identifiers: list[str],
    status: IdentityReviewStatus = IdentityReviewStatus.RESOLVED,
) -> None:
    crawl_run = CrawlRun(
        run_type=RunType.DISCOVERY,
        status=CollectionStatus.SUCCEEDED,
        providers_attempted=[],
        created_at=NOW,
    )
    session.add(crawl_run)
    session.flush()
    item = CompanyIdentityReviewItem(
        stable_identity_hash=stable_hash,
        first_crawl_run_id=crawl_run.id,
        status=status,
        candidate_name="Reviewed Candidate",
        normalized_name="reviewedcandidate",
        aliases=[],
        official_website=website,
        recruitment_identity=recruitment_identity,
        legal_identifiers=legal_identifiers,
        city=None,
        public_evidence_refs=[],
        candidate_matches=[],
        review_reasons=["website_identity_conflict"],
        created_at=NOW,
        resolved_at=NOW if status is not IdentityReviewStatus.PENDING else None,
    )
    session.add(item)
    session.flush()
    session.add(
        CompanyIdentityReviewDecision(
            review_item_id=item.id,
            action=IdentityReviewAction.CREATE_NEW,
            target_company_id=None,
            resulting_company_id=company_id,
            reason="Reviewed public identity.",
            decided_at=NOW,
            decision_hash=stable_hash[::-1],
        )
    )


def test_evidence_owners_include_current_records_and_only_resolved_review_history() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            (
                company(COMPANY_A, "alpha", website="https://alpha.example/"),
                company(COMPANY_B, "beta"),
                company(COMPANY_C, "gamma"),
            )
        )
        session.flush()
        session.add(
            RegulatoryFiling(
                company_id=COMPANY_B,
                filing_type=FilingType.BUSINESS_LICENSE,
                filing_number="cn-123",
                filing_name="Beta filing",
            )
        )
        resolved_review(
            session,
            company_id=COMPANY_B,
            stable_hash="a" * 64,
            website="https://history.example/",
            recruitment_identity="tenant:beta",
            legal_identifiers=["cn-456"],
        )
        resolved_review(
            session,
            company_id=COMPANY_C,
            stable_hash="b" * 64,
            website="https://stale.example/",
            recruitment_identity="tenant:stale",
            legal_identifiers=["cn-stale"],
            status=IdentityReviewStatus.PENDING,
        )
        resolved_review(
            session,
            company_id=COMPANY_C,
            stable_hash="c" * 64,
            website="https://rejected.example/",
            recruitment_identity="tenant:rejected",
            legal_identifiers=["cn-rejected"],
            status=IdentityReviewStatus.REJECTED,
        )
        session.commit()
        repository = SqlAlchemyCompanyIdentityRepository(session, similarity_limit=20)

        website_current = asyncio.run(
            repository.find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    official_website="https://alpha.example/",
                )
            )
        )
        website_history = asyncio.run(
            repository.find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    official_website="https://history.example/",
                )
            )
        )
        recruitment_history = asyncio.run(
            repository.find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    recruitment_identity="tenant:beta",
                )
            )
        )
        legal_current_and_history = asyncio.run(
            repository.find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    legal_identifiers=("CN-123", "CN-456"),
                )
            )
        )
        stale = asyncio.run(
            repository.find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    official_website="https://stale.example/",
                    recruitment_identity="tenant:stale",
                    legal_identifiers=("CN-STALE",),
                )
            )
        )
        rejected = asyncio.run(
            repository.find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    official_website="https://rejected.example/",
                    recruitment_identity="tenant:rejected",
                    legal_identifiers=("CN-REJECTED",),
                )
            )
        )

    assert website_current == frozenset({COMPANY_A})
    assert website_history == frozenset({COMPANY_B})
    assert recruitment_history == frozenset({COMPANY_B})
    assert legal_current_and_history == frozenset({COMPANY_B})
    assert stale == frozenset()
    assert rejected == frozenset()


def test_historical_company_website_uses_backfilled_normalized_identity() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        stored = company(
            COMPANY_A,
            "alpha",
            website="HTTPS://Alpha.Example/path?campaign=ignored#fragment",
        )
        session.add(stored)
        session.commit()
        session.execute(
            text("UPDATE companies SET website = :website WHERE id = :company_id"),
            {
                "website": "HTTPS://Alpha.Example/path?campaign=ignored#fragment",
                "company_id": str(COMPANY_A),
            },
        )
        session.commit()
        session.refresh(stored)

        owners = asyncio.run(
            SqlAlchemyCompanyIdentityRepository(session).find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    official_website="https://alpha.example/path",
                )
            )
        )

    assert stored.website == "HTTPS://Alpha.Example/path?campaign=ignored#fragment"
    assert owners == frozenset({COMPANY_A})


def test_clearing_company_website_clears_normalized_identity() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        stored = company(COMPANY_A, "alpha", website="https://alpha.example/path")
        session.add(stored)
        session.commit()

        stored.website = None
        session.commit()

        owners = asyncio.run(
            SqlAlchemyCompanyIdentityRepository(session).find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    official_website="https://alpha.example/path",
                )
            )
        )

    assert owners == frozenset()


def test_historical_filing_number_uses_backfilled_normalized_identity() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    with Session(engine) as session:
        session.add(company(COMPANY_A, "alpha"))
        stored = RegulatoryFiling(
            company_id=COMPANY_A,
            filing_type=FilingType.BUSINESS_LICENSE,
            filing_number="K STRASSE 42",
            filing_name="Alpha filing",
        )
        session.add(stored)
        session.commit()
        session.execute(
            text(
                "UPDATE regulatory_filings SET filing_number = :filing_number "
                "WHERE id = :filing_id"
            ),
            {
                "filing_number": "  Ｋ\u3000Straße\t42 ",
                "filing_id": str(stored.id),
            },
        )
        session.commit()
        session.refresh(stored)
        statements.clear()

        owners = asyncio.run(
            SqlAlchemyCompanyIdentityRepository(session).find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    legal_identifiers=("K STRASSE 42",),
                )
            )
        )

    assert stored.filing_number == "  Ｋ\u3000Straße\t42 "
    assert owners == frozenset({COMPANY_A})
    assert statements
    assert all("LOWER(" not in statement.upper() for statement in statements)


def test_evidence_queries_are_sql_bounded_and_deduplicated() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(
            statement
        ),
    )
    with Session(engine) as session:
        session.add(company(COMPANY_A, "alpha", website="https://alpha.example/"))
        session.commit()
        statements.clear()

        owners = asyncio.run(
            SqlAlchemyCompanyIdentityRepository(
                session, similarity_limit=20
            ).find_evidence_owner_ids(
                CompanyIdentityInput(
                    canonical_name="Candidate",
                    official_website="https://alpha.example/",
                )
            )
        )

    assert owners == frozenset({COMPANY_A})
    assert statements
    assert all("LIMIT" in statement.upper() for statement in statements)
    assert all("SELECT COMPANIES.ID, COMPANIES.NORMALIZED_NAME FROM COMPANIES" not in " ".join(
        statement.upper().split()
    ) for statement in statements)

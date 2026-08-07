import asyncio

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.ingestion.deduplication.company import CompanyDeduplicator
from app.ingestion.extraction.schemas import CompanyCandidate
from app.ingestion.extraction.schemas import EmploymentType
from app.ingestion.repositories import (
    SqlAlchemyCompanyDeduplicationRepository,
    SqlAlchemyJobDeduplicationRepository,
)
from app.models import Base, Company, JobPosting, JobType


def test_sql_job_repository_preserves_persisted_employment_types() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        company = Company(canonical_name="Example", normalized_name="example")
        session.add(company)
        session.flush()
        session.add_all(
            (
                JobPosting(
                    company_id=company.id,
                    title="Part-time Engineer",
                    normalized_title="parttimeengineer",
                    job_type=JobType.PART_TIME,
                    city="shanghai",
                    description="Part-time role",
                ),
                JobPosting(
                    company_id=company.id,
                    title="Temporary Engineer",
                    normalized_title="temporaryengineer",
                    job_type=JobType.TEMPORARY,
                    city="shanghai",
                    description="Temporary role",
                ),
            )
        )
        session.commit()

        comparisons = asyncio.run(
            SqlAlchemyJobDeduplicationRepository(session).list_for_company(company.id)
        )

    employment_types = {
        comparison.job_type: comparison.employment_type for comparison in comparisons
    }
    assert employment_types == {
        JobType.PART_TIME: EmploymentType.PART_TIME,
        JobType.TEMPORARY: EmploymentType.TEMPORARY,
    }


def test_sql_company_compatibility_repository_never_lists_all_companies() -> None:
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
        session.add(Company(canonical_name="OpenAI", normalized_name="openai"))
        session.commit()
        statements.clear()

        match = asyncio.run(
            CompanyDeduplicator(
                SqlAlchemyCompanyDeduplicationRepository(session)
            ).resolve(
                CompanyCandidate(
                    name="Open Al",
                    evidence_ids=("doc-1",),
                    confidence=0.9,
                )
            )
        )

    assert match.kind == "review_required"
    compact_sql = tuple(" ".join(statement.upper().split()) for statement in statements)
    assert all(
        "SELECT COMPANIES.ID, COMPANIES.NORMALIZED_NAME FROM COMPANIES" not in statement
        for statement in compact_sql
    )

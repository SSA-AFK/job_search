import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.ingestion.extraction.schemas import EmploymentType
from app.ingestion.repositories import SqlAlchemyJobDeduplicationRepository
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

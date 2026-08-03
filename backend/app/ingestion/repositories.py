"""SQLAlchemy read repositories used by the ingestion runtime."""

from collections.abc import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ingestion.deduplication.company import CompanyForComparison
from app.ingestion.deduplication.job import JobForComparison, SourceJobMatch
from app.models import Company, CompanyAlias, JobPosting, JobSource


class SqlAlchemyCompanyDeduplicationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def find_by_normalized_name_or_alias(self, normalized_name: str):
        return self.session.scalar(
            select(Company.id).outerjoin(CompanyAlias).where(
                or_(Company.normalized_name == normalized_name, CompanyAlias.normalized_alias == normalized_name)
            )
        )

    async def list_for_deduplication(self) -> Iterable[CompanyForComparison]:
        return tuple(
            CompanyForComparison(company_id=item.id, normalized_name=item.normalized_name)
            for item in self.session.scalars(select(Company))
        )


class SqlAlchemyJobDeduplicationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def find_by_source(self, provider: str, source_raw_id: str) -> SourceJobMatch | None:
        source = self.session.scalar(
            select(JobSource).where(JobSource.provider == provider, JobSource.source_raw_id == source_raw_id)
        )
        if source is None:
            return None
        job = self.session.get(JobPosting, source.job_posting_id)
        return None if job is None else SourceJobMatch(source.job_posting_id, job.company_id)

    async def list_for_company(self, company_id) -> Iterable[JobForComparison]:
        return tuple(
            JobForComparison(item.id, item.normalized_title, item.city, item.job_type)
            for item in self.session.scalars(select(JobPosting).where(JobPosting.company_id == company_id))
        )

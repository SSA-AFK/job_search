from collections import defaultdict
from typing import Any, cast
from uuid import UUID

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.companies.schemas import CompanyQuery, CompanySort, JobQuery
from app.core.normalization import normalize_name
from app.models import (
    Company,
    CompanyAlias,
    CompanyProfileField,
    CompanySource,
    FundingEvent,
    FundingInvestor,
    JobPosting,
    JobSource,
    RegulatoryFiling,
    SourceDocument,
)


class CompanyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def search(self, query: CompanyQuery) -> tuple[list[Company], int]:
        filters: list[ColumnElement[bool]] = []
        normalized_query = normalize_name(query.q) if query.q else ""
        relevance = None
        if normalized_query:
            alias_exact = self._alias_matches(CompanyAlias.normalized_alias == normalized_query)
            alias_prefix = self._alias_matches(
                CompanyAlias.normalized_alias.startswith(normalized_query, autoescape=True)
            )
            alias_contains = self._alias_matches(
                CompanyAlias.normalized_alias.contains(normalized_query, autoescape=True)
            )
            name_prefix = Company.normalized_name.startswith(normalized_query, autoescape=True)
            name_contains = Company.normalized_name.contains(normalized_query, autoescape=True)
            filters.append(or_(name_contains, alias_contains))
            relevance = case(
                (Company.normalized_name == normalized_query, 0),
                (alias_exact, 1),
                (name_prefix, 2),
                (alias_prefix, 3),
                (name_contains, 4),
                (alias_contains, 5),
                else_=6,
            )

        for field in ("industry", "sub_industry", "funding_stage", "scale", "city"):
            value = getattr(query, field)
            if value is not None:
                filters.append(getattr(Company, field) == value)

        count_statement = select(func.count(Company.id)).where(*filters)
        total = self.session.scalar(count_statement) or 0

        statement = select(Company).where(*filters)
        if query.resolved_sort is CompanySort.RELEVANCE and relevance is not None:
            statement = statement.order_by(relevance, Company.canonical_name, Company.id)
        elif query.resolved_sort is CompanySort.NAME:
            statement = statement.order_by(Company.canonical_name, Company.id)
        else:
            statement = statement.order_by(
                Company.updated_at.desc(), Company.canonical_name, Company.id
            )
        statement = statement.offset((query.page - 1) * query.page_size).limit(query.page_size)
        return list(self.session.scalars(statement)), total

    def get_detail(self, company_id: UUID) -> Company | None:
        company = self.session.get(Company, company_id)
        if company is None:
            return None

        aliases = list(
            self.session.scalars(
                select(CompanyAlias)
                .where(CompanyAlias.company_id == company_id)
                .order_by(CompanyAlias.alias, CompanyAlias.id)
            )
        )
        filings = list(
            self.session.scalars(
                select(RegulatoryFiling)
                .where(RegulatoryFiling.company_id == company_id)
                .order_by(
                    RegulatoryFiling.filing_date.desc(),
                    RegulatoryFiling.filing_type,
                    RegulatoryFiling.filing_number,
                )
            )
        )
        source_rows = list(
            self.session.execute(
                select(CompanySource, SourceDocument)
                .join(
                    SourceDocument,
                    SourceDocument.id == CompanySource.source_document_id,
                )
                .where(CompanySource.company_id == company_id)
                .order_by(SourceDocument.provider, SourceDocument.external_id, SourceDocument.id)
            ).all()
        )
        job_count = self.session.scalar(
            select(func.count(JobPosting.id)).where(JobPosting.company_id == company_id)
        )
        loaded_company = cast(Any, company)
        loaded_company._loaded_aliases = aliases
        loaded_company._loaded_filings = filings
        loaded_company._loaded_sources = source_rows
        loaded_company._loaded_profile_fields = list(
            self.session.scalars(
                select(CompanyProfileField)
                .where(CompanyProfileField.company_id == company_id)
                .order_by(CompanyProfileField.field_key)
            )
        )
        loaded_company._loaded_funding_events = list(
            self.session.scalars(
                select(FundingEvent)
                .where(FundingEvent.company_id == company_id)
                .order_by(FundingEvent.announced_at.desc(), FundingEvent.id)
            )
        )
        loaded_company._loaded_funding_investors = {
            event.id: list(
                self.session.scalars(
                    select(FundingInvestor.name)
                    .where(FundingInvestor.funding_event_id == event.id)
                    .order_by(FundingInvestor.name)
                )
            )
            for event in company._loaded_funding_events  # type: ignore[attr-defined]
        }  # type: ignore[attr-defined]
        company._loaded_job_count = job_count or 0  # type: ignore[attr-defined]
        return company

    def list_jobs(self, company_id: UUID, query: JobQuery) -> tuple[list[JobPosting], int]:
        filters: list[ColumnElement[bool]] = [JobPosting.company_id == company_id]
        if query.job_type is not None:
            filters.append(JobPosting.job_type == query.job_type)
        if query.city is not None:
            filters.append(JobPosting.city == query.city)
        if query.active_only:
            filters.append(JobPosting.is_active.is_(True))

        total = self.session.scalar(select(func.count(JobPosting.id)).where(*filters)) or 0
        statement = (
            select(JobPosting)
            .where(*filters)
            .order_by(
                JobPosting.posted_at.desc().nulls_last(),
                JobPosting.title,
                JobPosting.id,
            )
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        jobs = list(self.session.scalars(statement))
        if jobs:
            source_rows = self.session.scalars(
                select(JobSource)
                .where(JobSource.job_posting_id.in_([job.id for job in jobs]))
                .order_by(
                    JobSource.job_posting_id,
                    JobSource.provider,
                    JobSource.source_raw_id,
                )
            )
            sources_by_job: dict[UUID, list[JobSource]] = defaultdict(list)
            for source in source_rows:
                sources_by_job[source.job_posting_id].append(source)
            for job in jobs:
                job._loaded_sources = sources_by_job[job.id]  # type: ignore[attr-defined]
        return jobs, total

    def company_exists(self, company_id: UUID) -> bool:
        return bool(
            self.session.scalar(
                select(exists().where(Company.id == company_id))
            )
        )

    @staticmethod
    def _alias_matches(predicate: ColumnElement[bool]) -> ColumnElement[bool]:
        return exists(
            select(1).where(CompanyAlias.company_id == Company.id, predicate)
        )

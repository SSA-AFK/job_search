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
    CompanyRankingSignal,
    CompanySource,
    FundingEvent,
    FundingInvestor,
    JobPosting,
    JobSource,
    RegulatoryFiling,
    SourceDocument,
)
from app.rankings.repository import RankingRepository


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

        ranking_rows = RankingRepository(self.session).rows()
        if not ranking_rows:
            return [], 0
        member_ids = [row.company.id for row in ranking_rows]
        filters.append(Company.id.in_(member_ids))

        statement = select(Company).where(*filters)
        if query.resolved_sort is CompanySort.RELEVANCE and relevance is not None:
            statement = statement.order_by(relevance, Company.canonical_name, Company.id)
        elif query.resolved_sort is CompanySort.NAME:
            statement = statement.order_by(Company.canonical_name, Company.id)
        else:
            statement = statement.order_by(
                Company.updated_at.desc(), Company.canonical_name, Company.id
            )
        companies = list(self.session.scalars(statement))
        rank_by_company = {row.company.id: row for row in ranking_rows}
        for company in companies:
            ranking = rank_by_company[company.id]
            company._loaded_ranking_snapshot = ranking.snapshot  # type: ignore[attr-defined]
            company._loaded_ranking_rank = ranking.rank  # type: ignore[attr-defined]
        if not normalized_query and query.sort is None:
            companies.sort(
                key=lambda company: (
                    rank_by_company[company.id].rank is None,
                    rank_by_company[company.id].rank or 10_000,
                )
            )
        total = len(companies)
        start = (query.page - 1) * query.page_size
        return companies[start : start + query.page_size], total

    def get_detail(self, company_id: UUID) -> Company | None:
        ranking = next(
            (row for row in RankingRepository(self.session).rows() if row.company.id == company_id),
            None,
        )
        if ranking is None:
            return None
        company = ranking.company
        company._loaded_ranking_snapshot = ranking.snapshot  # type: ignore[attr-defined]
        company._loaded_ranking_rank = ranking.rank  # type: ignore[attr-defined]

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
                .where(RegulatoryFiling.filing_type != "business_license")
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
        source_rows = list(
            {
                (document.provider, document.url, document.content_hash): (company_source, document)
                for company_source, document in source_rows
            }.values()
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
        loaded_company._loaded_ranking_signals = list(
            self.session.scalars(
                select(CompanyRankingSignal)
                .where(CompanyRankingSignal.company_id == company_id)
                .order_by(
                    CompanyRankingSignal.category,
                    CompanyRankingSignal.event_date.desc(),
                    CompanyRankingSignal.signal_key,
                )
            )
        )
        company._loaded_job_count = job_count or 0  # type: ignore[attr-defined]
        return company

    def list_jobs(self, company_id: UUID, query: JobQuery) -> tuple[list[JobPosting], int]:
        source_exists = exists(
            select(1).where(
                JobSource.job_posting_id == JobPosting.id,
                JobSource.provider.like("jobhunt:%"),
                JobSource.is_active.is_(True),
            )
        )
        filters: list[ColumnElement[bool]] = [
            JobPosting.company_id == company_id,
            self._early_career_predicate(),
            source_exists,
        ]
        if query.city is not None:
            filters.append(JobPosting.city == query.city)
        if query.active_only:
            filters.append(JobPosting.is_active.is_(True))

        total = min(self.session.scalar(select(func.count(JobPosting.id)).where(*filters)) or 0, 20)
        display_order = case(
            (func.lower(JobPosting.title).like("%实习%"), 1),
            (func.lower(JobPosting.title).like("%intern%"), 1),
            (JobPosting.job_type == "campus", 0),
            else_=1,
        )
        statement = (
            select(JobPosting)
            .where(*filters)
            .order_by(
                display_order,
                JobPosting.posted_at.desc().nulls_last(),
                JobPosting.updated_at.desc(),
                JobPosting.id,
            )
            .offset((query.page - 1) * min(query.page_size, 20))
            .limit(min(query.page_size, 20))
        )
        jobs = list(self.session.scalars(statement))
        if jobs:
            source_rows = self.session.scalars(
                select(JobSource)
                .where(JobSource.job_posting_id.in_([job.id for job in jobs]))
                .where(JobSource.provider.like("jobhunt:%"))
                .where(JobSource.is_active.is_(True))
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

    def early_career_counts(self, company_ids: list[UUID]) -> dict[UUID, tuple[int, int]]:
        if not company_ids:
            return {}
        rows = self.session.execute(
            select(JobPosting.company_id, JobPosting.job_type, JobPosting.title)
            .where(JobPosting.company_id.in_(company_ids))
            .where(JobPosting.is_active.is_(True))
            .where(self._early_career_predicate())
            .where(
                exists(
                    select(1).where(
                        JobSource.job_posting_id == JobPosting.id,
                        JobSource.provider.like("jobhunt:%"),
                        JobSource.is_active.is_(True),
                    )
                )
            )
        )
        counts: dict[UUID, list[int]] = defaultdict(lambda: [0, 0])
        for company_id, job_type, title in rows:
            index = 0 if self._public_job_type(job_type, title) == "campus" else 1
            counts[company_id][index] += 1
        return {key: (value[0], value[1]) for key, value in counts.items()}

    @staticmethod
    def _public_job_type(job_type: object, title: str) -> str:
        lowered = title.lower()
        if "实习" in lowered or "intern" in lowered:
            return "internship"
        value = getattr(job_type, "value", str(job_type))
        return "campus" if value == "campus" else "internship"

    @staticmethod
    def _early_career_predicate() -> ColumnElement[bool]:
        lowered = func.lower(JobPosting.title)
        return or_(
            JobPosting.job_type.in_(["campus", "internship"]),
            *(lowered.like(f"%{keyword}%") for keyword in ("实习", "校招", "校园", "应届", "intern", "graduate")),
        )

    def company_exists(self, company_id: UUID) -> bool:
        return any(row.company.id == company_id for row in RankingRepository(self.session).rows())

    @staticmethod
    def _alias_matches(predicate: ColumnElement[bool]) -> ColumnElement[bool]:
        return exists(
            select(1).where(CompanyAlias.company_id == Company.id, predicate)
        )

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.cache.base import CompanyCache, ListCacheEntry
from app.companies.repository import CompanyRepository
from app.companies.schemas import (
    CompanyDetail,
    CompanyListItem,
    CompanyQuery,
    CompanySourceSummary,
    FilingItem,
    JobListItem,
    JobQuery,
    JobSourceItem,
    Page,
)
from app.core.errors import CompanyNotFoundError
from app.models import Company, CompanySource, JobPosting, SourceDocument


class CompanyService:
    def __init__(self, repository: CompanyRepository, *, cache: CompanyCache | None = None) -> None:
        self.repository = repository
        self.cache = cache

    def search(self, query: CompanyQuery) -> Page[CompanyListItem]:
        params = query.model_dump(mode="json")
        entry = self.cache.get_list(params) if self.cache is not None else ListCacheEntry(None, None)
        cached = self._deserialize_cached(entry.value, Page[CompanyListItem])
        if cached is not None:
            return cached
        companies, total = self.repository.search(query)
        page = Page(
            items=[self._company_list_item(company) for company in companies],
            page=query.page,
            page_size=query.page_size,
            total=total,
        )
        if self.cache is not None:
            self.cache.set_list(params, page.model_dump_json(), version=entry.version)
        return page

    def get_detail(self, company_id: UUID) -> CompanyDetail:
        cached = self._get_cached(
            lambda: self.cache.get_detail(company_id) if self.cache is not None else None,
            CompanyDetail,
        )
        if cached is not None:
            return cached
        company = self.repository.get_detail(company_id)
        if company is None:
            raise CompanyNotFoundError

        sources = [
            self._source_summary(company_source, document)
            for company_source, document in company._loaded_sources  # type: ignore[attr-defined]
        ]
        detail = CompanyDetail(
            **self._company_fields(company),
            aliases=[
                alias.alias
                for alias in company._loaded_aliases  # type: ignore[attr-defined]
            ],
            filings=[
                FilingItem(
                    filing_type=filing.filing_type,
                    filing_number=filing.filing_number,
                    filing_name=filing.filing_name,
                    filing_authority=filing.filing_authority,
                    filing_date=filing.filing_date,
                    filing_status=filing.filing_status,
                    detail_url=filing.detail_url,
                )
                for filing in company._loaded_filings  # type: ignore[attr-defined]
            ],
            sources=sources,
            job_count=company._loaded_job_count,  # type: ignore[attr-defined]
        )
        if self.cache is not None:
            self.cache.set_detail(company_id, detail.model_dump_json())
        return detail

    def list_jobs(self, company_id: UUID, query: JobQuery) -> Page[JobListItem]:
        if not self.repository.company_exists(company_id):
            raise CompanyNotFoundError
        params = query.model_dump(mode="json")
        cached = self._get_cached(
            lambda: self.cache.get_jobs(company_id, params) if self.cache is not None else None,
            Page[JobListItem],
        )
        if cached is not None:
            return cached
        jobs, total = self.repository.list_jobs(company_id, query)
        page = Page(
            items=[self._job_list_item(job) for job in jobs],
            page=query.page,
            page_size=query.page_size,
            total=total,
        )
        if self.cache is not None:
            self.cache.set_jobs(company_id, params, page.model_dump_json())
        return page

    @staticmethod
    def _get_cached(getter: Any, response_type: type[Any]) -> Any | None:
        return CompanyService._deserialize_cached(getter(), response_type)

    @staticmethod
    def _deserialize_cached(value: str | None, response_type: type[Any]) -> Any | None:
        try:
            return response_type.model_validate_json(value) if value is not None else None
        except (TypeError, ValidationError, ValueError):
            return None

    @staticmethod
    def _company_fields(company: Company) -> dict[str, Any]:
        return {
            "id": company.id,
            "canonical_name": company.canonical_name,
            "industry": company.industry,
            "sub_industry": company.sub_industry,
            "funding_stage": company.funding_stage,
            "scale": company.scale,
            "city": company.city,
            "logo_url": company.logo_url,
            "website": company.website,
            "description": company.description,
            "last_collected_at": company.last_collected_at,
            "created_at": company.created_at,
            "updated_at": company.updated_at,
        }

    @classmethod
    def _company_list_item(cls, company: Company) -> CompanyListItem:
        return CompanyListItem(**cls._company_fields(company))

    @staticmethod
    def _source_summary(
        company_source: CompanySource, document: SourceDocument
    ) -> CompanySourceSummary:
        return CompanySourceSummary(
            provider=document.provider,
            url=document.url,
            title=document.title,
            covered_fields=company_source.covered_fields,
            confidence=company_source.confidence,
            published_at=document.published_at,
            fetched_at=document.fetched_at,
        )

    @staticmethod
    def _job_list_item(job: JobPosting) -> JobListItem:
        return JobListItem(
            id=job.id,
            company_id=job.company_id,
            title=job.title,
            job_type=job.job_type,
            city=job.city,
            salary_min_monthly=job.salary_min_monthly,
            salary_max_monthly=job.salary_max_monthly,
            salary_months=job.salary_months,
            description=job.description,
            posted_at=job.posted_at,
            is_active=job.is_active,
            sources=[
                JobSourceItem(provider=source.provider, apply_url=source.apply_url)
                for source in job._loaded_sources  # type: ignore[attr-defined]
            ],
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

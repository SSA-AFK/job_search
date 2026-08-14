from datetime import UTC, date, datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import ValidationError

from app.cache.base import CompanyCache, ListCacheEntry
from app.companies.repository import CompanyRepository
from app.companies.schemas import (
    CompanyDetail,
    CompanyListItem,
    CompanyProfileFieldItem,
    CompanyQuery,
    CompanySourceSummary,
    FilingItem,
    FundingEventItem,
    JobListItem,
    JobQuery,
    JobSourceItem,
    Page,
    RankingComponentsItem,
    RankingSignalItem,
    RecruitingCoverageItem,
)
from app.core.errors import CompanyNotFoundError
from app.models import Company, CompanySource, JobPosting, SourceDocument, VerificationStatus
from app.rankings.public_service import ranking_reason
from app.rankings.repository import PublishedRankingRow
from app.recruiting_coverage.service import RecruitingCoverageService


class CompanyService:
    def __init__(
        self,
        repository: CompanyRepository,
        *,
        cache: CompanyCache | None = None,
        job_total_limit: int | None = None,
    ) -> None:
        self.repository = repository
        self.cache = cache
        self.job_total_limit = job_total_limit

    def search(self, query: CompanyQuery) -> Page[CompanyListItem]:
        params = query.model_dump(mode="json")
        entry = self.cache.get_list(params) if self.cache is not None else ListCacheEntry(None, None)
        cached = self._deserialize_cached(entry.value, Page[CompanyListItem])
        if cached is not None:
            return cached
        companies, total = self.repository.search(query)
        opportunity_counts = self.repository.early_career_counts([company.id for company in companies])
        active_counts = self.repository.active_job_counts([company.id for company in companies])
        page = Page(
            items=[self._company_list_item(company, opportunity_counts.get(company.id, (0, 0)), active_counts.get(company.id, 0)) for company in companies],
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
        signal_funding_events = self._signal_funding_events(
            company._loaded_ranking_signals  # type: ignore[attr-defined]
        )
        funding_events = [
            FundingEventItem(
                round_label=event.round_label,
                announced_at=event.announced_at,
                amount=event.amount,
                currency=event.currency,
                investors=company._loaded_funding_investors[event.id],  # type: ignore[attr-defined]
                verification_status=event.verification_status,
            )
            for event in company._loaded_funding_events  # type: ignore[attr-defined]
        ]
        known_funding = {(event.round_label, event.announced_at) for event in funding_events}
        funding_events.extend(
            event
            for event in signal_funding_events
            if (event.round_label, event.announced_at) not in known_funding
        )
        funding_events.sort(key=lambda event: event.announced_at or date.min, reverse=True)
        detail = CompanyDetail(
            **self._company_fields(company),
            aliases=[
                alias.alias
                for alias in company._loaded_aliases  # type: ignore[attr-defined]
            ],
            headquarters=company.headquarters,
            founded_year=company.founded_year,
            established_at=company.established_at,
            province=company.province,
            district=company.district,
            company_type=company.company_type,
            registered_capital=company.registered_capital,
            paid_in_capital=company.paid_in_capital,
            industry_sector=company.industry_sector,
            industry_middle=company.industry_middle,
            insured_employee_count=company.insured_employee_count,
            employee_report_year=company.employee_report_year,
            business_scope=company.business_scope,
            latest_funding_round=funding_events[0].round_label if funding_events else None,
            filings=[
                FilingItem(
                    filing_type=filing.filing_type,
                    filing_number=filing.filing_number,
                    filing_name=filing.filing_name,
                    filing_authority=filing.filing_authority,
                    filing_date=filing.filing_date,
                    filing_status=filing.filing_status,
                    verification_status=filing.verification_status,
                    detail_url=filing.detail_url,
                )
                for filing in company._loaded_filings  # type: ignore[attr-defined]
            ],
            sources=sources,
            profile_fields=[
                CompanyProfileFieldItem(
                    field_key=field.field_key,
                    value=field.value,
                    verification_status=field.verification_status,
                    collected_at=field.collected_at,
                )
                for field in company._loaded_profile_fields  # type: ignore[attr-defined]
            ],
            funding_events=funding_events,
            job_count=company._loaded_job_count,  # type: ignore[attr-defined]
            recruiting_coverage=self._recruiting_coverage_item(company.id),
            ranking_rule_version=company._loaded_ranking_snapshot.rule_version,  # type: ignore[attr-defined]
            ranking_calculated_at=company._loaded_ranking_snapshot.calculated_at,  # type: ignore[attr-defined]
            ranking_components=RankingComponentsItem.model_validate(
                company._loaded_ranking_snapshot.component_scores  # type: ignore[attr-defined]
            ),
            ranking_reason=ranking_reason(
                PublishedRankingRow(
                    company,
                    company._loaded_ranking_snapshot,  # type: ignore[attr-defined]
                    company._loaded_ranking_rank,  # type: ignore[attr-defined]
                )
            ),
            ranking_missing_fields=company._loaded_ranking_snapshot.missing_fields,  # type: ignore[attr-defined]
            ranking_signals=[
                RankingSignalItem(
                    category=signal.category,
                    signal_key=signal.signal_key,
                    value=signal.value,
                    event_date=signal.event_date.date() if signal.event_date else None,
                )
                for signal in company._loaded_ranking_signals  # type: ignore[attr-defined]
                if signal.category
                in {
                    "ai_relevance",
                    "growth",
                    "intellectual_property",
                    "market_validation",
                    "material_risk",
                }
            ],
            **self._opportunity_fields(company.id),
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
        jobs, total = self.repository.list_jobs(
            company_id, query, total_limit=self.job_total_limit
        )
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
        snapshot = company._loaded_ranking_snapshot  # type: ignore[attr-defined]
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
            "ranking_status": "ranked" if snapshot.is_eligible else "observation",
            "rank": company._loaded_ranking_rank,  # type: ignore[attr-defined]
            "ranking_score": int(snapshot.total_score),
            "company_stage": snapshot.company_stage or "growth",
        }

    def _company_list_item(self, company: Company, counts: tuple[int, int] = (0, 0), active_count: int = 0) -> CompanyListItem:
        return CompanyListItem(
            **self._company_fields(company),
            campus_job_count=counts[0],
            internship_job_count=counts[1],
            active_job_count=active_count,
            recruiting_coverage=self._recruiting_coverage_item(company.id),
        )

    def _opportunity_fields(self, company_id: UUID) -> dict[str, int]:
        campus, internship = self.repository.early_career_counts([company_id]).get(company_id, (0, 0))
        active = self.repository.active_job_counts([company_id]).get(company_id, 0)
        return {"campus_job_count": campus, "internship_job_count": internship, "active_job_count": active}

    def _recruiting_coverage_item(self, company_id: object) -> RecruitingCoverageItem:
        coverage = RecruitingCoverageService(self.repository.session).build(
            company_id, now=datetime.now(UTC)
        )
        return RecruitingCoverageItem(
            status=coverage.status,
            active_job_count=coverage.active_job_count,
            last_checked_at=coverage.last_checked_at,
            last_successful_at=coverage.last_successful_at,
            freshness=cast(Literal["fresh", "stale", "unknown"], coverage.freshness),
            reason_code=coverage.reason_code,
            primary_entry_url=coverage.primary_entry_url,
            primary_entry_platform=coverage.primary_entry_platform,
        )

    @staticmethod
    def _source_summary(
        company_source: CompanySource, document: SourceDocument
    ) -> CompanySourceSummary:
        return CompanySourceSummary(
            provider=document.provider,
            url=document.url,
            title=document.title,
            covered_fields=company_source.covered_fields,
            field_verification={key: VerificationStatus(value) for key, value in company_source.field_verification.items()},
            confidence=company_source.confidence,
            published_at=document.published_at,
            fetched_at=document.fetched_at,
        )

    @staticmethod
    def _signal_funding_events(signals: list[Any]) -> list[FundingEventItem]:
        events: list[FundingEventItem] = []
        for signal in signals:
            if signal.signal_key != "financing" or signal.verification_status != "internal_verified":
                continue
            round_label = signal.value.get("round")
            if not isinstance(round_label, str) or not round_label.strip() or round_label == "出资设立":
                continue
            investors = signal.value.get("investors", [])
            events.append(
                FundingEventItem(
                    round_label=round_label.strip(),
                    announced_at=signal.event_date.date() if signal.event_date else None,
                    amount=None,
                    currency=None,
                    investors=[value for value in investors if isinstance(value, str)]
                    if isinstance(investors, list)
                    else [],
                    verification_status=VerificationStatus.VERIFIED,
                )
            )
        return events

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
                JobSourceItem(
                    provider=source.provider,
                    apply_url=source.apply_url,
                    verification_status=source.verification_status,
                )
                for source in job._loaded_sources  # type: ignore[attr-defined]
            ],
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

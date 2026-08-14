from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CompanyScale,
    FilingType,
    FundingStage,
    JobType,
    RecruitingStatus,
    VerificationStatus,
)


class CompanySort(StrEnum):
    RELEVANCE = "relevance"
    NAME = "name"
    UPDATED_AT = "updated_at"


class PageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=100)] = 20


class CompanyQuery(PageQuery):
    q: Annotated[str | None, Field(max_length=255)] = None
    industry: Annotated[str | None, Field(max_length=100)] = None
    sub_industry: Annotated[str | None, Field(max_length=100)] = None
    funding_stage: FundingStage | None = None
    scale: CompanyScale | None = None
    city: Annotated[str | None, Field(max_length=50)] = None
    sort: CompanySort | None = None

    @property
    def resolved_sort(self) -> CompanySort:
        if self.sort is not None:
            return self.sort
        return CompanySort.RELEVANCE if self.q and self.q.strip() else CompanySort.UPDATED_AT


class JobQuery(PageQuery):
    job_type: JobType | None = None
    city: Annotated[str | None, Field(max_length=50)] = None
    active_only: bool = True


class CompanyListItem(BaseModel):
    id: UUID
    canonical_name: str
    industry: str | None
    sub_industry: str | None
    funding_stage: FundingStage
    scale: CompanyScale
    city: str | None
    logo_url: str | None
    website: str | None
    description: str | None
    last_collected_at: datetime | None
    created_at: datetime
    updated_at: datetime
    recruiting_coverage: "RecruitingCoverageItem"
    ranking_status: Literal["ranked", "observation"]
    rank: int | None
    ranking_score: int
    company_stage: Literal["early", "growth", "mature"]
    active_job_count: int = 0
    campus_job_count: int = 0
    internship_job_count: int = 0


class RecruitingCoverageItem(BaseModel):
    status: RecruitingStatus
    active_job_count: int | None
    last_checked_at: datetime | None
    last_successful_at: datetime | None
    freshness: Literal["fresh", "stale", "unknown"]
    reason_code: str | None
    primary_entry_url: str | None
    primary_entry_platform: str | None


class FilingItem(BaseModel):
    filing_type: FilingType
    filing_number: str
    filing_name: str
    filing_authority: str | None
    filing_date: date | None
    filing_status: str | None
    verification_status: VerificationStatus
    detail_url: str | None


class CompanySourceSummary(BaseModel):
    provider: str
    url: str
    title: str | None
    covered_fields: list[str]
    field_verification: dict[str, VerificationStatus]
    confidence: Decimal
    published_at: datetime | None
    fetched_at: datetime


class CompanyProfileFieldItem(BaseModel):
    field_key: str
    value: object
    verification_status: VerificationStatus
    collected_at: datetime


class FundingEventItem(BaseModel):
    round_label: str
    announced_at: date | None
    amount: Decimal | None
    currency: str | None
    investors: list[str]
    verification_status: VerificationStatus


class RankingComponentsItem(BaseModel):
    ai_core: int
    market_validation: int
    growth_momentum: int
    industry_influence: int
    reliability: int


class RankingSignalItem(BaseModel):
    category: Literal[
        "ai_relevance",
        "growth",
        "intellectual_property",
        "market_validation",
        "material_risk",
    ]
    signal_key: Literal[
        "ai_business_scope",
        "financing",
        "ai_invention_patent",
        "ai_software_copyright",
        "winning_bid",
        "active_qualification",
        "material_risk",
    ]
    value: dict[str, object]
    event_date: date | None


class CompanyDetail(CompanyListItem):
    aliases: list[str]
    headquarters: str | None
    founded_year: int | None
    established_at: date | None
    province: str | None
    district: str | None
    company_type: str | None
    registered_capital: str | None
    paid_in_capital: str | None
    industry_sector: str | None
    industry_middle: str | None
    insured_employee_count: int | None
    employee_report_year: int | None
    business_scope: str | None
    latest_funding_round: str | None
    filings: list[FilingItem]
    sources: list[CompanySourceSummary]
    profile_fields: list[CompanyProfileFieldItem]
    funding_events: list[FundingEventItem]
    job_count: int
    ranking_rule_version: str
    ranking_calculated_at: datetime
    ranking_components: RankingComponentsItem
    ranking_reason: str
    ranking_missing_fields: list[str]
    ranking_signals: list[RankingSignalItem]


class JobSourceItem(BaseModel):
    provider: str
    apply_url: str
    verification_status: VerificationStatus


class JobListItem(BaseModel):
    id: UUID
    company_id: UUID
    title: str
    job_type: JobType
    city: str
    salary_min_monthly: int | None
    salary_max_monthly: int | None
    salary_months: int | None
    description: str
    posted_at: date | None
    is_active: bool
    sources: list[JobSourceItem]
    created_at: datetime
    updated_at: datetime


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FilingType, JobType


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
    funding_stage: Annotated[str | None, Field(max_length=50)] = None
    scale: Annotated[str | None, Field(max_length=50)] = None
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
    funding_stage: str
    scale: str
    city: str | None
    logo_url: str | None
    website: str | None
    description: str | None
    last_collected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FilingItem(BaseModel):
    filing_type: FilingType
    filing_number: str
    filing_name: str
    filing_authority: str | None
    filing_date: date | None
    filing_status: str | None
    detail_url: str | None


class CompanySourceSummary(BaseModel):
    provider: str
    url: str
    title: str | None
    covered_fields: list[str]
    confidence: Decimal
    published_at: datetime | None
    fetched_at: datetime


class CompanyDetail(CompanyListItem):
    aliases: list[str]
    filings: list[FilingItem]
    sources: list[CompanySourceSummary]
    job_count: int


class JobSourceItem(BaseModel):
    provider: str
    apply_url: str


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

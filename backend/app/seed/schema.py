from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.core.normalization import normalize_url
from app.models.enums import CompanyScale, FilingType, FundingStage, JobType

NonEmptyText = Annotated[str, Field(min_length=1)]
CompanyUrl = Annotated[str, Field(min_length=1, max_length=1000)]
ExternalUrl = Annotated[str, Field(min_length=1, max_length=2000)]


class StrictSeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeedJobSource(StrictSeedModel):
    provider: NonEmptyText
    source_raw_id: NonEmptyText
    apply_url: ExternalUrl
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    is_active: bool = True

    @field_validator("apply_url", mode="before")
    @classmethod
    def validate_apply_url(cls, value: object) -> object:
        return normalize_url(value) if isinstance(value, str) else value

    @field_validator("first_seen_at", "last_seen_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class SeedJob(StrictSeedModel):
    title: NonEmptyText
    job_type: JobType = JobType.UNKNOWN
    city: NonEmptyText
    salary_min_monthly: int | None = Field(default=None, ge=0)
    salary_max_monthly: int | None = Field(default=None, ge=0)
    salary_months: int | None = Field(default=None, ge=1)
    description: str
    posted_at: date | None = None
    is_active: bool = True
    sources: list[SeedJobSource] = Field(min_length=1)


class SeedFiling(StrictSeedModel):
    filing_type: FilingType
    filing_number: NonEmptyText
    filing_name: NonEmptyText
    filing_authority: str | None = None
    filing_date: date | None = None
    filing_status: str | None = None
    detail_url: ExternalUrl | None = None

    @field_validator("detail_url", mode="before")
    @classmethod
    def validate_detail_url(cls, value: object) -> object:
        return normalize_url(value) if isinstance(value, str) else value


class SeedCompany(StrictSeedModel):
    canonical_name: NonEmptyText
    aliases: list[NonEmptyText] = Field(default_factory=list)
    industry: str | None = None
    sub_industry: str | None = None
    funding_stage: FundingStage = FundingStage.UNKNOWN
    scale: CompanyScale = CompanyScale.UNKNOWN
    city: str | None = None
    logo_url: CompanyUrl | None = None
    website: CompanyUrl | None = None
    description: str | None = None
    jobs: list[SeedJob] = Field(default_factory=list)
    filings: list[SeedFiling] = Field(default_factory=list)

    @field_validator("logo_url", "website", mode="before")
    @classmethod
    def validate_company_url(cls, value: object) -> object:
        return normalize_url(value) if isinstance(value, str) else value


class SeedPayload(StrictSeedModel):
    version: Literal[1]
    companies: list[SeedCompany] = Field(min_length=1)

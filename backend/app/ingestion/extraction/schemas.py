"""Validated data returned from untrusted model output."""

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
)

from app.ingestion.contracts import require_statically_public_url


def _bounded_external_url(value: HttpUrl) -> HttpUrl:
    if len(str(value)) > 2_000:
        raise ValueError("URL must not exceed 2000 characters")
    return value


ExternalUrl = Annotated[
    HttpUrl,
    AfterValidator(_bounded_external_url),
    AfterValidator(require_statically_public_url),
]


def _bounded_company_url(value: HttpUrl) -> HttpUrl:
    if len(str(value)) > 1_000:
        raise ValueError("URL must not exceed 1000 characters")
    return value


CompanyUrl = Annotated[
    HttpUrl,
    AfterValidator(_bounded_company_url),
    AfterValidator(require_statically_public_url),
]


class FrozenExtractionModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"


class FilingType(StrEnum):
    ICP = "icp"
    ALGORITHM = "algorithm"
    BUSINESS_LICENSE = "business_license"


class FundingStage(StrEnum):
    SEED = "seed"
    ANGEL = "angel"
    PRE_A = "pre_a"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C_PLUS = "series_c_plus"
    PUBLIC = "public"
    UNFUNDED = "unfunded"
    UNKNOWN = "unknown"


class CompanyScale(StrEnum):
    ONE_TO_49 = "one_to_49"
    FIFTY_TO_199 = "50_to_199"
    TWO_HUNDRED_TO_499 = "200_to_499"
    FIVE_HUNDRED_PLUS = "500_plus"
    UNKNOWN = "unknown"


def _coerce_funding_stage(v: object) -> object:
    if isinstance(v, str):
        v_lower = v.lower().strip()
        for member in FundingStage:
            if v_lower == member.value:
                return member
        return FundingStage.UNKNOWN
    return v


def _coerce_company_scale(v: object) -> object:
    if isinstance(v, str):
        v_lower = v.lower().strip()
        for member in CompanyScale:
            if v_lower == member.value:
                return member
        return CompanyScale.UNKNOWN
    return v


class EvidenceCandidate(FrozenExtractionModel):
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def coerce_evidence_ids_to_str(cls, v: object) -> object:
        if isinstance(v, (list, tuple)):
            result = []
            for item in v:
                if not isinstance(item, str):
                    item = str(item)
                item = item.removeprefix("evidence:")
                result.append(item)
            return result
        return v

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_must_be_provided(
        cls, evidence_ids: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        allowed = (info.context or {}).get("allowed_evidence_ids")
        if allowed is not None and not set(evidence_ids).issubset(allowed):
            raise ValueError("evidence_ids must be supplied in the prompt")
        return evidence_ids


class CompanyRef(FrozenExtractionModel):
    name: str = Field(min_length=1, max_length=200)
    website: CompanyUrl | None = None

    @field_validator("website", mode="before")
    @classmethod
    def normalize_website(cls, v: object) -> object:
        if isinstance(v, str) and v and not v.startswith(("http://", "https://")):
            return f"https://{v}"
        return v


class CompanyCandidate(CompanyRef, EvidenceCandidate):
    aliases: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(
        default_factory=tuple, max_length=20
    )
    description: str | None = Field(default=None, max_length=4_000)
    city: str | None = Field(default=None, max_length=50)
    industry: str | None = Field(default=None, max_length=100)
    sub_industry: str | None = Field(default=None, max_length=100)
    funding_stage: FundingStage = Field(default=FundingStage.UNKNOWN)
    scale: CompanyScale = Field(default=CompanyScale.UNKNOWN)
    career_page_url: ExternalUrl | None = None
    
    @field_validator("career_page_url", mode="before")
    @classmethod
    def normalize_career_page_url(cls, v: object) -> object:
        if isinstance(v, str) and v and not v.startswith(("http://", "https://")):
            return f"https://{v}"
        return v

    @field_validator("aliases", mode="before")
    @classmethod
    def coerce_aliases(cls, v: object) -> object:
        if v is None:
            return ()
        return v

    @field_validator("funding_stage", mode="before")
    @classmethod
    def coerce_funding_stage(cls, v: object) -> object:
        return _coerce_funding_stage(v)

    @field_validator("scale", mode="before")
    @classmethod
    def coerce_scale(cls, v: object) -> object:
        return _coerce_company_scale(v)

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        if description is not None and ("<" in description or ">" in description):
            raise ValueError("description must be plain text")
        return description


class CompanyProfileCandidate(CompanyRef, EvidenceCandidate):
    description: str | None = Field(default=None, max_length=4_000)
    headquarters: str | None = Field(default=None, max_length=300)
    founded_year: int | None = Field(default=None, ge=1000, le=9999)
    city: str | None = Field(default=None, max_length=50)
    industry: str | None = Field(default=None, max_length=100)
    sub_industry: str | None = Field(default=None, max_length=100)
    funding_stage: FundingStage = Field(default=FundingStage.UNKNOWN)
    scale: CompanyScale = Field(default=CompanyScale.UNKNOWN)

    @field_validator("funding_stage", mode="before")
    @classmethod
    def coerce_funding_stage(cls, v: object) -> object:
        return _coerce_funding_stage(v)

    @field_validator("scale", mode="before")
    @classmethod
    def coerce_scale(cls, v: object) -> object:
        return _coerce_company_scale(v)

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        return CompanyCandidate.description_must_not_contain_html(description)


class JobCandidate(EvidenceCandidate):
    company_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=255)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=50)
    provider: str | None = Field(default=None, min_length=1, max_length=50)
    source_raw_id: str | None = Field(default=None, min_length=1, max_length=255)
    source_evidence_id: str | None = Field(default=None, min_length=1, max_length=255)
    apply_url: ExternalUrl | None = None
    posted_at: date | None = None
    salary: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        return CompanyCandidate.description_must_not_contain_html(description)

    @field_validator("source_evidence_id", mode="before")
    @classmethod
    def coerce_source_evidence_id_to_str(cls, v: object) -> object:
        return str(v) if isinstance(v, int) else v

    @field_validator("source_evidence_id")
    @classmethod
    def source_evidence_must_be_cited(
        cls, source_evidence_id: str | None, info: ValidationInfo
    ) -> str | None:
        if source_evidence_id is None:
            return None
        evidence_ids = info.data.get("evidence_ids", ())
        if source_evidence_id not in evidence_ids:
            raise ValueError("source_evidence_id must be one of evidence_ids")
        allowed = (info.context or {}).get("allowed_evidence_ids")
        if allowed is not None and source_evidence_id not in allowed:
            raise ValueError("source_evidence_id must be supplied in the prompt")
        return source_evidence_id


class FilingCandidate(EvidenceCandidate):
    title: str = Field(min_length=1, max_length=255)
    filing_type: FilingType
    filing_number: str | None = Field(default=None, max_length=255)
    filing_authority: str | None = Field(default=None, max_length=255)
    filing_date: date | None = None
    filing_status: str | None = Field(default=None, max_length=50)

    @field_validator("filing_type", mode="before")
    @classmethod
    def coerce_filing_type(cls, v: object) -> object:
        if isinstance(v, str):
            v_lower = v.lower().strip()
            for member in FilingType:
                if v_lower == member.value:
                    return member
            return FilingType.BUSINESS_LICENSE
        return v

    @field_validator("filing_date", mode="before")
    @classmethod
    def normalize_filing_date(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            parts = v.split("-")
            if len(parts) == 1:
                return f"{parts[0]}-01-01"
            if len(parts) == 2:
                return f"{parts[0]}-{parts[1]}-01"
        return v
    url: ExternalUrl | None = None
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        return CompanyCandidate.description_must_not_contain_html(description)


class ProfileExtraction(FrozenExtractionModel):
    profile: CompanyProfileCandidate
    filings: tuple[FilingCandidate, ...] = Field(default_factory=tuple, max_length=100)


def _confidence_of(item: object) -> float:
    if isinstance(item, dict):
        value = item.get("confidence", 0)
        return float(value) if isinstance(value, (int, float)) else 0.0
    return 0.0


def _bound_list(v: object, limit: int) -> object:
    """Keep the highest-confidence entries when LLM emits too many items."""
    if isinstance(v, list) and len(v) > limit:
        return sorted(v, key=_confidence_of, reverse=True)[:limit]
    return v


class ExtractionBatch(FrozenExtractionModel):
    companies: tuple[CompanyCandidate, ...] = Field(default_factory=tuple, max_length=50)
    profiles: tuple[CompanyProfileCandidate, ...] = Field(default_factory=tuple, max_length=10)
    jobs: tuple[JobCandidate, ...] = Field(default_factory=tuple, max_length=200)
    filings: tuple[FilingCandidate, ...] = Field(default_factory=tuple, max_length=100)

    @field_validator("companies", mode="before")
    @classmethod
    def bound_companies(cls, v: object) -> object:
        return _bound_list(v, 50)

    @field_validator("jobs", mode="before")
    @classmethod
    def bound_jobs(cls, v: object) -> object:
        return _bound_list(v, 200)

    @field_validator("filings", mode="before")
    @classmethod
    def bound_filings(cls, v: object) -> object:
        return _bound_list(v, 100)

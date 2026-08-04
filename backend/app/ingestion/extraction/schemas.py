"""Validated data returned from untrusted model output."""

from datetime import date
from enum import StrEnum
from ipaddress import ip_address
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


def _bounded_external_url(value: HttpUrl) -> HttpUrl:
    if len(str(value)) > 2_000:
        raise ValueError("URL must not exceed 2000 characters")
    return value


def _require_statically_public_url(value: HttpUrl) -> HttpUrl:
    host = (value.host or "").lower().rstrip(".")
    if value.username is not None or value.password is not None:
        raise ValueError("URL must be a public URL without credentials")
    if not host or host == "localhost" or host.endswith((".localhost", ".local")):
        raise ValueError("URL must be a public URL")
    literal_host = host.removeprefix("[").removesuffix("]")
    try:
        address = ip_address(literal_host)
    except ValueError:
        if "." not in host:
            raise ValueError("URL must be a public URL") from None
    else:
        if not address.is_global:
            raise ValueError("URL must be a public URL")
    return value


ExternalUrl = Annotated[
    HttpUrl,
    AfterValidator(_bounded_external_url),
    AfterValidator(_require_statically_public_url),
]


def _bounded_company_url(value: HttpUrl) -> HttpUrl:
    if len(str(value)) > 1_000:
        raise ValueError("URL must not exceed 1000 characters")
    return value


CompanyUrl = Annotated[
    HttpUrl,
    AfterValidator(_bounded_company_url),
    AfterValidator(_require_statically_public_url),
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


class EvidenceCandidate(FrozenExtractionModel):
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)

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


class CompanyCandidate(CompanyRef, EvidenceCandidate):
    description: str | None = Field(default=None, max_length=4_000)

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

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        return CompanyCandidate.description_must_not_contain_html(description)


class JobCandidate(EvidenceCandidate):
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
    filing_number: str = Field(min_length=1, max_length=255)
    filing_authority: str | None = Field(default=None, max_length=255)
    filing_date: date | None = None
    filing_status: str | None = Field(default=None, max_length=50)
    url: ExternalUrl | None = None
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        return CompanyCandidate.description_must_not_contain_html(description)


class ExtractionBatch(FrozenExtractionModel):
    companies: tuple[CompanyCandidate, ...] = Field(default_factory=tuple, max_length=50)
    profiles: tuple[CompanyProfileCandidate, ...] = Field(default_factory=tuple, max_length=10)
    jobs: tuple[JobCandidate, ...] = Field(default_factory=tuple, max_length=200)
    filings: tuple[FilingCandidate, ...] = Field(default_factory=tuple, max_length=100)

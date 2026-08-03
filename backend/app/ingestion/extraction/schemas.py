"""Validated data returned from untrusted model output."""

from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, ValidationInfo, field_validator


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"


class FilingType(StrEnum):
    ANNUAL_REPORT = "annual_report"
    REGULATORY_FILING = "regulatory_filing"
    PRESS_RELEASE = "press_release"


class EvidenceCandidate(BaseModel):
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_must_be_provided(
        cls, evidence_ids: list[str], info: ValidationInfo
    ) -> list[str]:
        allowed = (info.context or {}).get("allowed_evidence_ids")
        if allowed is not None and not set(evidence_ids).issubset(allowed):
            raise ValueError("evidence_ids must be supplied in the prompt")
        return evidence_ids


class CompanyRef(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website: HttpUrl | None = None


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
    title: str = Field(min_length=1, max_length=300)
    employment_type: EmploymentType | None = None
    location: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        return CompanyCandidate.description_must_not_contain_html(description)


class FilingCandidate(EvidenceCandidate):
    title: str = Field(min_length=1, max_length=300)
    filing_type: FilingType
    url: HttpUrl | None = None
    description: str | None = Field(default=None, max_length=4_000)

    @field_validator("description")
    @classmethod
    def description_must_not_contain_html(cls, description: str | None) -> str | None:
        return CompanyCandidate.description_must_not_contain_html(description)


class ExtractionBatch(BaseModel):
    companies: list[CompanyCandidate] = Field(default_factory=list, max_length=50)
    profiles: list[CompanyProfileCandidate] = Field(default_factory=list, max_length=10)
    jobs: list[JobCandidate] = Field(default_factory=list, max_length=200)
    filings: list[FilingCandidate] = Field(default_factory=list, max_length=100)

"""Validated DTOs at the normalization-to-persistence boundary."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import FilingCandidate
from app.ingestion.normalization.company import NormalizedCompanyCandidate
from app.ingestion.normalization.job import NormalizedJobCandidate
from app.models.enums import FilingType

EvidenceId = Annotated[str, Field(min_length=1, max_length=255)]


def _bounded_external_url(value: HttpUrl) -> HttpUrl:
    if len(str(value)) > 2_000:
        raise ValueError("URL must not exceed 2000 characters")
    return value


ExternalUrl = Annotated[HttpUrl, AfterValidator(_bounded_external_url)]
CompanyFieldName = Literal[
    "canonical_name",
    "industry",
    "sub_industry",
    "funding_stage",
    "scale",
    "city",
    "logo_url",
    "website",
    "description",
]
_MAX_SQL_INTEGER = 2_147_483_647
_MAX_SQL_SMALLINT = 32_767


class FrozenDTO(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


class NormalizedDocument(FrozenDTO):
    evidence_id: EvidenceId
    document: RawDocument
    fetched_at: AwareDatetime


class CompanyFieldEvidence(FrozenDTO):
    field_name: CompanyFieldName
    evidence_id: EvidenceId
    confidence: float = Field(ge=0, le=1)


class NormalizedCompanyRecord(FrozenDTO):
    candidate: NormalizedCompanyCandidate
    company_id: UUID | None
    field_evidence: tuple[CompanyFieldEvidence, ...] = ()


class NormalizedJobRecord(FrozenDTO):
    candidate: NormalizedJobCandidate
    job_posting_id: UUID | None
    source_evidence_id: EvidenceId | None
    apply_url: ExternalUrl
    posted_at: date | None
    seen_at: AwareDatetime
    is_active: bool = True

    @model_validator(mode="after")
    def validate_database_lengths(self) -> "NormalizedJobRecord":
        if len(self.candidate.normalized_title) > 255:
            raise ValueError("normalized job title exceeds 255 characters")
        if len(self.candidate.normalized_city) > 50:
            raise ValueError("normalized job city exceeds 50 characters")
        salary_bounds = (
            self.candidate.salary_minimum_monthly,
            self.candidate.salary_maximum_monthly,
        )
        if any(
            value is not None and not 0 <= value <= _MAX_SQL_INTEGER
            for value in salary_bounds
        ):
            raise ValueError("normalized salary exceeds database integer domain")
        salary_months = self.candidate.salary_months
        if salary_months is not None and not 1 <= salary_months <= _MAX_SQL_SMALLINT:
            raise ValueError("normalized salary months exceed database domain")
        return self


class NormalizedFilingRecord(FrozenDTO):
    filing_type: FilingType
    filing_number: str = Field(min_length=1, max_length=255)
    filing_name: str = Field(min_length=1, max_length=255)
    filing_authority: str | None = Field(default=None, max_length=255)
    filing_date: date | None = None
    filing_status: str | None = Field(default=None, max_length=50)
    detail_url: ExternalUrl | None = None
    source_evidence_id: EvidenceId | None = None

    @classmethod
    def from_candidate(
        cls, candidate: FilingCandidate, *, source_evidence_id: str | None
    ) -> "NormalizedFilingRecord":
        return cls(
            filing_type=FilingType(candidate.filing_type.value),
            filing_number=candidate.filing_number,
            filing_name=candidate.title,
            filing_authority=candidate.filing_authority,
            filing_date=candidate.filing_date,
            filing_status=candidate.filing_status,
            detail_url=candidate.url,
            source_evidence_id=source_evidence_id,
        )


class NormalizedBatch(FrozenDTO):
    documents: tuple[NormalizedDocument, ...]
    company: NormalizedCompanyRecord
    jobs: tuple[NormalizedJobRecord, ...] = ()
    filings: tuple[NormalizedFilingRecord, ...] = ()
    collected_at: AwareDatetime

    @model_validator(mode="after")
    def validate_references(self) -> "NormalizedBatch":
        evidence_ids = [item.evidence_id for item in self.documents]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate evidence_id in documents")
        known_evidence = set(evidence_ids)

        references = [
            *(item.evidence_id for item in self.company.field_evidence),
            *self.company.candidate.candidate.evidence_ids,
            *(evidence_id for job in self.jobs for evidence_id in job.candidate.candidate.evidence_ids),
            *(job.source_evidence_id for job in self.jobs if job.source_evidence_id is not None),
            *(
                filing.source_evidence_id
                for filing in self.filings
                if filing.source_evidence_id is not None
            ),
        ]
        unknown = sorted(set(references) - known_evidence)
        if unknown:
            raise ValueError(f"unknown evidence_id reference: {unknown[0]}")

        for job in self.jobs:
            source = job.candidate.candidate
            if source.provider is None or source.source_raw_id is None:
                raise ValueError("job provider and source_raw_id are required")
        return self

    def with_fetched_at(self, fetched_at: datetime) -> "NormalizedBatch":
        return NormalizedBatch(
            documents=tuple(
                NormalizedDocument(
                    evidence_id=item.evidence_id,
                    document=item.document,
                    fetched_at=fetched_at,
                )
                for item in self.documents
            ),
            company=self.company,
            jobs=tuple(
                NormalizedJobRecord(
                    candidate=item.candidate,
                    job_posting_id=item.job_posting_id,
                    source_evidence_id=item.source_evidence_id,
                    apply_url=item.apply_url,
                    posted_at=item.posted_at,
                    seen_at=fetched_at,
                    is_active=item.is_active,
                )
                for item in self.jobs
            ),
            filings=self.filings,
            collected_at=fetched_at,
        )

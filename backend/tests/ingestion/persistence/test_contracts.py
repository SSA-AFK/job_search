from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import (
    CompanyCandidate,
    FilingCandidate,
    JobCandidate,
)
from app.ingestion.normalization.company import normalize_company
from app.ingestion.normalization.job import normalize_job
from app.ingestion.persistence.contracts import (
    CompanyFieldEvidence,
    NormalizedBatch,
    NormalizedCompanyRecord,
    NormalizedDocument,
    NormalizedFilingRecord,
    NormalizedJobRecord,
    NormalizedProfileFieldRecord,
)
from app.models.enums import FilingType

NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)


def document(evidence_id: str = "doc-1") -> NormalizedDocument:
    return NormalizedDocument(
        evidence_id=evidence_id,
        document=RawDocument(
            provider="official",
            external_id=evidence_id,
            url="https://example.com/source",
            title="Source",
            text="Evidence text",
            published_at=None,
            authority_level=1,
        ),
        fetched_at=NOW,
    )


def company_record(
    *field_evidence: CompanyFieldEvidence,
) -> NormalizedCompanyRecord:
    return NormalizedCompanyRecord(
        candidate=normalize_company(
            CompanyCandidate(
                name="Example",
                website="https://example.com",
                description="Company description",
                evidence_ids=["doc-1"],
                confidence=0.9,
            )
        ),
        company_id=None,
        field_evidence=field_evidence,
    )


def test_batch_rejects_duplicate_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate evidence_id"):
        NormalizedBatch(
            documents=(document(), document()),
            company=company_record(),
            jobs=(),
            filings=(),
            collected_at=NOW,
        )


def test_batch_rejects_unresolved_company_field_evidence() -> None:
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        NormalizedBatch(
            documents=(document(),),
            company=company_record(
                CompanyFieldEvidence(
                    field_name="description", evidence_id="missing", confidence=0.8
                )
            ),
            jobs=(),
            filings=(),
            collected_at=NOW,
        )


def test_batch_rejects_unresolved_profile_field_evidence() -> None:
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        NormalizedBatch(
            documents=(document(),),
            company=company_record(),
            jobs=(),
            filings=(),
            profile_fields=(
                NormalizedProfileFieldRecord(
                    field_key="technology.github.stars_total",
                    value=123,
                    source_evidence_id="missing",
                ),
            ),
            collected_at=NOW,
        )


def test_company_field_evidence_restricts_persisted_fields() -> None:
    with pytest.raises(ValidationError):
        CompanyFieldEvidence(
            field_name="created_at", evidence_id="doc-1", confidence=0.8
        )


def test_batch_rejects_job_without_provider_source_identity() -> None:
    job = NormalizedJobRecord(
        candidate=normalize_job(
            JobCandidate(
                company_name="Example",
                title="Engineer",
                location="Shanghai",
                evidence_ids=["doc-1"],
                confidence=0.9,
            )
        ),
        job_posting_id=None,
        source_evidence_id="doc-1",
        apply_url="https://example.com/jobs/42",
        posted_at=date(2026, 7, 20),
        seen_at=NOW,
    )

    with pytest.raises(ValidationError, match="provider and source_raw_id"):
        NormalizedBatch(
            documents=(document(),),
            company=company_record(),
            jobs=(job,),
            filings=(),
            collected_at=NOW,
        )


def test_batch_with_fetched_at_updates_all_collection_times() -> None:
    later = datetime(2026, 8, 1, 12, tzinfo=UTC)
    job = NormalizedJobRecord(
        candidate=normalize_job(
            JobCandidate(
                company_name="Example",
                title="Engineer",
                location="Shanghai",
                provider="official",
                source_raw_id="job-42",
                evidence_ids=["doc-1"],
                confidence=0.9,
            )
        ),
        job_posting_id=None,
        source_evidence_id="doc-1",
        apply_url="https://example.com/jobs/42",
        posted_at=date(2026, 7, 20),
        seen_at=NOW,
    )
    batch = NormalizedBatch(
        documents=(document(),),
        company=company_record(),
        jobs=(job,),
        filings=(),
        collected_at=NOW,
    )

    updated = batch.with_fetched_at(later)

    assert updated.collected_at == later
    assert updated.documents[0].fetched_at == later
    assert updated.jobs[0].seen_at == later
    assert batch.collected_at == NOW


def test_batch_with_fetched_at_rejects_naive_datetime() -> None:
    batch = NormalizedBatch(
        documents=(document(),),
        company=company_record(),
        jobs=(),
        filings=(),
        collected_at=NOW,
    )

    with pytest.raises(ValidationError):
        batch.with_fetched_at(NOW.replace(tzinfo=None))


def test_normalized_filing_maps_extraction_vocabulary_and_fields() -> None:
    candidate = FilingCandidate(
        title="Example filing",
        filing_type="business_license",
        filing_number="LICENSE-42",
        filing_authority="Registry",
        filing_date=date(2026, 7, 1),
        filing_status="active",
        url="https://example.com/filings/42",
        evidence_ids=["doc-1"],
        confidence=0.9,
    )

    record = NormalizedFilingRecord.from_candidate(
        candidate, source_evidence_id="doc-1"
    )

    assert record.filing_type is FilingType.BUSINESS_LICENSE
    assert record.filing_name == "Example filing"
    assert record.detail_url == candidate.url
    assert record.source_evidence_id == "doc-1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "x" * 51),
        ("external_id", "x" * 256),
        ("url", "https://example.com/" + "x" * 2_000),
        ("title", "x" * 501),
    ],
)
def test_raw_document_rejects_values_too_long_for_database(
    field: str, value: str
) -> None:
    payload: dict[str, object] = {
        "provider": "official",
        "external_id": "doc-1",
        "url": "https://example.com/source",
        "title": "Source",
        "text": "Evidence",
        "published_at": None,
        field: value,
    }

    with pytest.raises(ValidationError):
        RawDocument.model_validate(payload)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/source",
        "http://localhost/source",
        "https://user:password@example.com/source",
    ],
)
def test_raw_document_rejects_non_public_or_credentialed_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        RawDocument(
            provider="official",
            external_id="doc-1",
            url=url,
            title="Source",
            text="Evidence",
            published_at=None,
        )


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/jobs/1", "https://user:pass@example.com/jobs/1"]
)
def test_persistence_job_dto_rejects_unsafe_apply_url(url: str) -> None:
    candidate = normalize_job(
        JobCandidate(
            company_name="Example",
            title="Engineer",
            provider="official",
            source_raw_id="job-1",
            evidence_ids=["doc-1"],
            confidence=0.9,
        )
    )
    with pytest.raises(ValidationError):
        NormalizedJobRecord(
            candidate=candidate,
            job_posting_id=None,
            source_evidence_id="doc-1",
            apply_url=url,
            posted_at=None,
            seen_at=NOW,
        )


def test_normalized_job_rejects_salary_outside_database_integer_domain() -> None:
    candidate = normalize_job(
        JobCandidate(
            company_name="Example",
            title="Engineer",
            location="Shanghai",
            provider="official",
            source_raw_id="job-1",
            evidence_ids=["doc-1"],
            confidence=0.9,
        )
    )
    oversized = replace(
        candidate,
        salary_minimum_monthly=2_147_483_648,
        salary_maximum_monthly=2_147_483_648,
    )

    with pytest.raises(ValidationError, match="salary"):
        NormalizedJobRecord(
            candidate=oversized,
            job_posting_id=None,
            source_evidence_id="doc-1",
            apply_url="https://example.com/jobs/1",
            posted_at=None,
            seen_at=NOW,
        )


@pytest.mark.parametrize("salary_months", [0, -1, 32_768])
def test_normalized_job_rejects_salary_months_outside_positive_smallint_domain(
    salary_months: int,
) -> None:
    candidate = normalize_job(
        JobCandidate(
            company_name="Example",
            title="Engineer",
            location="Shanghai",
            provider="official",
            source_raw_id="job-1",
            evidence_ids=["doc-1"],
            confidence=0.9,
        )
    )
    invalid = replace(candidate, salary_months=salary_months)

    with pytest.raises(ValidationError, match="salary months"):
        NormalizedJobRecord(
            candidate=invalid,
            job_posting_id=None,
            source_evidence_id="doc-1",
            apply_url="https://example.com/jobs/1",
            posted_at=None,
            seen_at=NOW,
        )


@pytest.mark.parametrize("salary_months", [True, 1.5])
def test_normalized_job_rejects_non_integer_salary_months(
    salary_months: bool | float,
) -> None:
    candidate = normalize_job(
        JobCandidate(
            company_name="Example",
            title="Engineer",
            location="Shanghai",
            provider="official",
            source_raw_id="job-1",
            evidence_ids=["doc-1"],
            confidence=0.9,
        )
    )
    invalid = replace(candidate, salary_months=salary_months)

    with pytest.raises(ValidationError, match="salary months"):
        NormalizedJobRecord(
            candidate=invalid,
            job_posting_id=None,
            source_evidence_id="doc-1",
            apply_url="https://example.com/jobs/1",
            posted_at=None,
            seen_at=NOW,
        )

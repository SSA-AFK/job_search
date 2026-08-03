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


def test_company_field_evidence_restricts_persisted_fields() -> None:
    with pytest.raises(ValidationError):
        CompanyFieldEvidence(
            field_name="created_at", evidence_id="doc-1", confidence=0.8
        )


def test_batch_rejects_job_without_provider_source_identity() -> None:
    job = NormalizedJobRecord(
        candidate=normalize_job(
            JobCandidate(
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

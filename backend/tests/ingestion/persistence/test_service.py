from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import CompanyCandidate, JobCandidate
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
from app.ingestion.persistence.service import PersistenceError, PersistenceService
from app.models import (
    Company,
    CompanySource,
    FilingType,
    JobPosting,
    JobSource,
    RegulatoryFiling,
    SourceDocument,
)

NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)
LATER = datetime(2026, 8, 1, 12, tzinfo=UTC)


def normalized_document(
    evidence_id: str,
    *,
    external_id: str | None = None,
    url: str = "https://example.com/source",
    text: str = "Evidence text",
) -> NormalizedDocument:
    return NormalizedDocument(
        evidence_id=evidence_id,
        document=RawDocument(
            provider="official",
            external_id=external_id,
            url=url,
            title="Source",
            text=text,
            published_at=None,
            authority_level=1,
        ),
        fetched_at=NOW,
    )


def normalized_company(
    *,
    company_id: UUID | None = None,
    field_evidence: tuple[CompanyFieldEvidence, ...] = (),
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
        company_id=company_id,
        field_evidence=field_evidence,
    )


def normalized_job(
    source_raw_id: str,
    *,
    evidence_id: str = "doc-1",
    description: str = "Build systems",
    posted_at: date | None = date(2026, 7, 20),
    seen_at: datetime = NOW,
    is_active: bool = True,
    job_posting_id: UUID | None = None,
) -> NormalizedJobRecord:
    return NormalizedJobRecord(
        candidate=normalize_job(
            JobCandidate(
                title="Software Engineer",
                employment_type="full_time",
                location="Shanghai",
                provider="official",
                source_raw_id=source_raw_id,
                apply_url=f"https://example.com/jobs/{source_raw_id}",
                posted_at=posted_at,
                description=description,
                evidence_ids=[evidence_id],
                confidence=0.9,
            )
        ),
        job_posting_id=job_posting_id,
        source_evidence_id=evidence_id,
        apply_url=f"https://example.com/jobs/{source_raw_id}",
        posted_at=posted_at,
        seen_at=seen_at,
        is_active=is_active,
    )


def normalized_filing(
    filing_number: str = "ICP-42", *, evidence_id: str = "doc-1"
) -> NormalizedFilingRecord:
    return NormalizedFilingRecord(
        filing_type=FilingType.ICP,
        filing_number=filing_number,
        filing_name="Example ICP filing",
        filing_authority="MIIT",
        filing_date=date(2026, 7, 1),
        filing_status="active",
        detail_url="https://example.com/filings/42",
        source_evidence_id=evidence_id,
    )


def normalized_batch(
    *,
    documents: tuple[NormalizedDocument, ...] | None = None,
    company: NormalizedCompanyRecord | None = None,
    jobs: tuple[NormalizedJobRecord, ...] | None = None,
    filings: tuple[NormalizedFilingRecord, ...] | None = None,
    collected_at: datetime = NOW,
) -> NormalizedBatch:
    return NormalizedBatch(
        documents=documents or (normalized_document("doc-1", external_id="source-1"),),
        company=company or normalized_company(),
        jobs=jobs if jobs is not None else (normalized_job("job-1"),),
        filings=filings if filings is not None else (normalized_filing(),),
        collected_at=collected_at,
    )


def count_rows(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def persistence(session: Session) -> PersistenceService:
    return PersistenceService(session)


def test_reprocessing_same_batch_updates_seen_time_without_new_rows(
    session: Session, persistence: PersistenceService
) -> None:
    batch = normalized_batch(
        jobs=(normalized_job("job-1"), normalized_job("job-2")),
    )

    first = persistence.persist(batch, run_id=uuid4())
    second = persistence.persist(batch.with_fetched_at(LATER), run_id=uuid4())

    assert second.company_id == first.company_id
    assert count_rows(session, SourceDocument) == 1
    assert count_rows(session, JobPosting) == 1
    assert count_rows(session, JobSource) == 2
    assert session.scalar(select(func.max(JobSource.last_seen_at))) == LATER
    assert first.documents_written == 1
    assert first.jobs_written == 1


def test_two_sources_are_attached_to_one_canonical_job(
    session: Session, persistence: PersistenceService
) -> None:
    persistence.persist(
        normalized_batch(
            jobs=(normalized_job("official-1"), normalized_job("board-1")),
            filings=(),
        ),
        run_id=uuid4(),
    )

    source_job_ids = set(session.scalars(select(JobSource.job_posting_id)))
    assert count_rows(session, JobPosting) == 1
    assert count_rows(session, JobSource) == 2
    assert len(source_job_ids) == 1


def test_source_documents_without_external_ids_use_url_and_hash_identity(
    session: Session, persistence: PersistenceService
) -> None:
    raw_text = "<p>Hello &amp; world</p>" + "x" * 5_000
    documents = (
        normalized_document(
            "doc-1",
            url="HTTPS://Example.COM:443/source#fragment",
            text=raw_text,
        ),
        normalized_document(
            "doc-2", url="https://example.com/source", text=raw_text
        ),
    )
    company = normalized_company(
        field_evidence=(
            CompanyFieldEvidence(
                field_name="canonical_name", evidence_id="doc-1", confidence=0.7
            ),
            CompanyFieldEvidence(
                field_name="description", evidence_id="doc-2", confidence=0.9
            ),
        )
    )

    persistence.persist(
        normalized_batch(documents=documents, company=company, jobs=(), filings=()),
        run_id=uuid4(),
    )

    stored = session.scalar(select(SourceDocument))
    evidence = session.scalar(select(CompanySource))
    assert stored is not None
    assert count_rows(session, SourceDocument) == 1
    assert stored.url == "https://example.com/source"
    assert stored.content_hash == sha256(raw_text.encode()).hexdigest()
    assert len(stored.text_excerpt) <= 4_000
    assert "<p>" not in stored.text_excerpt
    assert evidence is not None
    assert evidence.covered_fields == ["canonical_name", "description"]
    assert float(evidence.confidence) == 0.9


def test_job_merge_keeps_earliest_date_longest_description_and_source_activity(
    session: Session, persistence: PersistenceService
) -> None:
    persistence.persist(
        normalized_batch(
            jobs=(
                normalized_job(
                    "job-newer",
                    description="short",
                    posted_at=date(2026, 7, 20),
                    is_active=False,
                ),
                normalized_job(
                    "job-older",
                    description="A much longer valid description",
                    posted_at=date(2026, 7, 1),
                    is_active=True,
                ),
            ),
            filings=(),
        ),
        run_id=uuid4(),
    )

    job = session.scalar(select(JobPosting))
    assert job is not None
    assert job.posted_at == date(2026, 7, 1)
    assert job.description == "A much longer valid description"
    assert job.is_active is True


def test_duplicate_filing_in_batch_rolls_back_every_write(
    session: Session, persistence: PersistenceService
) -> None:
    run_id = uuid4()
    batch = normalized_batch(
        filings=(normalized_filing(), normalized_filing()),
    )

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(batch, run_id=run_id)

    assert raised.value.run_id == run_id
    assert raised.value.constraint == "uq_filing_type_number"
    assert count_rows(session, SourceDocument) == 0
    assert count_rows(session, Company) == 0
    assert count_rows(session, JobPosting) == 0


def test_filing_conflict_preserves_previous_collection_time(
    session: Session, persistence: PersistenceService
) -> None:
    first = persistence.persist(normalized_batch(filings=()), run_id=uuid4())
    other = Company(canonical_name="Other", normalized_name="other")
    session.add(other)
    session.flush()
    session.add(
        RegulatoryFiling(
            company_id=other.id,
            filing_type=FilingType.ICP,
            filing_number="ICP-CONFLICT",
            filing_name="Other filing",
        )
    )
    session.commit()
    failing_company = normalized_company(company_id=first.company_id)

    with pytest.raises(PersistenceError, match="persistence_conflict"):
        persistence.persist(
            normalized_batch(
                company=failing_company,
                filings=(normalized_filing("ICP-CONFLICT"),),
                collected_at=LATER,
            ).with_fetched_at(LATER),
            run_id=uuid4(),
        )

    company = session.get(Company, first.company_id)
    assert company is not None
    assert company.last_collected_at == NOW
    assert count_rows(session, SourceDocument) == 1
    assert count_rows(session, JobSource) == 1


def test_unknown_explicit_company_rolls_back_with_run_context(
    session: Session, persistence: PersistenceService
) -> None:
    run_id = uuid4()
    batch = normalized_batch(
        company=normalized_company(company_id=uuid4()), jobs=(), filings=()
    )

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(batch, run_id=run_id)

    assert raised.value.run_id == run_id
    assert raised.value.constraint == "company_id"
    assert count_rows(session, SourceDocument) == 0


def test_canonical_job_becomes_inactive_when_all_sources_are_inactive(
    session: Session, persistence: PersistenceService
) -> None:
    persistence.persist(
        normalized_batch(
            jobs=(normalized_job("job-1"), normalized_job("job-2")), filings=()
        ),
        run_id=uuid4(),
    )
    inactive = normalized_batch(
        jobs=(
            normalized_job("job-1", seen_at=LATER, is_active=False),
            normalized_job("job-2", seen_at=LATER, is_active=False),
        ),
        filings=(),
        collected_at=LATER,
    ).with_fetched_at(LATER)

    persistence.persist(inactive, run_id=uuid4())

    assert session.scalar(select(JobPosting.is_active)) is False

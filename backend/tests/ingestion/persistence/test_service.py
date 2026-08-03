from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
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
    Base,
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
    provider: str = "official",
    external_id: str | None = None,
    url: str = "https://example.com/source",
    title: str = "Source",
    text: str = "Evidence text",
    authority_level: int | None = 1,
    fetched_at: datetime = NOW,
) -> NormalizedDocument:
    return NormalizedDocument(
        evidence_id=evidence_id,
        document=RawDocument(
            provider=provider,
            external_id=external_id,
            url=url,
            title=title,
            text=text,
            published_at=None,
            authority_level=authority_level,
        ),
        fetched_at=fetched_at,
    )


def normalized_company(
    *,
    name: str = "Example",
    company_id: UUID | None = None,
    field_evidence: tuple[CompanyFieldEvidence, ...] = (),
) -> NormalizedCompanyRecord:
    return NormalizedCompanyRecord(
        candidate=normalize_company(
            CompanyCandidate(
                name=name,
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


def test_older_document_delivery_does_not_overwrite_newer_payload(
    session: Session, persistence: PersistenceService
) -> None:
    newer_text = "New authoritative evidence"
    newer = normalized_document(
        "doc-1",
        external_id="source-1",
        url="https://example.com/new",
        title="New title",
        text=newer_text,
        authority_level=1,
        fetched_at=LATER,
    )
    older = normalized_document(
        "doc-1",
        external_id="source-1",
        url="https://example.com/old",
        title="Old title",
        text="Old evidence",
        authority_level=4,
        fetched_at=NOW,
    )
    persistence.persist(
        normalized_batch(documents=(newer,), jobs=(), filings=(), collected_at=LATER),
        run_id=uuid4(),
    )
    persistence.persist(
        normalized_batch(documents=(older,), jobs=(), filings=()), run_id=uuid4()
    )

    stored = session.scalar(select(SourceDocument))
    assert stored is not None
    assert stored.fetched_at == LATER
    assert stored.url == "https://example.com/new"
    assert stored.title == "New title"
    assert stored.text_excerpt == newer_text
    assert stored.content_hash == sha256(newer_text.encode()).hexdigest()
    assert stored.authority_level == 1


def test_cross_company_job_source_conflict_rolls_back_and_session_remains_usable(
    session: Session, persistence: PersistenceService
) -> None:
    persistence.persist(normalized_batch(filings=()), run_id=uuid4())
    conflicting = normalized_batch(
        company=normalized_company(name="Other"),
        jobs=(normalized_job("job-1"),),
        filings=(),
    )

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(conflicting, run_id=uuid4())

    assert raised.value.constraint == "uq_job_source_provider_raw_id"
    assert session.scalar(
        select(func.count()).select_from(Company).where(Company.normalized_name == "other")
    ) == 0
    session.rollback()
    session.add(Company(canonical_name="Usable", normalized_name="usable"))
    session.commit()
    assert session.scalar(
        select(func.count()).select_from(Company).where(Company.normalized_name == "usable")
    ) == 1


def test_invalid_bypassed_job_state_becomes_audited_persistence_error(
    session: Session, persistence: PersistenceService
) -> None:
    candidate = JobCandidate.model_construct(
        title="Engineer",
        employment_type=None,
        location="Shanghai",
        provider=None,
        source_raw_id=None,
        apply_url=None,
        posted_at=None,
        salary=None,
        description="Build systems",
        evidence_ids=("doc-1",),
        confidence=0.9,
    )
    invalid_job = normalized_job("placeholder").model_copy(
        update={"candidate": normalize_job(candidate)}
    )
    invalid_batch = NormalizedBatch.model_construct(
        documents=(normalized_document("doc-1", external_id="source-1"),),
        company=normalized_company(),
        jobs=(invalid_job,),
        filings=(),
        collected_at=NOW,
    )
    run_id = uuid4()

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(invalid_batch, run_id=run_id)

    assert raised.value.run_id == run_id
    assert raised.value.constraint == "uq_job_source_provider_raw_id"
    assert count_rows(session, SourceDocument) == 0


def test_duplicate_delivery_converges_across_independently_committed_sessions(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'persistence.sqlite3').as_posix()}"
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    batch = normalized_batch(filings=())
    with Session(engine, expire_on_commit=False) as first_session:
        first = PersistenceService(first_session).persist(batch, run_id=uuid4())
    with Session(engine, expire_on_commit=False) as second_session:
        second = PersistenceService(second_session).persist(
            batch.with_fetched_at(LATER), run_id=uuid4()
        )
    with Session(engine) as verification_session:
        assert first.company_id == second.company_id
        assert count_rows(verification_session, SourceDocument) == 1
        assert count_rows(verification_session, Company) == 1
        assert count_rows(verification_session, JobPosting) == 1
        assert count_rows(verification_session, JobSource) == 1


def test_company_unique_race_reselects_winner_without_poisoning_outer_transaction(
    non_autoflush_session: Session,
) -> None:
    run_id = uuid4()
    winner = Company(canonical_name="Winner", normalized_name="example")
    service = PersistenceService(non_autoflush_session)

    with non_autoflush_session.begin():
        non_autoflush_session.add(winner)
        resolved = service._upsert_company(normalized_company(), run_id)
        non_autoflush_session.add(
            Company(canonical_name="After recovery", normalized_name="afterrecovery")
        )

    assert resolved.id == winner.id
    assert count_rows(non_autoflush_session, Company) == 2


def test_document_unique_race_reselects_winner_inside_savepoint(
    non_autoflush_session: Session,
) -> None:
    evidence_text = "Shared evidence"
    winner = SourceDocument(
        provider="official",
        external_id=None,
        url="https://example.com/source",
        title="Winner",
        text_excerpt=evidence_text,
        content_hash=sha256(evidence_text.encode()).hexdigest(),
        fetched_at=NOW,
    )
    record = normalized_document(
        "doc-1",
        external_id=None,
        url="https://example.com/source",
        text=evidence_text,
    )
    service = PersistenceService(non_autoflush_session)

    with non_autoflush_session.begin():
        non_autoflush_session.add(winner)
        resolved = service._upsert_documents((record,), uuid4())

    assert resolved["doc-1"].id == winner.id
    assert count_rows(non_autoflush_session, SourceDocument) == 1


def test_filing_unique_race_reselects_same_company_winner(
    non_autoflush_session: Session,
) -> None:
    company = Company(canonical_name="Example", normalized_name="example")
    non_autoflush_session.add(company)
    non_autoflush_session.commit()
    winner = RegulatoryFiling(
        company_id=company.id,
        filing_type=FilingType.ICP,
        filing_number="ICP-RACE",
        filing_name="Winner filing",
    )
    candidate = normalized_filing("ICP-RACE").model_copy(
        update={"source_evidence_id": None}
    )
    service = PersistenceService(non_autoflush_session)

    with non_autoflush_session.begin():
        non_autoflush_session.add(winner)
        service._upsert_filings(company.id, (candidate,), {}, uuid4())
        non_autoflush_session.add(
            RegulatoryFiling(
                company_id=company.id,
                filing_type=FilingType.ALGORITHM,
                filing_number="ALG-AFTER",
                filing_name="After recovery",
            )
        )

    assert count_rows(non_autoflush_session, RegulatoryFiling) == 2
    stored = non_autoflush_session.scalar(
        select(RegulatoryFiling).where(
            RegulatoryFiling.filing_type == FilingType.ICP,
            RegulatoryFiling.filing_number == "ICP-RACE",
        )
    )
    assert stored is not None
    assert stored.filing_name == "Example ICP filing"


def test_job_source_unique_race_converges_and_removes_orphan_posting(
    non_autoflush_session: Session,
) -> None:
    company = Company(canonical_name="Example", normalized_name="example")
    non_autoflush_session.add(company)
    non_autoflush_session.flush()
    winner_job = JobPosting(
        company_id=company.id,
        title="Winner",
        normalized_title="winner",
        city="beijing",
        description="Winner description",
    )
    non_autoflush_session.add(winner_job)
    non_autoflush_session.commit()
    winner_source = JobSource(
        job_posting_id=winner_job.id,
        provider="official",
        source_raw_id="job-race",
        apply_url="https://example.com/winner",
        first_seen_at=NOW,
        last_seen_at=NOW,
        is_active=True,
    )
    candidate = normalized_job("job-race").model_copy(
        update={"source_evidence_id": None}
    )
    service = PersistenceService(non_autoflush_session)

    with non_autoflush_session.begin():
        non_autoflush_session.add(winner_source)
        job_ids, _warnings = service._upsert_jobs(
            company.id, (candidate,), {}, uuid4()
        )

    assert job_ids == {winner_job.id}
    assert count_rows(non_autoflush_session, JobPosting) == 1
    assert count_rows(non_autoflush_session, JobSource) == 1


def test_statement_error_is_sanitized_with_run_context_and_full_rollback(
    session: Session, persistence: PersistenceService
) -> None:
    invalid_document = NormalizedDocument.model_construct(
        evidence_id="doc-1",
        document=normalized_document("doc-1", external_id="bad-time").document,
        fetched_at=NOW.replace(tzinfo=None),
    )
    invalid_batch = NormalizedBatch.model_construct(
        documents=(invalid_document,),
        company=normalized_company(),
        jobs=(),
        filings=(),
        collected_at=NOW,
    )
    run_id = uuid4()

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(invalid_batch, run_id=run_id)

    assert raised.value.run_id == run_id
    assert raised.value.detail == "database statement failed"
    assert count_rows(session, SourceDocument) == 0
    assert count_rows(session, Company) == 0


def test_integer_overflow_is_sanitized_and_leaves_session_usable(
    session: Session, persistence: PersistenceService
) -> None:
    valid_job = normalized_job("salary-overflow")
    oversized_candidate = replace(
        valid_job.candidate,
        salary_minimum_monthly=10**48,
        salary_maximum_monthly=10**48,
    )
    invalid_job = valid_job.model_copy(update={"candidate": oversized_candidate})
    invalid_batch = NormalizedBatch.model_construct(
        documents=(normalized_document("doc-1", external_id="source-overflow"),),
        company=normalized_company(),
        jobs=(invalid_job,),
        filings=(),
        collected_at=NOW,
    )
    run_id = uuid4()

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(invalid_batch, run_id=run_id)

    assert raised.value.run_id == run_id
    assert raised.value.detail == "database integer overflow"
    assert count_rows(session, SourceDocument) == 0
    assert count_rows(session, Company) == 0
    assert count_rows(session, JobPosting) == 0
    session.rollback()
    session.add(Company(canonical_name="Usable", normalized_name="usable"))
    session.commit()
    assert session.scalar(
        select(func.count()).select_from(Company).where(Company.normalized_name == "usable")
    ) == 1


def test_bypassed_salary_months_outside_smallint_domain_rolls_back(
    session: Session, persistence: PersistenceService
) -> None:
    valid_job = normalized_job("salary-months-overflow")
    invalid_candidate = replace(valid_job.candidate, salary_months=32_768)
    invalid_job = valid_job.model_copy(update={"candidate": invalid_candidate})
    invalid_batch = NormalizedBatch.model_construct(
        documents=(normalized_document("doc-1", external_id="source-months"),),
        company=normalized_company(),
        jobs=(invalid_job,),
        filings=(),
        collected_at=NOW,
    )
    run_id = uuid4()

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(invalid_batch, run_id=run_id)

    assert raised.value.run_id == run_id
    assert raised.value.constraint == "salary_months"
    assert raised.value.detail == "normalized salary months exceed database domain"
    assert count_rows(session, SourceDocument) == 0
    assert count_rows(session, Company) == 0
    assert count_rows(session, JobPosting) == 0


@pytest.mark.parametrize("salary_months", [True, 1.5])
def test_bypassed_non_integer_salary_months_roll_back(
    salary_months: bool | float,
    session: Session,
    persistence: PersistenceService,
) -> None:
    valid_job = normalized_job("non-integer-salary-months")
    invalid_candidate = replace(valid_job.candidate, salary_months=salary_months)
    invalid_job = valid_job.model_copy(update={"candidate": invalid_candidate})
    invalid_batch = NormalizedBatch.model_construct(
        documents=(normalized_document("doc-1", external_id="source-months"),),
        company=normalized_company(),
        jobs=(invalid_job,),
        filings=(),
        collected_at=NOW,
    )
    run_id = uuid4()

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(invalid_batch, run_id=run_id)

    assert raised.value.run_id == run_id
    assert raised.value.constraint == "salary_months"
    assert raised.value.detail == "normalized salary months exceed database domain"
    assert count_rows(session, SourceDocument) == 0
    assert count_rows(session, Company) == 0
    assert count_rows(session, JobPosting) == 0

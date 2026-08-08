import os
import re
import warnings
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import CompanyCandidate, JobCandidate
from app.ingestion.normalization.company import normalize_company
from app.ingestion.normalization.job import normalize_job
from app.ingestion.persistence import service as persistence_service_module
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
    CollectionStatus,
    Company,
    CompanyAlias,
    CompanySource,
    FilingType,
    JobPosting,
    JobSource,
    JobType,
    RegulatoryFiling,
    SourceDocument,
)

NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)
LATER = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _quoted_filing_race_schema(schema_name: str) -> str:
    if re.fullmatch(r"filing_identity_race_[0-9a-f]{32}", schema_name) is None:
        raise ValueError("invalid isolated filing identity schema")
    return f'"{schema_name}"'


def _drop_filing_race_schema(connection: Connection, schema_name: str) -> None:
    quoted_schema = _quoted_filing_race_schema(schema_name)
    statements = (
        (
            f"DROP INDEX IF EXISTS {quoted_schema}."
            "ix_regulatory_filings_normalized_filing_number"
        ),
        f"DROP TABLE IF EXISTS {quoted_schema}.regulatory_filings",
        f"DROP TABLE IF EXISTS {quoted_schema}.companies",
        f"DROP SCHEMA {quoted_schema}",
    )
    for statement in statements:
        connection.execute(text(statement))


def _quoted_company_identity_race_schema(schema_name: str) -> str:
    if re.fullmatch(r"company_identity_race_[0-9a-f]{32}", schema_name) is None:
        raise ValueError("invalid isolated company identity schema")
    return f'"{schema_name}"'


def _drop_company_identity_race_schema(
    connection: Connection, schema_name: str
) -> None:
    quoted_schema = _quoted_company_identity_race_schema(schema_name)
    statements = (
        f"DROP TABLE IF EXISTS {quoted_schema}.company_aliases",
        f"DROP TABLE IF EXISTS {quoted_schema}.companies",
        f"DROP SCHEMA {quoted_schema}",
    )
    for statement in statements:
        connection.execute(text(statement))


class _FilingLockRecordingSession:
    def __init__(self, dialect_name: str) -> None:
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
        self.lock_keys: list[int] = []
        self.events: list[str] = []
        self.owner: RegulatoryFiling | None = None

    def get_bind(self) -> object:
        return self._bind

    def execute(self, statement: object, parameters: dict[str, int]) -> None:
        assert "pg_advisory_xact_lock" in str(statement)
        self.events.append("lock")
        self.lock_keys.append(parameters["lock_key"])

    def scalars(self, _statement: object) -> tuple[RegulatoryFiling, ...]:
        self.events.append("owners")
        assert self.owner is not None
        return (self.owner,)


class _FilingCleanupRecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))


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
    aliases: tuple[str, ...] = (),
    website: str = "https://example.com",
    description: str = "Company description",
    field_evidence: tuple[CompanyFieldEvidence, ...] = (),
) -> NormalizedCompanyRecord:
    return NormalizedCompanyRecord(
        candidate=normalize_company(
            CompanyCandidate(
                name=name,
                aliases=aliases,
                website=website,
                description=description,
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
    employment_type: str | None = "full_time",
) -> NormalizedJobRecord:
    return NormalizedJobRecord(
        candidate=normalize_job(
            JobCandidate(
                company_name="Example",
                title="Software Engineer",
                employment_type=employment_type,
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


def test_persistence_rejects_old_claim_after_requeue_and_reclaim(session: Session) -> None:
    repository = CollectionRepository(session)
    _request, run = repository.create_request("Example", "example")
    session.commit()
    first_claim = repository.claim_queued(run.id)
    assert first_claim is not None and first_claim.claimed
    first_token = first_claim.claim_token
    assert first_token is not None
    assert repository.owns_claim(run.id, expected_claim_token=first_token)
    repository.requeue_for_retry(run.id, expected_claim_token=first_token)
    second_claim = repository.claim_queued(run.id)
    assert second_claim is not None and second_claim.claimed
    second_token = second_claim.claim_token
    run_id = run.id
    session.rollback()

    with pytest.raises(PersistenceError, match="run_claim"):
        PersistenceService(session).persist(
            normalized_batch(filings=()),
            run_id=run_id,
            expected_claim_token=first_token,
        )

    assert session.scalar(select(func.count(Company.id))) == 0
    stored = session.get(type(run), run_id)
    assert stored is not None and stored.status is CollectionStatus.RUNNING
    assert stored.claim_token == second_token


def test_persist_rejects_clean_autobegin_without_ending_caller_transaction(session: Session) -> None:
    assert session.scalar(select(Company).where(Company.canonical_name == "missing")) is None
    assert session.in_transaction()

    with pytest.raises(PersistenceError, match="active_session_transaction"):
        PersistenceService(session).persist(normalized_batch(filings=()), run_id=uuid4())

    assert session.in_transaction()


def test_persist_rejects_pending_writes_without_rolling_them_back(session: Session) -> None:
    pending = Company(canonical_name="Pending", normalized_name="pending")
    session.add(pending)
    run_id = uuid4()

    with pytest.raises(PersistenceError, match="active_session_transaction") as error:
        PersistenceService(session).persist(normalized_batch(filings=()), run_id=run_id)

    assert error.value.run_id == run_id
    assert pending in session.new


def count_rows(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
def persistence(session: Session) -> PersistenceService:
    return PersistenceService(session)


def existing_company_job(session: Session) -> tuple[Company, JobPosting]:
    company = Company(canonical_name="Example", normalized_name="example")
    session.add(company)
    session.flush()
    job = JobPosting(
        company_id=company.id,
        title="Software Engineer",
        normalized_title="softwareengineer",
        job_type="full_time",
        city="shanghai",
        description="",
    )
    session.add(job)
    session.commit()
    return company, job


def test_reprocessing_same_batch_updates_seen_time_without_new_rows(
    session: Session, persistence: PersistenceService
) -> None:
    company, job = existing_company_job(session)
    batch = normalized_batch(
        company=normalized_company(company_id=company.id),
        jobs=(
            normalized_job("job-1", job_posting_id=job.id),
            normalized_job("job-2", job_posting_id=job.id),
        ),
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
    company, job = existing_company_job(session)
    persistence.persist(
        normalized_batch(
            company=normalized_company(company_id=company.id),
            jobs=(
                normalized_job("official-1", job_posting_id=job.id),
                normalized_job("board-1", job_posting_id=job.id),
            ),
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
    company, job = existing_company_job(session)
    persistence.persist(
        normalized_batch(
            company=normalized_company(company_id=company.id),
            jobs=(
                normalized_job(
                    "job-newer",
                    description="short",
                    posted_at=date(2026, 7, 20),
                    is_active=False,
                    job_posting_id=job.id,
                ),
                normalized_job(
                    "job-older",
                    description="A much longer valid description",
                    posted_at=date(2026, 7, 1),
                    is_active=True,
                    job_posting_id=job.id,
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
    assert raised.value.constraint == "uq_filing_type_normalized_number"
    assert count_rows(session, SourceDocument) == 0
    assert count_rows(session, Company) == 0
    assert count_rows(session, JobPosting) == 0


def test_equivalent_unicode_filing_numbers_replay_one_normalized_identity(
    session: Session, persistence: PersistenceService
) -> None:
    first = persistence.persist(
        normalized_batch(
            jobs=(),
            filings=(normalized_filing("  Ｋ\u3000Straße\t42 "),),
        ),
        run_id=uuid4(),
    )


    stored = session.scalar(select(RegulatoryFiling))
    assert stored is not None
    session.execute(
        text(
            "UPDATE regulatory_filings SET filing_number = :filing_number "
            "WHERE id = :filing_id"
        ),
        {
            "filing_number": "  Ｋ\u3000Straße\t42 ",
            "filing_id": str(stored.id),
        },
    )
    session.commit()

    second = persistence.persist(
        normalized_batch(
            jobs=(),
            filings=(normalized_filing("K STRASSE 42"),),
        ).with_fetched_at(LATER),
        run_id=uuid4(),
    )

    session.refresh(stored)
    assert second.company_id == first.company_id
    assert count_rows(session, RegulatoryFiling) == 1
    assert stored.filing_number == "  Ｋ\u3000Straße\t42 "
    assert stored.normalized_filing_number == "kstrasse42"


def test_existing_company_keeps_identity_and_upserts_candidate_aliases(
    session: Session,
) -> None:
    company = Company(
        canonical_name="OpenAI",
        normalized_name="openai",
        website="https://old.example",
        description="Old profile",
    )
    session.add(company)
    session.commit()
    record = normalized_company(
        name="OpenAI China",
        company_id=company.id,
        aliases=("OpenAI Asia",),
        website="https://new.example",
        description="New profile",
    )

    PersistenceService(session).persist(
        normalized_batch(company=record, jobs=(), filings=()),
        run_id=uuid4(),
    )

    session.refresh(company)
    assert (company.canonical_name, company.normalized_name) == ("OpenAI", "openai")
    assert company.website == "https://new.example/"
    assert company.description == "New profile"
    assert set(
        session.scalars(
            select(CompanyAlias.normalized_alias).where(
                CompanyAlias.company_id == company.id
            )
        )
    ) == {"openaichina", "openaiasia"}


def test_alias_owned_by_another_company_rolls_back_entire_batch(
    session: Session,
) -> None:
    target = Company(
        canonical_name="OpenAI",
        normalized_name="openai",
        description="Old profile",
    )
    other = Company(canonical_name="Other", normalized_name="other")
    session.add_all((target, other))
    session.flush()
    session.add(
        CompanyAlias(
            company_id=other.id,
            alias="OpenAI China",
            normalized_alias="openaichina",
        )
    )
    session.commit()
    target_id = target.id
    other_id = other.id
    before_documents = count_rows(session, SourceDocument)
    session.rollback()

    with pytest.raises(PersistenceError) as raised:
        PersistenceService(session).persist(
            normalized_batch(
                company=normalized_company(
                    name="OpenAI China",
                    company_id=target_id,
                    description="Must roll back",
                ),
                jobs=(),
                filings=(),
            ),
            run_id=uuid4(),
        )

    assert raised.value.constraint == "uq_company_alias_normalized_alias"
    session.refresh(target)
    assert target.description == "Old profile"
    assert count_rows(session, SourceDocument) == before_documents
    assert session.scalar(
        select(CompanyAlias.company_id).where(
            CompanyAlias.normalized_alias == "openaichina"
        )
    ) == other_id


def test_existing_alias_owner_rejects_new_canonical_and_rolls_back_batch(
    session: Session,
) -> None:
    owner = Company(canonical_name="Owner", normalized_name="owner")
    session.add(owner)
    session.flush()
    session.add(
        CompanyAlias(
            company_id=owner.id,
            alias="Shared",
            normalized_alias="shared",
        )
    )
    session.commit()

    with pytest.raises(PersistenceError) as raised:
        PersistenceService(session).persist(
            normalized_batch(
                company=normalized_company(name="Shared"),
                jobs=(),
                filings=(),
            ),
            run_id=uuid4(),
        )

    assert raised.value.constraint == "uq_company_alias_normalized_alias"
    assert count_rows(session, Company) == 1
    assert count_rows(session, CompanyAlias) == 1
    assert count_rows(session, SourceDocument) == 0


def test_existing_canonical_owner_rejects_alias_and_rolls_back_earlier_alias(
    session: Session,
) -> None:
    target = Company(
        canonical_name="Target",
        normalized_name="target",
        description="Old profile",
    )
    owner = Company(canonical_name="Taken", normalized_name="taken")
    session.add_all((target, owner))
    session.commit()
    target_id = target.id

    with pytest.raises(PersistenceError) as raised:
        PersistenceService(session).persist(
            normalized_batch(
                company=normalized_company(
                    name="Target",
                    company_id=target_id,
                    aliases=("A Safe", "Taken"),
                    description="Must roll back",
                ),
                jobs=(),
                filings=(),
            ),
            run_id=uuid4(),
        )

    assert raised.value.constraint == "uq_company_alias_normalized_alias"
    session.refresh(target)
    assert target.description == "Old profile"
    assert session.scalar(
        select(CompanyAlias).where(CompanyAlias.normalized_alias == "asafe")
    ) is None
    assert count_rows(session, SourceDocument) == 0


def test_same_company_may_own_candidate_name_as_canonical_and_alias(
    session: Session,
) -> None:
    company = Company(canonical_name="Target", normalized_name="target")
    session.add(company)
    session.flush()
    session.add(
        CompanyAlias(
            company_id=company.id,
            alias="Target",
            normalized_alias="target",
        )
    )
    session.commit()

    result = PersistenceService(session).persist(
        normalized_batch(
            company=normalized_company(name="Target", company_id=company.id),
            jobs=(),
            filings=(),
        ),
        run_id=uuid4(),
    )

    assert result.company_id == company.id
    assert count_rows(session, Company) == 1
    assert count_rows(session, CompanyAlias) == 1


def test_sqlite_company_identity_lock_is_held_until_outer_commit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company-identity-lock-lifetime.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    first_after_identity_upsert = Event()
    release_first = Event()
    second_started = Event()
    second_entered_identity_upsert = Event()
    first_lock_entry_transaction_states: list[bool] = []
    first_lock_exit_transaction_states: list[bool] = []
    serialized_company_identity_names = (
        persistence_service_module.serialized_company_identity_names
    )

    @contextmanager
    def observe_name_lock_lifetime(
        session: Session, names: Sequence[str]
    ) -> Iterator[None]:
        is_first = session.info.get("company_identity_lock_role") == "first"
        if is_first:
            first_lock_entry_transaction_states.append(session.in_transaction())
        with serialized_company_identity_names(session, names):
            yield
        if is_first:
            first_lock_exit_transaction_states.append(session.in_transaction())

    monkeypatch.setattr(
        persistence_service_module,
        "serialized_company_identity_names",
        observe_name_lock_lifetime,
    )

    class PausingPersistence(PersistenceService):
        def _upsert_company_evidence(
            self,
            company: Company,
            record: NormalizedCompanyRecord,
            documents: dict[str, SourceDocument],
            run_id: UUID,
        ) -> None:
            first_after_identity_upsert.set()
            if not release_first.wait(timeout=15):
                raise TimeoutError("second writer did not reach the identity lock")
            super()._upsert_company_evidence(company, record, documents, run_id)

    class ObservingPersistence(PersistenceService):
        def _upsert_company_locked(
            self,
            record: NormalizedCompanyRecord,
            run_id: UUID,
            *,
            identity_names: tuple[str, ...],
        ) -> Company:
            second_entered_identity_upsert.set()
            return super()._upsert_company_locked(
                record,
                run_id,
                identity_names=identity_names,
            )

    def identity_batch(company: NormalizedCompanyRecord) -> NormalizedBatch:
        return normalized_batch(company=company, jobs=(), filings=())

    try:
        with Session(engine, expire_on_commit=False) as setup:
            PersistenceService(setup).persist(
                identity_batch(normalized_company(name="Seed")),
                run_id=uuid4(),
            )
            alias_target = Company(
                canonical_name="Alias Target",
                normalized_name="aliastarget",
            )
            setup.add(alias_target)
            setup.commit()
            alias_target_id = alias_target.id

        def write_canonical() -> UUID:
            with Session(engine) as writer:
                writer.info["company_identity_lock_role"] = "first"
                return PausingPersistence(writer).persist(
                    identity_batch(normalized_company(name="Shared Identity")),
                    run_id=uuid4(),
                ).company_id

        def write_alias() -> PersistenceError | None:
            second_started.set()
            try:
                with Session(engine) as writer:
                    ObservingPersistence(writer).persist(
                        identity_batch(
                            normalized_company(
                                name="Alias Target",
                                company_id=alias_target_id,
                                aliases=("Shared Identity",),
                            )
                        ),
                        run_id=uuid4(),
                    )
            except PersistenceError as error:
                return error
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(write_canonical)
            if not first_after_identity_upsert.wait(timeout=15):
                first_future.result(timeout=15)
                raise TimeoutError("first writer did not reach the outer transaction hook")
            second_future = executor.submit(write_alias)
            try:
                assert second_started.wait(timeout=5)
                assert not second_entered_identity_upsert.wait(timeout=0.2)
            finally:
                release_first.set()
            canonical_company_id = first_future.result(timeout=15)
            second_error = second_future.result(timeout=15)

        assert second_entered_identity_upsert.is_set()
        assert second_error is not None
        assert second_error.constraint == "uq_company_alias_normalized_alias"
        with Session(engine) as verification:
            canonical_owner_ids = set(
                verification.scalars(
                    select(Company.id).where(
                        Company.normalized_name == "sharedidentity"
                    )
                )
            )
            alias_owner_ids = set(
                verification.scalars(
                    select(CompanyAlias.company_id).where(
                        CompanyAlias.normalized_alias == "sharedidentity"
                    )
                )
            )
        assert canonical_owner_ids == {canonical_company_id}
        assert alias_owner_ids == set()
        assert first_lock_entry_transaction_states == [True]
        assert first_lock_exit_transaction_states == [False]
    finally:
        release_first.set()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_normalized_filing_owned_by_other_company_rolls_back(
    session: Session, persistence: PersistenceService
) -> None:
    first = persistence.persist(normalized_batch(jobs=(), filings=()), run_id=uuid4())
    current = session.get(Company, first.company_id)
    assert current is not None
    previous_collection_time = current.last_collected_at
    other = Company(canonical_name="Other", normalized_name="other")
    session.add(other)
    session.flush()
    created_at = "2026-08-07 00:00:00+00:00"
    session.execute(
        text(
            "INSERT INTO regulatory_filings "
            "(id, company_id, filing_type, filing_number, normalized_filing_number, "
            "filing_name, created_at, updated_at) VALUES "
            "(:id, :company_id, 'icp', :filing_number, 'kstrasse42', "
            ":filing_name, :created_at, :created_at)"
        ),
        {
            "id": str(uuid4()),
            "company_id": str(other.id),
            "filing_number": "Ｋ\u3000Straße\t42",
            "filing_name": "Other legacy filing",
            "created_at": created_at,
        },
    )
    session.commit()
    run_id = uuid4()

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(
            normalized_batch(
                jobs=(),
                filings=(normalized_filing("K STRASSE 42"),),
            ).with_fetched_at(LATER),
            run_id=run_id,
        )

    assert raised.value.run_id == run_id
    assert raised.value.constraint == "uq_filing_type_normalized_number"
    assert count_rows(session, RegulatoryFiling) == 1
    session.refresh(current)
    assert current.last_collected_at == previous_collection_time


def test_same_company_normalized_filing_replay_updates_existing_record(
    session: Session, persistence: PersistenceService
) -> None:
    first = persistence.persist(normalized_batch(jobs=(), filings=()), run_id=uuid4())
    fullwidth_id = uuid4()
    created_at = "2026-08-07 00:00:00+00:00"
    session.execute(
        text(
            "INSERT INTO regulatory_filings "
            "(id, company_id, filing_type, filing_number, normalized_filing_number, "
            "filing_name, created_at, updated_at) VALUES "
            "(:id, :company_id, 'icp', :filing_number, 'kstrasse42', "
            ":filing_name, :created_at, :created_at)"
        ),
        {
            "id": str(fullwidth_id),
            "company_id": str(first.company_id),
            "filing_number": "Ｋ\u3000Straße\t42",
            "filing_name": "Fullwidth legacy filing",
            "created_at": created_at,
        },
    )
    session.commit()

    persistence.persist(
        normalized_batch(
            jobs=(),
            filings=(normalized_filing("K STRASSE 42"),),
        ).with_fetched_at(LATER),
        run_id=uuid4(),
    )

    fullwidth_filing = session.get(RegulatoryFiling, fullwidth_id)
    assert fullwidth_filing is not None
    assert count_rows(session, RegulatoryFiling) == 1
    assert fullwidth_filing.filing_name == "Example ICP filing"


def test_filing_identity_advisory_locks_are_stable_and_batch_ordered() -> None:
    records = (
        normalized_filing("Z 2"),
        normalized_filing("A 1"),
        normalized_filing("A 1").model_copy(
            update={"filing_type": FilingType.ALGORITHM}
        ),
    )
    postgresql_session = _FilingLockRecordingSession("postgresql")

    PersistenceService(postgresql_session)._lock_filing_identities(records)  # type: ignore[arg-type]

    assert postgresql_session.lock_keys == [
        -2237796803508185819,
        5122387751065773925,
        -286932790104785166,
    ]

    sqlite_session = _FilingLockRecordingSession("sqlite")
    PersistenceService(sqlite_session)._lock_filing_identities(records)  # type: ignore[arg-type]
    assert sqlite_session.lock_keys == []


def test_postgresql_filing_identity_lock_precedes_owner_query() -> None:
    company_id = uuid4()
    record = normalized_filing("A 1").model_copy(
        update={"source_evidence_id": None}
    )
    owner = RegulatoryFiling(
        company_id=company_id,
        filing_type=record.filing_type,
        filing_number=record.filing_number,
        filing_name="Existing filing",
    )
    postgresql_session = _FilingLockRecordingSession("postgresql")
    postgresql_session.owner = owner

    PersistenceService(postgresql_session)._upsert_filings(  # type: ignore[arg-type]
        company_id,
        (record,),
        {},
        uuid4(),
    )

    assert postgresql_session.events == ["lock", "owners"]


@pytest.mark.postgresql
def test_filing_race_cleanup_drops_only_validated_owned_objects() -> None:
    connection = _FilingCleanupRecordingConnection()

    with pytest.raises(ValueError, match="invalid isolated filing identity schema"):
        _drop_filing_race_schema(connection, "public")  # type: ignore[arg-type]
    assert connection.statements == []

    schema_name = f"filing_identity_race_{'a' * 32}"
    _drop_filing_race_schema(connection, schema_name)  # type: ignore[arg-type]

    assert connection.statements == [
        (
            'DROP INDEX IF EXISTS "filing_identity_race_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".'
            "ix_regulatory_filings_normalized_filing_number"
        ),
        (
            'DROP TABLE IF EXISTS "filing_identity_race_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".'
            "regulatory_filings"
        ),
        (
            'DROP TABLE IF EXISTS "filing_identity_race_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".'
            "companies"
        ),
        'DROP SCHEMA "filing_identity_race_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
    ]
    assert all("CASCADE" not in statement for statement in connection.statements)


@pytest.mark.postgresql
def test_company_identity_race_cleanup_drops_only_validated_owned_objects() -> None:
    connection = _FilingCleanupRecordingConnection()

    with pytest.raises(ValueError, match="invalid isolated company identity schema"):
        _drop_company_identity_race_schema(connection, "public")  # type: ignore[arg-type]
    assert connection.statements == []

    schema_name = f"company_identity_race_{'a' * 32}"
    _drop_company_identity_race_schema(connection, schema_name)  # type: ignore[arg-type]

    assert connection.statements == [
        (
            'DROP TABLE IF EXISTS '
            '"company_identity_race_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".company_aliases'
        ),
        (
            'DROP TABLE IF EXISTS '
            '"company_identity_race_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".companies'
        ),
        'DROP SCHEMA "company_identity_race_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
    ]
    assert all("CASCADE" not in statement for statement in connection.statements)


@pytest.mark.postgresql
def test_concurrent_normalized_filing_collision_cannot_commit_two_companies() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"filing_identity_race_{uuid4().hex}"
    quoted_schema = _quoted_filing_race_schema(schema_name)
    admin_engine = create_engine(database_url)
    schema_engine = None
    schema_created = False
    first_company_id = uuid4()
    second_company_id = uuid4()
    first_raw_number = "K STRASSE 42"
    second_raw_number = "\uff2b\u3000Stra\u00dfe\t42"
    normalized_record = normalized_filing(first_raw_number).model_copy(
        update={"source_evidence_id": None}
    )
    assert normalized_record.filing_number == "kstrasse42"
    assert normalized_filing(second_raw_number).filing_number == "kstrasse42"

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
            schema_created = True

        schema_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema_name}"}
        )
        schema_engine = create_engine(schema_url)
        with schema_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE companies ("
                    "id uuid PRIMARY KEY, canonical_name varchar(255) NOT NULL, "
                    "normalized_name varchar(255) NOT NULL UNIQUE, "
                    "created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE regulatory_filings ("
                    "id uuid PRIMARY KEY, company_id uuid NOT NULL REFERENCES companies(id), "
                    "source_document_id uuid, filing_type varchar(50) NOT NULL, "
                    "filing_number varchar(255) NOT NULL, "
                    "normalized_filing_number varchar(255) NOT NULL, "
                    "filing_name varchar(255) NOT NULL, filing_authority varchar(255), "
                    "filing_date date, filing_status varchar(50), detail_url varchar(2000), "
                    "created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, "
                    "CONSTRAINT uq_filing_type_normalized_number "
                    "UNIQUE (filing_type, normalized_filing_number))"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_regulatory_filings_normalized_filing_number "
                    "ON regulatory_filings (normalized_filing_number)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO companies "
                    "(id, canonical_name, normalized_name, created_at, updated_at) VALUES "
                    "(:first_id, 'First', 'first', :now, :now), "
                    "(:second_id, 'Second', 'second', :now, :now)"
                ),
                {
                    "first_id": first_company_id,
                    "second_id": second_company_id,
                    "now": NOW,
                },
            )

        first_inserted = Event()
        release_first = Event()

        @event.listens_for(schema_engine, "before_cursor_execute")
        def release_owner_before_second_advisory_lock(
            connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            info = connection.info  # type: ignore[attr-defined]
            if (
                info.get("filing_race_role") == "second"
                and "PG_ADVISORY_XACT_LOCK" in statement.upper()
            ):
                release_first.set()

        def write_filing(
            *, company_id: UUID, raw_number: str, role: str, hold_commit: bool
        ) -> str:
            with Session(schema_engine) as database_session:
                @event.listens_for(database_session, "before_flush")
                def preserve_distinct_raw_number(
                    target_session: Session,
                    _flush_context: object,
                    _instances: object,
                ) -> None:
                    for pending in (*target_session.new, *target_session.dirty):
                        if isinstance(pending, RegulatoryFiling):
                            pending.__dict__["filing_number"] = raw_number

                with database_session.begin():
                    database_session.connection().info["filing_race_role"] = role
                    PersistenceService(database_session)._upsert_filings(
                        company_id,
                        (normalized_record,),
                        {},
                        uuid4(),
                    )
                    if hold_commit:
                        first_inserted.set()
                        if not release_first.wait(timeout=15):
                            raise TimeoutError("second filing transaction did not progress")
            return "committed"

        second_error: PersistenceError | None = None
        with ThreadPoolExecutor(max_workers=1) as executor:
            first_future = executor.submit(
                write_filing,
                company_id=first_company_id,
                raw_number=first_raw_number,
                role="first",
                hold_commit=True,
            )
            try:
                if not first_inserted.wait(timeout=15):
                    first_future.result(timeout=15)
                    raise TimeoutError("first filing transaction did not reach insert")
                try:
                    second_outcome = write_filing(
                        company_id=second_company_id,
                        raw_number=second_raw_number,
                        role="second",
                        hold_commit=False,
                    )
                except PersistenceError as error:
                    second_error = error
                    second_outcome = "conflict"
            finally:
                release_first.set()
            first_outcome = first_future.result(timeout=15)

        assert [first_outcome, second_outcome].count("committed") == 1
        assert second_error is not None
        assert second_error.constraint == "uq_filing_type_normalized_number"
        assert first_raw_number not in str(second_error)
        assert second_raw_number not in str(second_error)

        with Session(schema_engine) as replay_session:
            with replay_session.begin():
                PersistenceService(replay_session)._upsert_filings(
                    first_company_id,
                    (normalized_record,),
                    {},
                    uuid4(),
                )
            filings = tuple(replay_session.scalars(select(RegulatoryFiling)))
        assert len(filings) == 1
        assert filings[0].company_id == first_company_id
        assert filings[0].normalized_filing_number == "kstrasse42"
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                _drop_filing_race_schema(connection, schema_name)
        admin_engine.dispose()


@pytest.mark.postgresql
def test_concurrent_canonical_alias_collision_has_one_company_owner() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"company_identity_race_{uuid4().hex}"
    quoted_schema = _quoted_company_identity_race_schema(schema_name)
    admin_engine = create_engine(database_url)
    schema_engine = None
    schema_created = False
    alias_company_id = uuid4()
    shared_name = "Shared Identity"
    normalized_shared_name = "sharedidentity"

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
            schema_created = True

        schema_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema_name}"}
        )
        schema_engine = create_engine(schema_url)
        with schema_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE companies ("
                    "id uuid PRIMARY KEY, canonical_name varchar(255) NOT NULL, "
                    "normalized_name varchar(255) NOT NULL UNIQUE, "
                    "industry varchar(100), sub_industry varchar(100), "
                    "funding_stage varchar(50) NOT NULL, scale varchar(50) NOT NULL, "
                    "city varchar(50), logo_url varchar(1000), website varchar(1000), "
                    "normalized_website varchar(1000) NOT NULL, description text, "
                    "last_collected_at timestamptz, created_at timestamptz NOT NULL, "
                    "updated_at timestamptz NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE company_aliases ("
                    "id uuid PRIMARY KEY, "
                    "company_id uuid NOT NULL REFERENCES companies(id), "
                    "alias varchar(255) NOT NULL, "
                    "normalized_alias varchar(255) NOT NULL UNIQUE)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO companies ("
                    "id, canonical_name, normalized_name, funding_stage, scale, "
                    "normalized_website, created_at, updated_at) VALUES ("
                    ":company_id, 'Alias Target', 'aliastarget', 'unknown', "
                    "'unknown', '', :now, :now)"
                ),
                {"company_id": alias_company_id, "now": NOW},
            )

        second_lock_attempted = Event()
        second_finished = Event()

        @event.listens_for(schema_engine, "before_cursor_execute")
        def observe_second_advisory_lock(
            connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            info = connection.info  # type: ignore[attr-defined]
            if (
                info.get("company_identity_race_role") == "alias"
                and "PG_ADVISORY_XACT_LOCK" in statement.upper()
            ):
                second_lock_attempted.set()

        def write_alias() -> PersistenceError | None:
            try:
                with Session(schema_engine) as database_session, database_session.begin():
                    database_session.connection().info[
                        "company_identity_race_role"
                    ] = "alias"
                    PersistenceService(database_session)._upsert_company(
                        normalized_company(
                            name="Alias Target",
                            company_id=alias_company_id,
                            aliases=(shared_name,),
                        ),
                        uuid4(),
                    )
            except PersistenceError as error:
                return error
            finally:
                second_finished.set()
            return None

        with Session(schema_engine) as canonical_session, ThreadPoolExecutor(
            max_workers=1
        ) as executor:
            transaction = canonical_session.begin()
            try:
                canonical_company = PersistenceService(
                    canonical_session
                )._upsert_company(normalized_company(name=shared_name), uuid4())
                canonical_session.flush()
                canonical_company_id = canonical_company.id
                second_future = executor.submit(write_alias)
                if not second_lock_attempted.wait(timeout=15):
                    raise TimeoutError("alias transaction did not reach identity lock")
                assert not second_finished.wait(timeout=0.2)
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()
            second_error = second_future.result(timeout=15)

        assert second_error is not None
        assert second_error.constraint == "uq_company_alias_normalized_alias"
        assert shared_name not in str(second_error)

        with Session(schema_engine) as verification:
            canonical_owner_ids = set(
                verification.scalars(
                    select(Company.id).where(
                        Company.normalized_name == normalized_shared_name
                    )
                )
            )
            alias_owner_ids = set(
                verification.scalars(
                    select(CompanyAlias.company_id).where(
                        CompanyAlias.normalized_alias == normalized_shared_name
                    )
                )
            )
        assert canonical_owner_ids == {canonical_company_id}
        assert alias_owner_ids == set()
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                _drop_company_identity_race_schema(connection, schema_name)
        admin_engine.dispose()


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
    assert raised.value.constraint == "persistence_dto"
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
    assert (winner.canonical_name, winner.normalized_name) == ("Winner", "example")
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
        filing_number="icp-race",
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
            RegulatoryFiling.filing_number == "icp-race",
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


def test_job_source_unique_race_rejects_incompatible_known_type(
    non_autoflush_session: Session,
) -> None:
    company = Company(canonical_name="Example", normalized_name="example")
    non_autoflush_session.add(company)
    non_autoflush_session.flush()
    winner_job = JobPosting(
        company_id=company.id,
        title="Winner",
        normalized_title="winner",
        job_type=JobType.FULL_TIME,
        city="beijing",
        description="Winner description",
    )
    non_autoflush_session.add(winner_job)
    non_autoflush_session.commit()
    winner_source = JobSource(
        job_posting_id=winner_job.id,
        provider="official",
        source_raw_id="job-type-race",
        apply_url="https://example.com/winner",
        first_seen_at=NOW,
        last_seen_at=NOW,
        is_active=True,
    )
    candidate = normalized_job(
        "job-type-race", employment_type="part_time"
    ).model_copy(update={"source_evidence_id": None})
    service = PersistenceService(non_autoflush_session)

    with pytest.raises(PersistenceError) as raised, non_autoflush_session.begin():
        non_autoflush_session.add(winner_source)
        service._upsert_jobs(company.id, (candidate,), {}, uuid4())

    assert raised.value.constraint == "job_type"
    assert count_rows(non_autoflush_session, JobPosting) == 1


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
    assert raised.value.detail == "invalid persistence boundary data"
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
    assert raised.value.detail == "invalid persistence boundary data"
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
    assert raised.value.constraint == "persistence_dto"
    assert raised.value.detail == "invalid persistence boundary data"
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

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", UserWarning)
        with pytest.raises(PersistenceError) as raised:
            persistence.persist(invalid_batch, run_id=run_id)

    if isinstance(salary_months, float):
        assert len(caught_warnings) == 1
        warning = caught_warnings[0]
        assert warning.category is UserWarning
        assert re.fullmatch(
            r"Pydantic serializer warnings:\n  Expected `int` but got `float` "
            r"with value `1\.5` - serialized value may not be as expected",
            str(warning.message),
        )
    else:
        assert caught_warnings == []

    assert raised.value.run_id == run_id
    assert raised.value.constraint == "persistence_dto"
    assert raised.value.detail == "invalid persistence boundary data"
    assert count_rows(session, SourceDocument) == 0
    assert count_rows(session, Company) == 0
    assert count_rows(session, JobPosting) == 0


def test_persistence_does_not_remerge_a_job_rejected_by_deduplication(
    session: Session, persistence: PersistenceService
) -> None:
    company = Company(canonical_name="Example", normalized_name="example")
    session.add(company)
    session.flush()
    existing = JobPosting(
        company_id=company.id,
        title="Software Engineer",
        normalized_title="softwareengineer",
        job_type="full_time",
        city="shanghai",
        description="Existing",
    )
    session.add(existing)
    session.commit()
    incoming = normalized_job("intern-1")
    incoming_candidate = JobCandidate(
        company_name="Example",
        title="Software Engineer",
        employment_type="internship",
        location="Shanghai",
        provider="official",
        source_raw_id="intern-1",
        evidence_ids=("doc-1",),
        confidence=0.9,
    )
    incoming = incoming.model_copy(
        update={"candidate": normalize_job(incoming_candidate), "job_posting_id": None}
    )

    persistence.persist(
        normalized_batch(
            company=normalized_company(company_id=company.id),
            jobs=(incoming,),
            filings=(),
        ),
        run_id=uuid4(),
    )

    jobs = session.scalars(select(JobPosting).order_by(JobPosting.created_at)).all()
    assert len(jobs) == 2
    assert {str(job.job_type) for job in jobs} == {"full_time", "internship"}


def test_unknown_incoming_type_does_not_degrade_known_canonical_type(
    session: Session, persistence: PersistenceService
) -> None:
    company = Company(canonical_name="Example", normalized_name="example")
    session.add(company)
    session.flush()
    existing = JobPosting(
        company_id=company.id,
        title="Software Engineer",
        normalized_title="softwareengineer",
        job_type="full_time",
        city="shanghai",
        description="Existing",
    )
    session.add(existing)
    session.commit()
    candidate = JobCandidate(
        company_name="Example",
        title="Software Engineer",
        location="Shanghai",
        provider="official",
        source_raw_id="unknown-1",
        evidence_ids=("doc-1",),
        confidence=0.9,
    )
    record = normalized_job("unknown-1", job_posting_id=existing.id).model_copy(
        update={"candidate": normalize_job(candidate)}
    )

    persistence.persist(
        normalized_batch(
            company=normalized_company(company_id=company.id),
            jobs=(record,),
            filings=(),
        ),
        run_id=uuid4(),
    )

    session.refresh(existing)
    assert existing.job_type.value == "full_time"


@pytest.mark.parametrize("employment_type", ["part_time", "temporary"])
def test_persistence_writes_first_class_employment_types(
    employment_type: str, session: Session, persistence: PersistenceService
) -> None:
    persistence.persist(
        normalized_batch(
            jobs=(
                normalized_job(
                    f"{employment_type}-1", employment_type=employment_type
                ),
            ),
            filings=(),
        ),
        run_id=uuid4(),
    )

    stored = session.scalar(select(JobPosting))
    assert stored is not None
    assert stored.job_type.value == employment_type


@pytest.mark.parametrize(
    ("existing_type", "incoming_type"),
    [
        (JobType.FULL_TIME, "part_time"),
        (JobType.PART_TIME, "temporary"),
        (JobType.TEMPORARY, "internship"),
    ],
)
def test_persistence_rejects_unequal_known_employment_types(
    existing_type: JobType,
    incoming_type: str,
    session: Session,
    persistence: PersistenceService,
) -> None:
    company = Company(canonical_name="Example", normalized_name="example")
    session.add(company)
    session.flush()
    existing = JobPosting(
        company_id=company.id,
        title="Software Engineer",
        normalized_title="softwareengineer",
        job_type=existing_type,
        city="shanghai",
        description="Existing",
    )
    session.add(existing)
    session.commit()
    incoming = normalized_job(
        f"{incoming_type}-1",
        employment_type=incoming_type,
        job_posting_id=existing.id,
    )

    with pytest.raises(PersistenceError) as raised:
        persistence.persist(
            normalized_batch(
                company=normalized_company(company_id=company.id),
                jobs=(incoming,),
                filings=(),
            ),
            run_id=uuid4(),
        )

    assert raised.value.constraint == "job_type"
    session.refresh(existing)
    assert existing.job_type is existing_type

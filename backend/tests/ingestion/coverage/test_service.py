from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.ingestion.coverage.contracts import RecordJobSnapshot
from app.ingestion.coverage.service import CoverageConflict, JobCoverageService
from app.models import (
    CollectionStatus,
    Company,
    CrawlRun,
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobPosting,
    JobSnapshotStatus,
    JobSource,
    RunType,
)

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
ENTRY_URL = "https://jobs.example.com/openings"


@dataclass(frozen=True)
class CoverageRows:
    company: Company
    entry: JobEntry
    posting: JobPosting
    source: JobSource


def seed_coverage_rows(
    session: Session,
    company: Company,
    *,
    source_active: bool = True,
    source_missing: int = 0,
    entry_status: JobEntryStatus = JobEntryStatus.UNKNOWN,
    failure_count: int = 0,
    source_id: UUID | None = None,
) -> CoverageRows:
    entry = JobEntry(
        company_id=company.id,
        url=ENTRY_URL,
        normalized_url=ENTRY_URL,
        provider="official",
        platform="self_hosted",
        requires_rendering=False,
        status=entry_status,
        failure_count=failure_count,
    )
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
        is_active=source_active,
    )
    session.add_all((entry, posting))
    session.flush()
    source = JobSource(
        id=source_id or uuid4(),
        job_posting_id=posting.id,
        job_entry_id=entry.id,
        provider="official",
        source_raw_id=str(uuid4()),
        apply_url="https://jobs.example.com/apply",
        first_seen_at=NOW - timedelta(days=10),
        last_seen_at=NOW - timedelta(days=2),
        is_active=source_active,
        missing_complete_snapshots=source_missing,
    )
    session.add(source)
    session.commit()
    return CoverageRows(company=company, entry=entry, posting=posting, source=source)


def add_run(session: Session, company_id: UUID) -> CrawlRun:
    run = CrawlRun(
        company_id=company_id,
        run_type=RunType.COMPANY_REFRESH,
        status=CollectionStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    return run


def snapshot_command(
    session: Session,
    rows: CoverageRows,
    *,
    status: JobSnapshotStatus = JobSnapshotStatus.SUCCEEDED,
    seen_source_ids: set[UUID] | frozenset[UUID] = frozenset(),
    completed_at: datetime = NOW,
) -> RecordJobSnapshot:
    run = add_run(session, rows.company.id)
    if status is JobSnapshotStatus.SUCCEEDED:
        return RecordJobSnapshot(
            entry_id=rows.entry.id,
            crawl_run_id=run.id,
            status=status,
            pagination_complete=True,
            empty_confirmed=False,
            reported_total=len(seen_source_ids),
            pages_fetched=1,
            started_at=completed_at - timedelta(minutes=1),
            completed_at=completed_at,
            seen_source_ids=seen_source_ids,
        )
    return RecordJobSnapshot(
        entry_id=rows.entry.id,
        crawl_run_id=run.id,
        status=status,
        pagination_complete=False,
        pages_fetched=0 if status is JobSnapshotStatus.FAILED else 1,
        error_code="request_failed",
        started_at=completed_at - timedelta(minutes=1),
        completed_at=completed_at,
    )


@pytest.fixture
def rows(session: Session, company: Company) -> CoverageRows:
    return seed_coverage_rows(session, company)


@pytest.fixture
def service(session: Session) -> JobCoverageService:
    return JobCoverageService(session)


def test_two_complete_absences_deactivate_source_and_posting(
    session: Session, service: JobCoverageService, rows: CoverageRows
) -> None:
    first = service.record(snapshot_command(session, rows, completed_at=NOW))

    assert first.sources_missing_incremented == 1
    assert first.sources_deactivated == 0
    assert rows.source.missing_complete_snapshots == 1
    assert rows.source.is_active is True

    second = service.record(
        snapshot_command(session, rows, completed_at=NOW + timedelta(hours=1))
    )

    assert second.sources_missing_incremented == 1
    assert second.sources_deactivated == 1
    assert rows.source.missing_complete_snapshots == 2
    assert rows.source.is_active is False
    assert rows.posting.is_active is False


@pytest.mark.parametrize(
    "status", (JobSnapshotStatus.PARTIAL, JobSnapshotStatus.FAILED)
)
def test_noncomplete_snapshot_updates_health_without_changing_sources(
    session: Session,
    service: JobCoverageService,
    rows: CoverageRows,
    status: JobSnapshotStatus,
) -> None:
    previous_seen_at = rows.source.last_seen_at

    result = service.record(snapshot_command(session, rows, status=status))

    assert result.sources_missing_incremented == 0
    assert result.sources_deactivated == 0
    assert result.sources_reactivated == 0
    assert result.jobs_recomputed == 0
    assert rows.source.missing_complete_snapshots == 0
    assert rows.source.is_active is True
    assert rows.source.last_seen_at == previous_seen_at
    assert rows.entry.failure_count == 1
    assert rows.entry.status is JobEntryStatus.UNKNOWN
    assert rows.entry.last_checked_at == NOW
    assert rows.entry.last_success_at is None


def test_seen_source_reactivates_resets_counter_and_updates_last_seen(
    session: Session, service: JobCoverageService, company: Company
) -> None:
    rows = seed_coverage_rows(
        session,
        company,
        source_active=False,
        source_missing=3,
        entry_status=JobEntryStatus.STALE,
        failure_count=4,
    )

    result = service.record(snapshot_command(session, rows, seen_source_ids={rows.source.id}))

    assert result.sources_reactivated == 1
    assert result.sources_missing_incremented == 0
    assert result.sources_deactivated == 0
    assert result.jobs_recomputed == 1
    assert rows.source.is_active is True
    assert rows.source.missing_complete_snapshots == 0
    assert rows.source.last_seen_at == NOW
    assert rows.source.last_seen_snapshot_id == result.snapshot_id
    assert rows.posting.is_active is True
    assert rows.entry.status is JobEntryStatus.ACTIVE
    assert rows.entry.failure_count == 0
    assert rows.entry.last_checked_at == NOW
    assert rows.entry.last_success_at == NOW


def test_another_active_source_keeps_posting_active(
    session: Session, service: JobCoverageService, company: Company
) -> None:
    rows = seed_coverage_rows(session, company, source_missing=1)
    survivor = JobSource(
        job_posting_id=rows.posting.id,
        job_entry_id=rows.entry.id,
        provider="official",
        source_raw_id=str(uuid4()),
        apply_url="https://jobs.example.com/survivor",
        is_active=True,
    )
    session.add(survivor)
    session.commit()

    result = service.record(
        snapshot_command(session, rows, seen_source_ids={survivor.id})
    )

    assert result.sources_deactivated == 1
    assert rows.source.is_active is False
    assert survivor.is_active is True
    assert rows.posting.is_active is True


def test_complete_snapshot_updates_all_seen_sources_before_any_unseen_source(
    session: Session, service: JobCoverageService, company: Company
) -> None:
    unseen_id = UUID("00000000-0000-0000-0000-000000000001")
    seen_id = UUID("00000000-0000-0000-0000-000000000002")
    rows = seed_coverage_rows(session, company, source_id=unseen_id)
    seen_source = JobSource(
        id=seen_id,
        job_posting_id=rows.posting.id,
        job_entry_id=rows.entry.id,
        provider="official",
        source_raw_id=str(uuid4()),
        apply_url="https://jobs.example.com/seen",
        is_active=True,
    )
    session.add(seen_source)
    session.commit()
    command = snapshot_command(session, rows, seen_source_ids={seen_source.id})
    mutations: list[str] = []

    def record_seen(target: JobSource, value: UUID | None, *_args: object) -> None:
        if target.id == seen_id and value is not None:
            mutations.append("seen")

    def record_unseen(target: JobSource, value: int, *_args: object) -> None:
        if target.id == unseen_id and value == 1:
            mutations.append("unseen")

    event.listen(JobSource.last_seen_snapshot_id, "set", record_seen)
    event.listen(JobSource.missing_complete_snapshots, "set", record_unseen)
    try:
        service.record(command)
    finally:
        event.remove(JobSource.last_seen_snapshot_id, "set", record_seen)
        event.remove(JobSource.missing_complete_snapshots, "set", record_unseen)

    assert mutations == ["seen", "unseen"]


def test_seen_source_from_another_entry_is_rejected_without_snapshot(
    session: Session, service: JobCoverageService, rows: CoverageRows
) -> None:
    other_entry = JobEntry(
        company_id=rows.company.id,
        url="https://jobs.example.com/other",
        normalized_url="https://jobs.example.com/other",
        provider="official",
        platform="self_hosted",
        requires_rendering=False,
    )
    other_posting = JobPosting(
        company_id=rows.company.id,
        title="Designer",
        normalized_title="designer",
        city="Shanghai",
        description="Design",
    )
    session.add_all((other_entry, other_posting))
    session.flush()
    other_source = JobSource(
        job_posting_id=other_posting.id,
        job_entry_id=other_entry.id,
        provider="official",
        source_raw_id=str(uuid4()),
        apply_url="https://jobs.example.com/other/apply",
    )
    session.add(other_source)
    session.commit()
    command = snapshot_command(session, rows, seen_source_ids={other_source.id})

    with pytest.raises(CoverageConflict) as raised:
        service.record(command)

    assert raised.value.code == "source_entry_conflict"
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 0
    assert rows.source.missing_complete_snapshots == 0
    assert other_source.missing_complete_snapshots == 0


def test_complete_snapshot_does_not_mutate_legacy_or_unrelated_entry_sources(
    session: Session, service: JobCoverageService, rows: CoverageRows
) -> None:
    legacy = JobSource(
        job_posting_id=rows.posting.id,
        job_entry_id=None,
        provider="legacy",
        source_raw_id=str(uuid4()),
        apply_url="https://jobs.example.com/legacy",
        is_active=True,
        missing_complete_snapshots=7,
    )
    unrelated_entry = JobEntry(
        company_id=rows.company.id,
        url="https://jobs.example.com/unrelated",
        normalized_url="https://jobs.example.com/unrelated",
        provider="official",
        platform="self_hosted",
        requires_rendering=False,
    )
    session.add_all((legacy, unrelated_entry))
    session.flush()
    unrelated = JobSource(
        job_posting_id=rows.posting.id,
        job_entry_id=unrelated_entry.id,
        provider="official",
        source_raw_id=str(uuid4()),
        apply_url="https://jobs.example.com/unrelated/apply",
        is_active=False,
        missing_complete_snapshots=9,
    )
    session.add(unrelated)
    session.commit()

    service.record(snapshot_command(session, rows))

    assert rows.source.missing_complete_snapshots == 1
    assert legacy.missing_complete_snapshots == 7
    assert legacy.is_active is True
    assert unrelated.missing_complete_snapshots == 9
    assert unrelated.is_active is False


def test_identical_replay_returns_existing_snapshot_without_reapplying_lifecycle(
    session: Session, service: JobCoverageService, rows: CoverageRows
) -> None:
    command = snapshot_command(session, rows)
    created = service.record(command)
    first_checked_at = rows.entry.last_checked_at
    first_failure_count = rows.entry.failure_count

    replayed = service.record(command)

    assert replayed.snapshot_id == created.snapshot_id
    assert replayed.created is False
    assert replayed.sources_reactivated == 0
    assert replayed.sources_missing_incremented == 0
    assert replayed.sources_deactivated == 0
    assert replayed.jobs_recomputed == 0
    assert rows.source.missing_complete_snapshots == 1
    assert rows.entry.last_checked_at == first_checked_at
    assert rows.entry.failure_count == first_failure_count
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 1


def test_conflicting_replay_fails_before_mutating_entry_or_sources(
    session: Session, service: JobCoverageService, rows: CoverageRows
) -> None:
    command = snapshot_command(session, rows)
    service.record(command)
    first_checked_at = rows.entry.last_checked_at
    conflicting = command.model_copy(update={"reported_total": 3})

    with pytest.raises(CoverageConflict) as raised:
        service.record(conflicting)

    assert raised.value.code == "snapshot_conflict"
    assert rows.source.missing_complete_snapshots == 1
    assert rows.entry.last_checked_at == first_checked_at
    assert rows.entry.failure_count == 0
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 1


def test_active_session_is_rejected_without_ending_caller_transaction(
    session: Session, service: JobCoverageService, rows: CoverageRows
) -> None:
    pending = Company(canonical_name="Pending", normalized_name="pending")
    session.add(pending)
    command = RecordJobSnapshot(
        entry_id=rows.entry.id,
        crawl_run_id=uuid4(),
        status=JobSnapshotStatus.FAILED,
        error_code="request_failed",
        pages_fetched=0,
        started_at=NOW - timedelta(minutes=1),
        completed_at=NOW,
    )

    with pytest.raises(CoverageConflict) as raised:
        service.record(command)

    assert raised.value.code == "active_session_transaction"
    assert session.in_transaction()
    assert pending in session.new


def test_record_locks_entry_before_uuid_ordered_sources_and_commits_once(
    session: Session, service: JobCoverageService, rows: CoverageRows
) -> None:
    command = snapshot_command(session, rows, seen_source_ids={rows.source.id})
    calls: list[str] = []
    original_lock_entry = service.repository.lock_entry
    original_lock_sources = service.repository.lock_entry_sources

    def lock_entry(entry_id: UUID) -> JobEntry:
        calls.append("entry")
        return original_lock_entry(entry_id)

    def lock_sources(entry_id: UUID) -> tuple[JobSource, ...]:
        sources = original_lock_sources(entry_id)
        assert tuple(source.id for source in sources) == tuple(
            sorted(source.id for source in sources)
        )
        calls.append("sources")
        return sources

    service.repository.lock_entry = lock_entry  # type: ignore[method-assign]
    service.repository.lock_entry_sources = lock_sources  # type: ignore[method-assign]
    commits = 0

    def count_commit(_connection: object) -> None:
        nonlocal commits
        commits += 1

    assert session.bind is not None
    event.listen(session.bind, "commit", count_commit)
    try:
        service.record(command)
    finally:
        event.remove(session.bind, "commit", count_commit)

    assert calls == ["entry", "sources"]
    assert commits == 1
    assert not session.in_transaction()


def test_flush_failure_rolls_back_snapshot_source_posting_and_entry_health(
    session: Session,
    service: JobCoverageService,
    company: Company,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = seed_coverage_rows(
        session,
        company,
        source_active=True,
        source_missing=1,
        entry_status=JobEntryStatus.STALE,
        failure_count=5,
    )
    command = snapshot_command(session, rows)
    original_flush = session.flush
    unrestricted_flushes = 0

    def fail_after_final_flush(objects=None) -> None:
        nonlocal unrestricted_flushes
        original_flush(objects)
        if objects is None:
            unrestricted_flushes += 1
            if unrestricted_flushes == 2:
                raise RuntimeError("injected flush failure")

    monkeypatch.setattr(session, "flush", fail_after_final_flush)

    with pytest.raises(RuntimeError, match="injected flush failure"):
        service.record(command)

    assert not session.in_transaction()
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 0
    persisted_source = session.get(JobSource, rows.source.id)
    persisted_posting = session.get(JobPosting, rows.posting.id)
    persisted_entry = session.get(JobEntry, rows.entry.id)
    assert persisted_source is not None
    assert persisted_source.missing_complete_snapshots == 1
    assert persisted_source.is_active is True
    assert persisted_source.last_seen_snapshot_id is None
    assert persisted_posting is not None and persisted_posting.is_active is True
    assert persisted_entry is not None
    assert persisted_entry.status is JobEntryStatus.STALE
    assert persisted_entry.failure_count == 5
    assert persisted_entry.last_checked_at is None
    assert persisted_entry.last_success_at is None

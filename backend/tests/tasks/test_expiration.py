from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.ingestion.coverage.contracts import RecordJobSnapshot
from app.ingestion.coverage.service import JobCoverageService
from app.models import (
    Base,
    CollectionStatus,
    Company,
    CrawlRun,
    JobCollectionSnapshot,
    JobEntry,
    JobPosting,
    JobSnapshotStatus,
    JobSource,
    JobType,
    RunType,
)


def add_company(session: Session) -> Company:
    record = Company(
        id=uuid4(), canonical_name="Acme", normalized_name="acme", funding_stage="unknown", scale="unknown"
    )
    session.add(record)
    session.flush()
    return record


def add_source(
    session: Session,
    job: JobPosting,
    seen_at: datetime,
    *,
    entry_id: UUID | None = None,
    last_seen_snapshot_id: UUID | None = None,
    missing_complete_snapshots: int = 0,
    lifecycle_managed: bool = False,
) -> JobSource:
    source = JobSource(
        job_posting_id=job.id,
        job_entry_id=entry_id,
        last_seen_snapshot_id=last_seen_snapshot_id,
        provider="test",
        source_raw_id=str(uuid4()),
        apply_url="https://example.test/jobs/1",
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        missing_complete_snapshots=missing_complete_snapshots,
        lifecycle_managed=lifecycle_managed,
    )
    session.add(source)
    return source


def add_entry(session: Session, company: Company, suffix: str) -> JobEntry:
    url = f"https://example.test/jobs/{suffix}"
    entry = JobEntry(
        company_id=company.id,
        url=url,
        normalized_url=url,
        provider="test",
        platform="custom",
    )
    session.add(entry)
    session.flush()
    return entry


def add_applied_snapshot(
    session: Session, entry: JobEntry, completed_at: datetime
) -> JobCollectionSnapshot:
    snapshot = JobCollectionSnapshot(
        job_entry_id=entry.id,
        status=JobSnapshotStatus.SUCCEEDED,
        lifecycle_applied=True,
        pagination_complete=True,
        observed_count=1,
        pages_fetched=1,
        command_hash="a" * 64,
        started_at=completed_at - timedelta(minutes=1),
        completed_at=completed_at,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def create_foreign_key_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(
        dbapi_connection: object, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def record_complete_snapshot(
    session: Session,
    company: Company,
    entry: JobEntry,
    *,
    completed_at: datetime,
    seen_source_ids: set[UUID],
) -> UUID:
    run = CrawlRun(
        company_id=company.id,
        run_type=RunType.COMPANY_REFRESH,
        status=CollectionStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    result = JobCoverageService(session).record(
        RecordJobSnapshot(
            entry_id=entry.id,
            crawl_run_id=run.id,
            status=JobSnapshotStatus.SUCCEEDED,
            pagination_complete=True,
            reported_total=len(seen_source_ids),
            pages_fetched=1,
            started_at=completed_at - timedelta(minutes=1),
            completed_at=completed_at,
            seen_source_ids=seen_source_ids,
        )
    )
    return result.snapshot_id


def test_job_stays_active_when_one_source_is_fresh(monkeypatch) -> None:
    from app.tasks.expiration import expire_stale_job_sources

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = add_company(session)
        job = JobPosting(id=uuid4(), company_id=company.id, title="Engineer", normalized_title="engineer", job_type=JobType.UNKNOWN, city="unknown", description="role")
        session.add(job)
        session.flush()
        expired = add_source(session, job, now - timedelta(days=31))
        fresh = add_source(session, job, now - timedelta(days=1))
        boundary = add_source(session, job, now - timedelta(days=30))
        session.flush()
        expired_id, fresh_id, boundary_id, job_id = expired.id, fresh.id, boundary.id, job.id
        session.commit()

    monkeypatch.setattr("app.tasks.expiration.SessionLocal", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr("app.tasks.expiration.utc_now", lambda: now)

    assert expire_stale_job_sources.apply().get() == {"sources_expired": 1, "jobs_updated": 1}
    with Session(engine) as session:
        assert session.get(JobSource, expired_id).is_active is False
        assert session.get(JobSource, fresh_id).is_active is True
        assert session.get(JobSource, boundary_id).is_active is True
        assert session.get(JobPosting, job_id).is_active is True


def test_expiration_deactivates_job_when_all_sources_are_expired_idempotently(monkeypatch) -> None:
    from app.tasks.expiration import expire_stale_job_sources

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = add_company(session)
        job = JobPosting(id=uuid4(), company_id=company.id, title="Engineer", normalized_title="engineer", job_type=JobType.UNKNOWN, city="unknown", description="role")
        session.add(job)
        session.flush()
        add_source(session, job, now - timedelta(days=31))
        job_id = job.id
        session.commit()

    monkeypatch.setattr("app.tasks.expiration.SessionLocal", lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr("app.tasks.expiration.utc_now", lambda: now)

    assert expire_stale_job_sources.apply().get() == {"sources_expired": 1, "jobs_updated": 1}
    assert expire_stale_job_sources.apply().get() == {"sources_expired": 0, "jobs_updated": 0}
    with Session(engine) as session:
        assert session.get(JobPosting, job_id).is_active is False


def test_expiration_does_not_bypass_managed_source_absence_lifecycle(monkeypatch) -> None:
    from app.tasks.expiration import expire_stale_job_sources

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = add_company(session)
        entry = add_entry(session, company, "managed")
        job = JobPosting(
            id=uuid4(),
            company_id=company.id,
            title="Engineer",
            normalized_title="engineer",
            job_type=JobType.UNKNOWN,
            city="unknown",
            description="role",
        )
        session.add(job)
        session.flush()
        snapshot = add_applied_snapshot(session, entry, now - timedelta(days=31))
        managed = add_source(
            session,
            job,
            now - timedelta(days=31),
            entry_id=entry.id,
            missing_complete_snapshots=1,
            lifecycle_managed=True,
        )
        seen = add_source(
            session,
            job,
            now - timedelta(days=31),
            entry_id=entry.id,
            last_seen_snapshot_id=snapshot.id,
            lifecycle_managed=True,
        )
        session.flush()
        managed_id, seen_id, snapshot_id, job_id = (
            managed.id,
            seen.id,
            snapshot.id,
            job.id,
        )
        session.commit()

    monkeypatch.setattr("app.tasks.expiration.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.tasks.expiration.utc_now", lambda: now)

    assert expire_stale_job_sources.apply().get() == {
        "sources_expired": 0,
        "jobs_updated": 0,
    }
    with Session(engine) as session:
        persisted = session.get(JobSource, managed_id)
        assert persisted is not None
        assert persisted.is_active is True
        assert persisted.missing_complete_snapshots == 1
        seen_persisted = session.get(JobSource, seen_id)
        assert seen_persisted is not None
        assert seen_persisted.is_active is True
        assert seen_persisted.last_seen_snapshot_id == snapshot_id
        assert session.get(JobPosting, job_id).is_active is True


@pytest.mark.parametrize(
    "snapshot_status", (JobSnapshotStatus.PARTIAL, JobSnapshotStatus.FAILED)
)
def test_expiration_expires_linked_source_without_complete_lifecycle_state(
    monkeypatch,
    snapshot_status: JobSnapshotStatus,
) -> None:
    from app.tasks.expiration import expire_stale_job_sources

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = add_company(session)
        entry = add_entry(session, company, "partial-only")
        noncomplete_snapshot = JobCollectionSnapshot(
            job_entry_id=entry.id,
            status=snapshot_status,
            lifecycle_applied=False,
            pagination_complete=False,
            observed_count=1 if snapshot_status is JobSnapshotStatus.PARTIAL else 0,
            pages_fetched=1 if snapshot_status is JobSnapshotStatus.PARTIAL else 0,
            command_hash="b" * 64,
            error_code="page_timeout",
            started_at=now - timedelta(days=31, minutes=1),
            completed_at=now - timedelta(days=31),
        )
        job = JobPosting(
            id=uuid4(),
            company_id=company.id,
            title="Engineer",
            normalized_title="engineer",
            job_type=JobType.UNKNOWN,
            city="unknown",
            description="role",
        )
        session.add_all((noncomplete_snapshot, job))
        session.flush()
        source = add_source(
            session,
            job,
            now - timedelta(days=31),
            entry_id=entry.id,
        )
        session.flush()
        source_id, job_id = source.id, job.id
        session.commit()

    monkeypatch.setattr("app.tasks.expiration.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.tasks.expiration.utc_now", lambda: now)

    assert expire_stale_job_sources.apply().get() == {
        "sources_expired": 1,
        "jobs_updated": 1,
    }
    with Session(engine) as session:
        persisted_source = session.get(JobSource, source_id)
        persisted_job = session.get(JobPosting, job_id)
        assert persisted_source is not None
        assert persisted_job is not None
        assert persisted_source.is_active is False
        assert persisted_source.last_seen_snapshot_id is None
        assert persisted_source.missing_complete_snapshots == 0
        assert persisted_job.is_active is False


def test_applied_snapshot_deletion_does_not_return_retained_source_to_fallback(
    monkeypatch,
) -> None:
    from app.tasks.expiration import expire_stale_job_sources

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_foreign_key_engine()
    with Session(engine, expire_on_commit=False) as session:
        company = add_company(session)
        entry = add_entry(session, company, "deleted-snapshot")
        job = JobPosting(
            id=uuid4(),
            company_id=company.id,
            title="Engineer",
            normalized_title="engineer",
            job_type=JobType.UNKNOWN,
            city="unknown",
            description="role",
        )
        session.add(job)
        session.flush()
        source = add_source(
            session,
            job,
            now - timedelta(days=31),
            entry_id=entry.id,
        )
        session.commit()
        snapshot_id = record_complete_snapshot(
            session,
            company,
            entry,
            completed_at=now - timedelta(days=31),
            seen_source_ids={source.id},
        )
        snapshot = session.get(JobCollectionSnapshot, snapshot_id)
        assert snapshot is not None
        session.delete(snapshot)
        session.commit()
        session.refresh(source)
        assert source.job_entry_id == entry.id
        assert source.last_seen_snapshot_id is None
        assert source.missing_complete_snapshots == 0
        assert source.lifecycle_managed is True
        source_id, job_id = source.id, job.id

    monkeypatch.setattr("app.tasks.expiration.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.tasks.expiration.utc_now", lambda: now)

    assert expire_stale_job_sources.apply().get() == {
        "sources_expired": 0,
        "jobs_updated": 0,
    }
    with Session(engine) as session:
        assert session.get(JobSource, source_id).is_active is True
        assert session.get(JobPosting, job_id).is_active is True


def test_entry_deletion_returns_managed_source_with_one_absence_to_fallback(
    monkeypatch,
) -> None:
    from app.tasks.expiration import expire_stale_job_sources

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_foreign_key_engine()
    with Session(engine, expire_on_commit=False) as session:
        company = add_company(session)
        entry = add_entry(session, company, "deleted-entry")
        job = JobPosting(
            id=uuid4(),
            company_id=company.id,
            title="Engineer",
            normalized_title="engineer",
            job_type=JobType.UNKNOWN,
            city="unknown",
            description="role",
        )
        session.add(job)
        session.flush()
        source = add_source(
            session,
            job,
            now - timedelta(days=31),
            entry_id=entry.id,
        )
        session.commit()
        record_complete_snapshot(
            session,
            company,
            entry,
            completed_at=now,
            seen_source_ids=set(),
        )
        assert source.missing_complete_snapshots == 1
        session.delete(entry)
        session.commit()
        session.refresh(source)
        assert source.job_entry_id is None
        assert source.last_seen_snapshot_id is None
        assert source.missing_complete_snapshots == 1
        assert source.lifecycle_managed is True
        source_id, job_id = source.id, job.id

    monkeypatch.setattr("app.tasks.expiration.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.tasks.expiration.utc_now", lambda: now)

    assert expire_stale_job_sources.apply().get() == {
        "sources_expired": 1,
        "jobs_updated": 1,
    }
    with Session(engine) as session:
        assert session.get(JobSource, source_id).is_active is False
        assert session.get(JobPosting, job_id).is_active is False


def test_expiration_recomputes_mixed_postings_from_legacy_sources_only(monkeypatch) -> None:
    from app.tasks.expiration import expire_stale_job_sources

    now = datetime(2026, 8, 3, 2, 0, tzinfo=UTC)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = add_company(session)
        entry = add_entry(session, company, "mixed")
        mixed_job = JobPosting(
            id=uuid4(),
            company_id=company.id,
            title="Engineer",
            normalized_title="engineer",
            job_type=JobType.UNKNOWN,
            city="unknown",
            description="role",
        )
        legacy_only_job = JobPosting(
            id=uuid4(),
            company_id=company.id,
            title="Designer",
            normalized_title="designer",
            job_type=JobType.UNKNOWN,
            city="unknown",
            description="role",
        )
        session.add_all((mixed_job, legacy_only_job))
        session.flush()
        mixed_legacy = add_source(session, mixed_job, now - timedelta(days=31))
        mixed_managed = add_source(
            session,
            mixed_job,
            now - timedelta(days=31),
            entry_id=entry.id,
            missing_complete_snapshots=1,
            lifecycle_managed=True,
        )
        legacy_only = add_source(session, legacy_only_job, now - timedelta(days=31))
        session.flush()
        ids = (
            mixed_legacy.id,
            mixed_managed.id,
            legacy_only.id,
            mixed_job.id,
            legacy_only_job.id,
        )
        session.commit()

    monkeypatch.setattr("app.tasks.expiration.SessionLocal", lambda: Session(engine))
    monkeypatch.setattr("app.tasks.expiration.utc_now", lambda: now)

    assert expire_stale_job_sources.apply().get() == {
        "sources_expired": 2,
        "jobs_updated": 2,
    }
    with Session(engine) as session:
        mixed_legacy_id, mixed_managed_id, legacy_only_id, mixed_job_id, legacy_job_id = ids
        assert session.get(JobSource, mixed_legacy_id).is_active is False
        assert session.get(JobSource, mixed_managed_id).is_active is True
        assert session.get(JobSource, legacy_only_id).is_active is False
        assert session.get(JobPosting, mixed_job_id).is_active is True
        assert session.get(JobPosting, legacy_job_id).is_active is False

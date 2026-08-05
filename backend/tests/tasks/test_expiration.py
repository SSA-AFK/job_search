from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Company, JobEntry, JobPosting, JobSource, JobType


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
    missing_complete_snapshots: int = 0,
) -> JobSource:
    source = JobSource(
        job_posting_id=job.id,
        job_entry_id=entry_id,
        provider="test",
        source_raw_id=str(uuid4()),
        apply_url="https://example.test/jobs/1",
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        missing_complete_snapshots=missing_complete_snapshots,
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
        managed = add_source(
            session,
            job,
            now - timedelta(days=31),
            entry_id=entry.id,
            missing_complete_snapshots=1,
        )
        session.flush()
        managed_id, job_id = managed.id, job.id
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
        assert session.get(JobPosting, job_id).is_active is True


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

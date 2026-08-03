from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Company, JobPosting, JobSource, JobType


def add_company(session: Session) -> Company:
    record = Company(
        id=uuid4(), canonical_name="Acme", normalized_name="acme", funding_stage="unknown", scale="unknown"
    )
    session.add(record)
    session.flush()
    return record


def add_source(session: Session, job: JobPosting, seen_at: datetime) -> JobSource:
    source = JobSource(
        job_posting_id=job.id, provider="test", source_raw_id=str(uuid4()),
        apply_url="https://example.test/jobs/1", first_seen_at=seen_at, last_seen_at=seen_at,
    )
    session.add(source)
    return source


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

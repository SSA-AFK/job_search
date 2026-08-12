from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.job_enumeration.contracts import (
    ExternalJobCandidate,
    JobEnumerationResult,
    JobEnumerationStatus,
)
from app.job_enumeration.persistence import JobEnumerationPersistence
from app.models import (
    Base,
    CollectionRequest,
    Company,
    CrawlRun,
    JobCollectionSnapshot,
    JobPosting,
    JobSource,
)
from app.models.enums import CollectionStatus, RunType


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _run(session: Session, company: Company):
    request = CollectionRequest(query=company.canonical_name, normalized_query=company.normalized_name)
    session.add(request)
    session.flush()
    run = CrawlRun(
        collection_request_id=request.id,
        company_id=company.id,
        run_type=RunType.COMPANY_REFRESH,
        status=CollectionStatus.RUNNING,
        claim_token="token",
    )
    session.add(run)
    session.commit()
    return run


def _candidate(source_id: str, observed_at: datetime) -> ExternalJobCandidate:
    return ExternalJobCandidate(
        source_provider="jobhunt:acme",
        source_raw_id=source_id,
        title="AI Engineer",
        apply_url=f"https://jobs.acme.example/{source_id}",
        job_type="social",
        city="上海",
        observed_at=observed_at,
    )


def test_complete_result_writes_lifecycle_snapshot(session) -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.commit()
    run = _run(session, company)
    result = JobEnumerationResult(
        status=JobEnumerationStatus.SOURCE_SUCCEEDED,
        jobs=(_candidate("job-1", now),),
        source_key="acme",
        pagination_complete=True,
    )

    JobEnumerationPersistence(session).persist(
        company_id=company.id,
        entry_url="https://jobs.acme.example",
        crawl_run_id=run.id,
        result=result,
        started_at=now - timedelta(minutes=1),
        completed_at=now,
    )

    assert session.query(JobPosting).count() == 1
    assert session.query(JobSource).one().lifecycle_managed is True
    snapshot = session.query(JobCollectionSnapshot).one()
    assert snapshot.pagination_complete is True
    assert snapshot.lifecycle_applied is True


def test_partial_result_never_applies_lifecycle(session) -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.commit()
    run = _run(session, company)
    result = JobEnumerationResult(
        status=JobEnumerationStatus.SOURCE_PARTIAL,
        jobs=(_candidate("job-1", now),),
        source_key="acme",
        error_code="jobhunt_source_unavailable",
    )

    JobEnumerationPersistence(session).persist(
        company_id=company.id,
        entry_url="https://jobs.acme.example",
        crawl_run_id=run.id,
        result=result,
        started_at=now - timedelta(minutes=1),
        completed_at=now,
    )

    assert session.query(JobCollectionSnapshot).one().lifecycle_applied is False

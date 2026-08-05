from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Company,
    CrawlRun,
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobSnapshotStatus,
    RunType,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.fixture
def company(session: Session) -> Company:
    value = Company(canonical_name="Example", normalized_name="example")
    session.add(value)
    session.commit()
    return value


@pytest.fixture
def crawl_run(session: Session) -> CrawlRun:
    value = CrawlRun(run_type=RunType.COMPANY_REFRESH, providers_attempted=[])
    session.add(value)
    session.commit()
    return value


def create_entry(company: Company) -> JobEntry:
    return JobEntry(
        company_id=company.id,
        url="https://careers.example.com/jobs",
        normalized_url="https://careers.example.com/jobs",
        provider="official",
        platform="custom",
    )


def create_snapshot(entry: JobEntry, crawl_run: CrawlRun) -> JobCollectionSnapshot:
    now = datetime.now(UTC)
    return JobCollectionSnapshot(
        job_entry_id=entry.id,
        crawl_run_id=crawl_run.id,
        status=JobSnapshotStatus.SUCCEEDED,
        pagination_complete=True,
        empty_confirmed=True,
        reported_total=0,
        content_fingerprint="a" * 64,
        command_hash="b" * 64,
        started_at=now,
        completed_at=now,
    )


def test_job_entry_identity_is_company_and_normalized_url_scoped(
    session: Session, company: Company
) -> None:
    session.add(create_entry(company))
    session.commit()
    session.add(create_entry(company))

    with pytest.raises(IntegrityError):
        session.commit()


def test_entry_and_snapshot_defaults_match_coverage_contract(
    session: Session, company: Company, crawl_run: CrawlRun
) -> None:
    entry = create_entry(company)
    session.add(entry)
    session.flush()
    snapshot = JobCollectionSnapshot(
        job_entry_id=entry.id,
        crawl_run_id=crawl_run.id,
        status=JobSnapshotStatus.SUCCEEDED,
        pagination_complete=True,
        empty_confirmed=True,
        reported_total=0,
        content_fingerprint="a" * 64,
        command_hash="b" * 64,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(snapshot)
    session.flush()

    assert entry.status is JobEntryStatus.UNKNOWN
    assert entry.requires_rendering is False
    assert entry.failure_count == 0
    assert snapshot.observed_count == 0
    assert snapshot.pages_fetched == 0
    for column_name in ("failure_count",):
        assert JobEntry.__table__.c[column_name].server_default is not None
    for column_name in ("observed_count", "pages_fetched"):
        assert JobCollectionSnapshot.__table__.c[column_name].server_default is not None


def test_deleting_company_cascades_entries_and_snapshots(
    session: Session, company: Company, crawl_run: CrawlRun
) -> None:
    entry = create_entry(company)
    session.add(entry)
    session.flush()
    session.add(create_snapshot(entry, crawl_run))
    session.commit()

    session.delete(company)
    session.commit()

    assert session.scalar(select(func.count()).select_from(JobEntry)) == 0
    assert session.scalar(select(func.count()).select_from(JobCollectionSnapshot)) == 0


def test_deleting_crawl_run_keeps_snapshot_with_null_run_id(
    session: Session, company: Company, crawl_run: CrawlRun
) -> None:
    entry = create_entry(company)
    session.add(entry)
    session.flush()
    snapshot = create_snapshot(entry, crawl_run)
    session.add(snapshot)
    session.commit()

    session.delete(crawl_run)
    session.commit()
    session.refresh(snapshot)

    assert snapshot.crawl_run_id is None

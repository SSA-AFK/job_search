from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
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


def create_snapshot(
    entry: JobEntry,
    crawl_run: CrawlRun | None,
    *,
    status: JobSnapshotStatus = JobSnapshotStatus.SUCCEEDED,
    command_hash: str = "b" * 64,
) -> JobCollectionSnapshot:
    now = datetime.now(UTC)
    return JobCollectionSnapshot(
        job_entry_id=entry.id,
        crawl_run_id=crawl_run.id if crawl_run is not None else None,
        status=status,
        pagination_complete=status is JobSnapshotStatus.SUCCEEDED,
        empty_confirmed=status is JobSnapshotStatus.SUCCEEDED,
        reported_total=0 if status is JobSnapshotStatus.SUCCEEDED else None,
        content_fingerprint="a" * 64,
        command_hash=command_hash,
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
        lifecycle_applied=True,
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
    assert snapshot.lifecycle_applied is True
    for column_name in ("failure_count",):
        assert JobEntry.__table__.c[column_name].server_default is not None
    for column_name in ("observed_count", "pages_fetched", "lifecycle_applied"):
        assert JobCollectionSnapshot.__table__.c[column_name].server_default is not None


def test_boolean_server_defaults_are_false_for_direct_inserts(
    session: Session, company: Company
) -> None:
    entry_id = uuid4()
    snapshot_id = uuid4()
    now = datetime.now(UTC).isoformat()
    session.execute(
        text(
            "INSERT INTO job_entries "
            "(id, company_id, url, normalized_url, provider, platform, created_at, updated_at) "
            "VALUES (:id, :company_id, :url, :url, 'official', 'custom', :now, :now)"
        ),
        {
            "id": str(entry_id),
            "company_id": str(company.id),
            "url": "https://careers.example.com/jobs/direct",
            "now": now,
        },
    )
    session.execute(
        text(
            "INSERT INTO job_collection_snapshots "
            "(id, job_entry_id, status, command_hash, started_at, completed_at, created_at) "
            "VALUES (:id, :entry_id, 'succeeded', :command_hash, :now, :now, :now)"
        ),
        {
            "id": str(snapshot_id),
            "entry_id": str(entry_id),
            "command_hash": "a" * 64,
            "now": now,
        },
    )
    session.commit()

    assert session.scalar(
        text("SELECT requires_rendering FROM job_entries WHERE id = :id"),
        {"id": str(entry_id)},
    ) == 0
    assert session.execute(
        text(
            "SELECT lifecycle_applied, pagination_complete, empty_confirmed "
            "FROM job_collection_snapshots WHERE id = :id"
        ),
        {"id": str(snapshot_id)},
    ).one() == (0, 0, 0)
    entry = session.get(JobEntry, entry_id)
    snapshot = session.get(JobCollectionSnapshot, snapshot_id)
    assert entry is not None
    assert snapshot is not None
    assert entry.requires_rendering is False
    assert snapshot.pagination_complete is False
    assert snapshot.empty_confirmed is False
    assert snapshot.lifecycle_applied is False


def test_snapshot_identity_rejects_duplicate_non_null_crawl_run(
    session: Session, company: Company, crawl_run: CrawlRun
) -> None:
    entry = create_entry(company)
    session.add(entry)
    session.flush()
    session.add(create_snapshot(entry, crawl_run))
    session.commit()
    session.add(create_snapshot(entry, crawl_run, command_hash="c" * 64))

    with pytest.raises(IntegrityError):
        session.commit()


def test_snapshot_identity_allows_multiple_null_crawl_runs(
    session: Session, company: Company
) -> None:
    entry = create_entry(company)
    session.add(entry)
    session.flush()
    session.add_all(
        [
            create_snapshot(entry, None, command_hash="c" * 64),
            create_snapshot(entry, None, command_hash="d" * 64),
        ]
    )

    session.commit()

    assert (
        session.scalar(
            select(func.count())
            .select_from(JobCollectionSnapshot)
            .where(JobCollectionSnapshot.job_entry_id == entry.id)
        )
        == 2
    )


def test_coverage_status_enums_persist_each_documented_value(
    session: Session, company: Company
) -> None:
    entry_statuses = list(JobEntryStatus)
    snapshot_statuses = list(JobSnapshotStatus)
    entries = [
        JobEntry(
            company_id=company.id,
            url=f"https://careers.example.com/jobs/{status.value}",
            normalized_url=f"https://careers.example.com/jobs/{status.value}",
            provider="official",
            platform="custom",
            status=status,
        )
        for status in entry_statuses
    ]
    crawl_runs = [
        CrawlRun(run_type=RunType.COMPANY_REFRESH, providers_attempted=[])
        for _ in snapshot_statuses
    ]
    session.add_all(entries + crawl_runs)
    session.flush()
    session.add_all(
        [
            create_snapshot(
                entries[0],
                crawl_run,
                status=status,
                command_hash=f"{index:064x}",
            )
            for index, (crawl_run, status) in enumerate(
                zip(crawl_runs, snapshot_statuses), start=1
            )
        ]
    )
    session.commit()

    assert set(session.scalars(text("SELECT status FROM job_entries"))) == {
        status.value for status in entry_statuses
    }
    assert set(session.scalars(text("SELECT status FROM job_collection_snapshots"))) == {
        status.value for status in snapshot_statuses
    }
    assert JobEntry.__table__.c.status.type.enums == [status.value for status in entry_statuses]
    assert JobCollectionSnapshot.__table__.c.status.type.enums == [
        status.value for status in snapshot_statuses
    ]


def test_coverage_status_constraints_reject_undocumented_values(
    session: Session, company: Company, crawl_run: CrawlRun
) -> None:
    session.add(
        JobEntry(
            company_id=company.id,
            url="https://careers.example.com/jobs/invalid",
            normalized_url="https://careers.example.com/jobs/invalid",
            provider="official",
            platform="custom",
            status="retired",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()

    session.rollback()
    entry = create_entry(company)
    session.add(entry)
    session.flush()
    session.add(create_snapshot(entry, crawl_run, status="cancelled"))

    with pytest.raises(IntegrityError):
        session.commit()


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

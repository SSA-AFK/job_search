from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.models import Base, Company, JobCollectionSnapshot, JobEntry, JobPosting, JobSource


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


def test_job_source_lifecycle_references_clear_when_entry_and_snapshot_are_deleted(
    session: Session,
) -> None:
    now = datetime.now(UTC)
    company = Company(canonical_name="Example", normalized_name="example")
    session.add(company)
    session.flush()
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build systems",
    )
    session.add(posting)
    session.flush()
    entry = JobEntry(
        company_id=company.id,
        url="https://careers.example.com/jobs",
        normalized_url="https://careers.example.com/jobs",
        provider="official",
        platform="custom",
    )
    session.add(entry)
    session.flush()
    snapshot = JobCollectionSnapshot(
        job_entry_id=entry.id,
        status="succeeded",
        command_hash="a" * 64,
        started_at=now,
        completed_at=now,
    )
    session.add(snapshot)
    session.flush()
    source = JobSource(
        job_posting_id=posting.id,
        job_entry_id=entry.id,
        last_seen_snapshot_id=snapshot.id,
        lifecycle_managed=True,
        provider="official",
        source_raw_id="job-1",
        apply_url="https://example.com/jobs/1",
    )
    session.add(source)
    session.commit()

    assert source.missing_complete_snapshots == 0
    assert JobSource.__table__.c.missing_complete_snapshots.server_default is not None
    assert source.lifecycle_managed is True
    assert JobSource.__table__.c.lifecycle_managed.server_default is not None

    session.delete(snapshot)
    session.commit()
    session.refresh(source)
    assert source.job_entry_id == entry.id
    assert source.last_seen_snapshot_id is None
    assert source.lifecycle_managed is True

    session.delete(entry)
    session.commit()
    session.refresh(source)
    assert source.job_entry_id is None
    assert source.lifecycle_managed is True


def test_job_source_lifecycle_managed_defaults_false(session: Session) -> None:
    company = Company(canonical_name="Default", normalized_name="default")
    session.add(company)
    session.flush()
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build systems",
    )
    session.add(posting)
    session.flush()
    source = JobSource(
        job_posting_id=posting.id,
        provider="legacy",
        source_raw_id="legacy-job-1",
        apply_url="https://example.com/legacy/jobs/1",
    )
    session.add(source)
    session.commit()

    assert source.lifecycle_managed is False


def test_job_source_lifecycle_index_matches_query_contract() -> None:
    indexes = {index.name: index for index in JobSource.__table__.indexes}

    entry_index = indexes["ix_job_sources_entry_active"]
    posting_index = indexes["ix_job_sources_posting_active"]

    assert [column.name for column in entry_index.columns] == ["job_entry_id", "is_active"]
    assert [column.name for column in posting_index.columns] == [
        "job_posting_id",
        "is_active",
    ]

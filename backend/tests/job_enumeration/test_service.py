from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.job_enumeration.contracts import JobEnumerationStatus
from app.job_enumeration.jobhunt import JobHuntCli
from app.job_enumeration.service import JobEnumerationService
from app.models import (
    Base,
    Company,
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobSnapshotStatus,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: object, _record: object) -> None:
        cursor = connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


@pytest.mark.anyio
async def test_fresh_complete_snapshot_avoids_external_calls(session) -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    company = Company(canonical_name="Acme", normalized_name="acme")
    session.add(company)
    session.flush()
    entry = JobEntry(
        company_id=company.id,
        url="https://acme.example/careers",
        normalized_url="https://acme.example/careers",
        provider="verified_entry",
        platform="company_site_careers",
        status=JobEntryStatus.ACTIVE,
    )
    session.add(entry)
    session.flush()
    session.add(
        JobCollectionSnapshot(
            job_entry_id=entry.id,
            crawl_run_id=None,
            status=JobSnapshotStatus.SUCCEEDED,
            lifecycle_applied=True,
            pagination_complete=True,
            empty_confirmed=False,
            observed_count=1,
            pages_fetched=1,
            command_hash="a" * 64,
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1),
        )
    )
    session.commit()
    jobhunt = AsyncMock(spec=JobHuntCli)
    service = JobEnumerationService(
        session, jobhunt=jobhunt, site_mapping={company.id: "acme"}
    )

    result = await service.enumerate_if_stale(company.id, now=now)

    assert result.status is JobEnumerationStatus.FRESH_DATABASE_HIT
    jobhunt.sites.assert_not_awaited()


@pytest.mark.anyio
async def test_unmapped_company_stops_without_external_calls(session) -> None:
    jobhunt = AsyncMock(spec=JobHuntCli)
    service = JobEnumerationService(session, jobhunt=jobhunt, site_mapping={})

    result = await service.enumerate_if_stale(uuid4(), now=datetime.now(UTC))

    assert result.status is JobEnumerationStatus.SOURCE_UNSUPPORTED
    jobhunt.sites.assert_not_awaited()

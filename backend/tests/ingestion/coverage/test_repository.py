from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.coverage.contracts import RecordJobSnapshot
from app.ingestion.coverage.repository import CoverageRepository
from app.models import (
    CollectionStatus,
    Company,
    CrawlRun,
    JobCollectionSnapshot,
    JobPosting,
    JobSnapshotStatus,
    JobSource,
    RunType,
)

ENTRY_FIELDS = {
    "provider": "official",
    "platform": "self_hosted",
    "requires_rendering": False,
}
NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
SHARED_URL = "https://jobs.example.com/openings"


def make_command(entry_id, run_id, *, seen_source_ids=frozenset()):
    return RecordJobSnapshot(
        entry_id=entry_id,
        crawl_run_id=run_id,
        status=JobSnapshotStatus.SUCCEEDED,
        pagination_complete=True,
        pages_fetched=1,
        started_at=NOW,
        completed_at=NOW,
        seen_source_ids=seen_source_ids,
    )


def make_run(session: Session, company_id) -> CrawlRun:
    run = CrawlRun(
        company_id=company_id,
        run_type=RunType.COMPANY_REFRESH,
        status=CollectionStatus.RUNNING,
    )
    session.add(run)
    session.flush()
    return run


def test_ensure_entry_normalizes_url_and_reuses_company_entry(
    repository: CoverageRepository, company: Company
) -> None:
    first = repository.ensure_entry(
        company.id,
        "https://jobs.example.com/openings/?utm_source=x",
        **ENTRY_FIELDS,
    )
    second = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)

    assert second.id == first.id
    assert first.normalized_url == SHARED_URL


def test_same_url_can_belong_to_different_companies(
    repository: CoverageRepository, companies: tuple[Company, Company]
) -> None:
    left = repository.ensure_entry(companies[0].id, SHARED_URL, **ENTRY_FIELDS)
    right = repository.ensure_entry(companies[1].id, SHARED_URL, **ENTRY_FIELDS)

    assert left.id != right.id


def test_ensure_entry_rejects_non_public_or_credentialed_url(
    repository: CoverageRepository, company: Company
) -> None:
    with pytest.raises(ValueError, match="public URL"):
        repository.ensure_entry(company.id, "https://localhost/jobs", **ENTRY_FIELDS)
    with pytest.raises(ValueError, match="credentials"):
        repository.ensure_entry(company.id, "https://user@example.com/jobs", **ENTRY_FIELDS)


def test_ensure_entry_rejects_missing_company_and_provenance_conflict(
    repository: CoverageRepository, company: Company
) -> None:
    with pytest.raises(ValueError, match="unknown company_id"):
        repository.ensure_entry(uuid4(), SHARED_URL, **ENTRY_FIELDS)

    repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    with pytest.raises(ValueError, match="job entry provenance conflict"):
        repository.ensure_entry(
            company.id,
            SHARED_URL,
            provider="board",
            platform="self_hosted",
            requires_rendering=False,
        )


def test_insert_snapshot_requires_existing_matching_entry_and_run(
    repository: CoverageRepository, companies: tuple[Company, Company], session: Session
) -> None:
    entry = repository.ensure_entry(companies[0].id, SHARED_URL, **ENTRY_FIELDS)
    with pytest.raises(ValueError, match="unknown crawl_run_id"):
        repository.insert_snapshot(make_command(entry.id, uuid4()))

    other_run = make_run(session, companies[1].id)
    with pytest.raises(ValueError, match="crawl run does not belong to entry company"):
        repository.insert_snapshot(make_command(entry.id, other_run.id))
    with pytest.raises(ValueError, match="unknown job_entry_id"):
        repository.insert_snapshot(make_command(uuid4(), other_run.id))


def test_insert_snapshot_persists_command_without_mutating_sources(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    run = make_run(session, company.id)
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
        is_active=False,
    )
    session.add(posting)
    session.flush()
    source = JobSource(
        job_posting_id=posting.id,
        job_entry_id=entry.id,
        provider="official",
        source_raw_id="source-1",
        apply_url="https://jobs.example.com/1",
        is_active=False,
        missing_complete_snapshots=1,
    )
    session.add(source)
    session.flush()

    snapshot = repository.insert_snapshot(make_command(entry.id, run.id, seen_source_ids={source.id}))

    assert snapshot.observed_count == 1
    assert snapshot.command_hash == make_command(entry.id, run.id, seen_source_ids={source.id}).command_hash()
    assert source.is_active is False
    assert source.missing_complete_snapshots == 1
    assert source.last_seen_snapshot_id is None


def test_duplicate_snapshot_is_rejected_by_database_uniqueness(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    run = make_run(session, company.id)
    repository.insert_snapshot(make_command(entry.id, run.id))

    with pytest.raises(ValueError, match="snapshot already exists"):
        repository.insert_snapshot(make_command(entry.id, run.id))

    assert len(session.scalars(select(JobCollectionSnapshot)).all()) == 1


def test_lock_entry_sources_returns_stable_id_order(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
    )
    session.add(posting)
    session.flush()
    ids = sorted((uuid4(), uuid4(), uuid4()))
    for index, source_id in enumerate(reversed(ids)):
        session.add(
            JobSource(
                id=source_id,
                job_posting_id=posting.id,
                job_entry_id=entry.id,
                provider="official",
                source_raw_id=f"source-{index}",
                apply_url=f"https://jobs.example.com/{index}",
            )
        )
    session.flush()

    assert tuple(source.id for source in repository.lock_entry_sources(entry.id)) == tuple(ids)


def test_recompute_job_activity_keeps_posting_active_when_any_source_is_active(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    posting = JobPosting(
        company_id=company.id,
        title="Engineer",
        normalized_title="engineer",
        city="Shanghai",
        description="Build",
        is_active=False,
    )
    session.add(posting)
    session.flush()
    session.add_all(
        (
            JobSource(
                job_posting_id=posting.id,
                provider="official",
                source_raw_id="inactive",
                apply_url="https://jobs.example.com/inactive",
                is_active=False,
            ),
            JobSource(
                job_posting_id=posting.id,
                provider="board",
                source_raw_id="active",
                apply_url="https://jobs.example.com/active",
                is_active=True,
            ),
        )
    )
    session.flush()

    assert repository.recompute_job_activity({posting.id}) == 1
    assert posting.is_active is True


def test_repository_flushes_without_ending_outer_transaction(
    repository: CoverageRepository, company: Company, session: Session
) -> None:
    session.commit()
    with session.begin():
        entry = repository.ensure_entry(company.id, SHARED_URL, **ENTRY_FIELDS)
        assert session.in_transaction()
        session.add(Company(canonical_name="Later", normalized_name="later"))
        assert entry.id is not None

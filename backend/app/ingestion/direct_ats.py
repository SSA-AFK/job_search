import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name, normalize_url
from app.ingestion.contracts import ParsedJob
from app.models import (
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobPosting,
    JobSnapshotStatus,
    JobSource,
)


@dataclass(frozen=True)
class DirectAtsWriteResult:
    jobs_written: int
    observed_count: int


def write_direct_ats_jobs(
    session: Session, *, company_id: UUID, entry_url: str, platform: str, jobs: tuple[ParsedJob, ...], crawl_run_id: UUID | None = None
) -> DirectAtsWriteResult:
    now = datetime.now(UTC)
    normalized_entry_url = normalize_url(entry_url)
    entry = session.scalar(select(JobEntry).where(JobEntry.company_id == company_id, JobEntry.normalized_url == normalized_entry_url))
    if entry is None:
        entry = JobEntry(company_id=company_id, url=entry_url, normalized_url=normalized_entry_url, provider="ats", platform=platform, status=JobEntryStatus.ACTIVE)
        session.add(entry)
        session.flush()
    entry.status = JobEntryStatus.ACTIVE
    entry.last_checked_at = now
    entry.last_success_at = now
    written = 0
    for parsed in jobs:
        normalized_title = normalize_name(parsed.title)
        city = parsed.city or ""
        job = session.scalar(select(JobPosting).where(JobPosting.company_id == company_id, JobPosting.normalized_title == normalized_title, JobPosting.city == city))
        if job is None:
            job = JobPosting(company_id=company_id, title=parsed.title, normalized_title=normalized_title, city=city, description="", is_active=True)
            session.add(job)
            session.flush()
            written += 1
        source_id = parsed.source_raw_id or parsed.url
        source = session.scalar(select(JobSource).where(JobSource.provider == (parsed.provider or f"ats_{platform}"), JobSource.source_raw_id == source_id))
        if source is None:
            session.add(JobSource(job_posting_id=job.id, job_entry_id=entry.id, provider=parsed.provider or f"ats_{platform}", source_raw_id=source_id, apply_url=parsed.url, first_seen_at=now, last_seen_at=now, is_active=True))
        else:
            source.job_posting_id = job.id
            source.job_entry_id = entry.id
            source.apply_url = parsed.url
            source.last_seen_at = now
            source.is_active = True
    session.add(JobCollectionSnapshot(job_entry_id=entry.id, crawl_run_id=crawl_run_id, status=JobSnapshotStatus.SUCCEEDED, lifecycle_applied=True, pagination_complete=True, empty_confirmed=not jobs, observed_count=len(jobs), pages_fetched=1, command_hash=hashlib.sha256(normalized_entry_url.encode()).hexdigest(), started_at=now, completed_at=now))
    session.commit()
    return DirectAtsWriteResult(jobs_written=written, observed_count=len(jobs))

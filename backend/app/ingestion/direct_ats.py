import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cache.base import CompanyCache
from app.core.normalization import normalize_name, normalize_url
from app.ingestion.contracts import ParsedJob
from app.ingestion.persistence.result import PersistenceResult
from app.models import (
    Company,
    CompanyAlias,
    JobCollectionSnapshot,
    JobEntry,
    JobEntryStatus,
    JobPosting,
    JobSnapshotStatus,
    JobSource,
    VerificationStatus,
)
from app.models.enums import JobType


@dataclass(frozen=True)
class DirectAtsWriteResult:
    jobs_written: int
    observed_count: int


class DirectAtsPersistence:
    """Writes structured ATS results only for an already-known company identity."""

    def __init__(self, session: Session, *, cache: CompanyCache | None = None) -> None:
        self._session = session
        self._cache = cache

    def persist(
        self,
        *,
        company_name: str,
        entry_url: str,
        platform: str,
        jobs: tuple[ParsedJob, ...],
        crawl_run_id: UUID,
    ) -> PersistenceResult | None:
        company_id = self.resolve_company_id(company_name)
        if company_id is None:
            return None
        result = write_direct_ats_jobs(
            self._session,
            company_id=company_id,
            entry_url=entry_url,
            platform=platform,
            jobs=jobs,
            crawl_run_id=crawl_run_id,
        )
        if self._cache is not None:
            self._cache.invalidate_company(company_id)
        return PersistenceResult(
            company_id=company_id,
            documents_written=0,
            jobs_written=result.jobs_written,
            warnings=(),
        )

    def resolve_company_id(self, company_name: str) -> UUID | None:
        normalized_name = normalize_name(company_name)
        company_id = self._session.scalar(
            select(Company.id).where(Company.normalized_name == normalized_name)
        )
        if company_id is None:
            company_id = self._session.scalar(
                select(CompanyAlias.company_id).where(
                    CompanyAlias.normalized_alias == normalized_name
                )
            )
        if company_id is None:
            return None
        return company_id


def _parse_job_type(value: str | None) -> JobType:
    if value is None:
        return JobType.UNKNOWN
    try:
        return JobType(value)
    except ValueError:
        return JobType.UNKNOWN


def _merge_job_from_parsed(job: JobPosting, parsed: ParsedJob) -> None:
    """Merge ParsedJob data into an existing JobPosting, preferring non-empty values."""
    parsed_job_type = _parse_job_type(parsed.job_type)
    if parsed_job_type is not JobType.UNKNOWN:
        existing_type = getattr(job.job_type, "value", str(job.job_type))
        if existing_type in {"unknown", parsed_job_type.value}:
            job.job_type = parsed_job_type
    if parsed.description and len(parsed.description) > len(job.description):
        job.description = parsed.description
    if parsed.salary_min_monthly is not None:
        job.salary_min_monthly = parsed.salary_min_monthly
    if parsed.salary_max_monthly is not None:
        job.salary_max_monthly = parsed.salary_max_monthly
    if parsed.salary_months is not None:
        job.salary_months = parsed.salary_months
    if parsed.posted_at is not None and (job.posted_at is None or parsed.posted_at < job.posted_at):
        job.posted_at = parsed.posted_at


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
    session.query(JobEntry).filter(
        JobEntry.company_id == company_id, JobEntry.id != entry.id
    ).update({JobEntry.is_primary: False}, synchronize_session=False)
    entry.status = JobEntryStatus.ACTIVE
    entry.is_primary = True
    entry.verification_status = VerificationStatus.VERIFIED
    entry.verified_at = now
    entry.last_checked_at = now
    entry.last_success_at = now
    written = 0
    for parsed in jobs:
        normalized_title = normalize_name(parsed.title)
        city = parsed.city or ""
        job = session.scalar(select(JobPosting).where(JobPosting.company_id == company_id, JobPosting.normalized_title == normalized_title, JobPosting.city == city))
        if job is None:
            job = JobPosting(
                company_id=company_id,
                title=parsed.title,
                normalized_title=normalized_title,
                city=city,
                job_type=_parse_job_type(parsed.job_type),
                salary_min_monthly=parsed.salary_min_monthly,
                salary_max_monthly=parsed.salary_max_monthly,
                salary_months=parsed.salary_months,
                description=parsed.description or "",
                posted_at=parsed.posted_at,
                is_active=True,
            )
            session.add(job)
            session.flush()
            written += 1
        else:
            _merge_job_from_parsed(job, parsed)
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

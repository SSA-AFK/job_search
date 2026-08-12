from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name
from app.ingestion.coverage.contracts import RecordJobSnapshot
from app.ingestion.coverage.repository import CoverageRepository
from app.ingestion.coverage.service import JobCoverageService
from app.job_enumeration.contracts import JobEnumerationResult, JobEnumerationStatus
from app.models import JobPosting, JobSource
from app.models.enums import JobSnapshotStatus, JobType


@dataclass(frozen=True, slots=True)
class EnumerationPersistenceResult:
    jobs_created: int
    sources_created: int


class JobEnumerationPersistence:
    def __init__(self, session: Session) -> None:
        self._session = session

    def persist(
        self,
        *,
        company_id: UUID,
        entry_url: str,
        crawl_run_id: UUID,
        result: JobEnumerationResult,
        started_at,
        completed_at,
    ) -> EnumerationPersistenceResult:
        repository = CoverageRepository(self._session)
        entry = repository.ensure_entry(
            company_id,
            entry_url,
            provider="jobhunt",
            platform=result.source_key or "jobhunt",
            requires_rendering=False,
        )
        jobs_created = 0
        sources_created = 0
        seen_source_ids: set[UUID] = set()
        for candidate in result.jobs:
            source = self._session.scalar(
                select(JobSource).where(
                    JobSource.provider == candidate.source_provider,
                    JobSource.source_raw_id == candidate.source_raw_id,
                )
            )
            if source is not None:
                job = self._session.get(JobPosting, source.job_posting_id)
                if job is None or job.company_id != company_id:
                    raise ValueError("job source belongs to another company")
                source.job_entry_id = entry.id
                source.last_seen_at = max(source.last_seen_at, candidate.observed_at)
                source.apply_url = str(candidate.apply_url)
                source.is_active = True
                seen_source_ids.add(source.id)
                continue
            city = candidate.city or ""
            normalized_title = normalize_name(candidate.title)
            job = self._session.scalar(
                select(JobPosting).where(
                    JobPosting.company_id == company_id,
                    JobPosting.normalized_title == normalized_title,
                    JobPosting.city == city,
                )
            )
            if job is None:
                job = JobPosting(
                    company_id=company_id,
                    title=candidate.title,
                    normalized_title=normalized_title,
                    job_type=_job_type(candidate.job_type),
                    city=city,
                    description=candidate.description or "",
                    is_active=True,
                )
                self._session.add(job)
                self._session.flush()
                jobs_created += 1
            source = JobSource(
                job_posting_id=job.id,
                job_entry_id=entry.id,
                provider=candidate.source_provider,
                source_raw_id=candidate.source_raw_id,
                apply_url=str(candidate.apply_url),
                first_seen_at=candidate.observed_at,
                last_seen_at=candidate.observed_at,
                is_active=True,
            )
            self._session.add(source)
            self._session.flush()
            sources_created += 1
            seen_source_ids.add(source.id)
        self._session.commit()

        snapshot_status, error_code = _snapshot_status(result)
        command = RecordJobSnapshot(
            entry_id=entry.id,
            crawl_run_id=crawl_run_id,
            status=snapshot_status,
            pagination_complete=result.pagination_complete,
            empty_confirmed=result.empty_confirmed,
            reported_total=len(result.jobs) if result.pagination_complete else None,
            pages_fetched=1 if result.status is not JobEnumerationStatus.SOURCE_FAILED else 0,
            error_code=error_code,
            started_at=started_at,
            completed_at=completed_at,
            seen_source_ids=frozenset(seen_source_ids),
        )
        JobCoverageService(self._session).record(command)
        return EnumerationPersistenceResult(jobs_created, sources_created)


def _snapshot_status(result: JobEnumerationResult) -> tuple[JobSnapshotStatus, str | None]:
    if result.status is JobEnumerationStatus.SOURCE_SUCCEEDED:
        return JobSnapshotStatus.SUCCEEDED, None
    if result.status is JobEnumerationStatus.SOURCE_PARTIAL:
        return JobSnapshotStatus.PARTIAL, result.error_code or "jobhunt_source_unavailable"
    return JobSnapshotStatus.FAILED, result.error_code or "jobhunt_source_unavailable"


def _job_type(value: str | None) -> JobType:
    mapping = {
        "social": JobType.EXPERIENCED,
        "campus": JobType.CAMPUS,
        "intern": JobType.INTERNSHIP,
    }
    return mapping.get(value or "", JobType.UNKNOWN)

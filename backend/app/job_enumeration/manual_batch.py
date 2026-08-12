from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name
from app.imports.boss_json import BossImportFile, BossImportRecord
from app.models import Company, CompanyAlias, JobPosting, JobSource
from app.models.enums import JobType


@dataclass(frozen=True, slots=True)
class ManualImportSummary:
    jobs_created: int
    sources_created: int
    records_unmatched: int
    records_rejected: int


class ManualBossImportService:
    """Import observed BOSS jobs without creating companies or complete snapshots."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def import_file(self, batch: BossImportFile) -> ManualImportSummary:
        jobs_created = 0
        sources_created = 0
        unmatched = 0
        for record in batch.records:
            company_id = self._resolve_company(record)
            if company_id is None:
                unmatched += 1
                continue
            source = self._session.scalar(
                select(JobSource).where(
                    JobSource.provider == record.job.source_provider,
                    JobSource.source_raw_id == record.job.source_raw_id,
                )
            )
            if source is not None:
                existing_job = self._session.get(JobPosting, source.job_posting_id)
                if existing_job is None or existing_job.company_id != company_id:
                    unmatched += 1
                    continue
                source.last_seen_at = max(source.last_seen_at, record.job.observed_at)
                source.apply_url = str(record.job.apply_url)
                source.is_active = True
                continue
            city = record.job.city or ""
            normalized_title = normalize_name(record.job.title)
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
                    title=record.job.title,
                    normalized_title=normalized_title,
                    job_type=JobType.UNKNOWN,
                    city=city,
                    description=record.job.description or "",
                    is_active=True,
                )
                self._session.add(job)
                self._session.flush()
                jobs_created += 1
            self._session.add(
                JobSource(
                    job_posting_id=job.id,
                    job_entry_id=None,
                    provider=record.job.source_provider,
                    source_raw_id=record.job.source_raw_id,
                    apply_url=str(record.job.apply_url),
                    first_seen_at=record.job.observed_at,
                    last_seen_at=record.job.observed_at,
                    is_active=True,
                    lifecycle_managed=False,
                )
            )
            sources_created += 1
        self._session.commit()
        return ManualImportSummary(
            jobs_created=jobs_created,
            sources_created=sources_created,
            records_unmatched=unmatched,
            records_rejected=batch.rejected_records,
        )

    def _resolve_company(self, record: BossImportRecord) -> UUID | None:
        normalized = normalize_name(record.company_name)
        company_ids = set(
            self._session.scalars(
                select(Company.id).where(Company.normalized_name == normalized)
            )
        )
        company_ids.update(
            self._session.scalars(
                select(CompanyAlias.company_id).where(
                    CompanyAlias.normalized_alias == normalized
                )
            )
        )
        return next(iter(company_ids)) if len(company_ids) == 1 else None

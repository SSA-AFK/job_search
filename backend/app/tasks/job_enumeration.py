"""Explicit JobHunt enumeration task; never used as an automatic fallback."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.job_enumeration.contracts import JobEnumerationStatus
from app.job_enumeration.jobhunt import JobHuntCli
from app.job_enumeration.persistence import JobEnumerationPersistence
from app.job_enumeration.service import JobEnumerationService
from app.job_enumeration.site_registry import load_site_mapping
from app.models import JobEntry
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.job_enumeration.run_job_enumeration")
def run_job_enumeration(company_id: str, crawl_run_id: str) -> dict[str, object]:
    return asyncio.run(_run(UUID(company_id), UUID(crawl_run_id)))


async def _run(company_id: UUID, crawl_run_id: UUID) -> dict[str, object]:
    if not settings.jobhunt_enabled or not settings.jobhunt_executable:
        return _failure(company_id, "jobhunt_not_installed")
    session = SessionLocal()
    try:
        mapping = load_site_mapping(Path(settings.jobhunt_site_registry_path), session)
        service = JobEnumerationService(
            session,
            jobhunt=JobHuntCli(
                executable=Path(settings.jobhunt_executable),
                expected_version=settings.jobhunt_expected_version,
                timeout_seconds=settings.jobhunt_timeout_seconds,
            ),
            site_mapping=mapping,
            freshness_hours=settings.job_freshness_hours,
        )
        started_at = datetime.now(UTC)
        result = await service.enumerate_if_stale(company_id, now=started_at)
        if result.status in {
            JobEnumerationStatus.FRESH_DATABASE_HIT,
            JobEnumerationStatus.SOURCE_UNSUPPORTED,
            JobEnumerationStatus.SOURCE_FAILED,
        }:
            return {
                "company_id": str(company_id),
                "status": result.status.value,
                "jobs_found": len(result.jobs),
                "jobs_written": 0,
                "error_code": result.error_code,
            }
        entry = session.scalar(
            select(JobEntry)
            .where(JobEntry.company_id == company_id, JobEntry.is_primary.is_(True))
            .order_by(JobEntry.id)
        )
        entry_url = entry.url if entry is not None else _source_entry_url(result)
        if entry_url is None:
            return _failure(company_id, "jobhunt_source_unavailable")
        persisted = JobEnumerationPersistence(session).persist(
            company_id=company_id,
            entry_url=entry_url,
            crawl_run_id=crawl_run_id,
            result=result,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        return {
            "company_id": str(company_id),
            "status": result.status.value,
            "jobs_found": len(result.jobs),
            "jobs_written": persisted.jobs_created,
            "error_code": result.error_code,
        }
    finally:
        session.close()


def _failure(company_id: UUID, code: str) -> dict[str, object]:
    return {
        "company_id": str(company_id),
        "status": JobEnumerationStatus.SOURCE_FAILED.value,
        "jobs_found": 0,
        "jobs_written": 0,
        "error_code": code,
    }


def _source_entry_url(result) -> str | None:
    if not result.jobs:
        return None
    parsed = urlsplit(str(result.jobs[0].apply_url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))

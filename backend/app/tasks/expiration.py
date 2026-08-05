"""Daily expiration of stale job sources and derived posting activity."""

from datetime import timedelta
from typing import Any, cast

from sqlalchemy import exists, select, update
from sqlalchemy.engine import CursorResult

from app.core.database import SessionLocal
from app.models import JobPosting, JobSource
from app.models.base import utc_now
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.expiration.expire_stale_job_sources")
def expire_stale_job_sources() -> dict[str, int]:
    """Deactivate only sources older than 30 days and recompute their postings atomically."""
    cutoff = utc_now() - timedelta(days=30)
    session = SessionLocal()
    try:
        stale_legacy_source = (
            JobSource.is_active.is_(True),
            JobSource.job_entry_id.is_(None),
            JobSource.last_seen_at < cutoff,
        )
        affected_job_ids = session.scalars(
            select(JobSource.job_posting_id).where(*stale_legacy_source)
        ).all()
        if not affected_job_ids:
            return {"sources_expired": 0, "jobs_updated": 0}

        source_result = session.execute(
            update(JobSource)
            .where(*stale_legacy_source)
            .values(is_active=False)
        )
        active_source = exists(
            select(JobSource.id).where(
                JobSource.job_posting_id == JobPosting.id,
                JobSource.is_active.is_(True),
            )
        )
        unique_job_ids = set(affected_job_ids)
        session.execute(
            update(JobPosting)
            .where(JobPosting.id.in_(unique_job_ids))
            .values(is_active=active_source)
        )
        session.commit()
        return {
            "sources_expired": cast(CursorResult[Any], source_result).rowcount,
            "jobs_updated": len(unique_job_ids),
        }
    finally:
        session.close()

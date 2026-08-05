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
        affected_job_ids = session.scalars(
            select(JobSource.job_posting_id).where(
                JobSource.is_active.is_(True), JobSource.last_seen_at < cutoff
            )
        ).all()
        if not affected_job_ids:
            return {"sources_expired": 0, "jobs_updated": 0}

        source_result = session.execute(
            update(JobSource)
            .where(JobSource.is_active.is_(True), JobSource.last_seen_at < cutoff)
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

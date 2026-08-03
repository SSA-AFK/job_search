"""Daily scheduling of stale company collection runs."""

from datetime import timedelta
from uuid import uuid4

import kombu.exceptions as kombu_exceptions  # type: ignore[import-untyped]
from sqlalchemy import or_, select

from app.core.database import SessionLocal
from app.models import CollectionRequest, CollectionStatus, Company, CrawlRun, RunType
from app.models.base import utc_now
from app.tasks.celery_app import celery_app
from app.tasks.collection import run_ingestion

_ACTIVE_STATUSES = (CollectionStatus.QUEUED, CollectionStatus.RUNNING)
KombuOperationalError = kombu_exceptions.OperationalError


@celery_app.task(name="app.tasks.schedule.enqueue_stale_companies")
def enqueue_stale_companies() -> dict[str, int]:
    """Create one refresh request/run per stale company and enqueue in stable order."""
    cutoff = utc_now() - timedelta(hours=24)
    session = SessionLocal()
    try:
        stale_companies = session.scalars(
            select(Company)
            .where(or_(Company.last_collected_at.is_(None), Company.last_collected_at < cutoff))
            .order_by(Company.canonical_name, Company.id)
        ).all()
        enqueued = 0
        skipped_active = 0
        for company in stale_companies:
            active_request = session.scalar(
                select(CollectionRequest.id).where(
                    CollectionRequest.company_id == company.id,
                    CollectionRequest.status.in_(_ACTIVE_STATUSES),
                )
            )
            active_run = session.scalar(
                select(CrawlRun.id).where(
                    CrawlRun.company_id == company.id,
                    CrawlRun.status.in_(_ACTIVE_STATUSES),
                )
            )
            if active_request is not None or active_run is not None:
                skipped_active += 1
                continue

            request = CollectionRequest(
                id=uuid4(),
                query=company.canonical_name,
                normalized_query=company.normalized_name,
                company_id=company.id,
                status=CollectionStatus.QUEUED,
            )
            run = CrawlRun(
                id=uuid4(),
                collection_request_id=request.id,
                company_id=company.id,
                run_type=RunType.COMPANY_REFRESH,
                status=CollectionStatus.QUEUED,
            )
            session.add_all((request, run))
            session.commit()
            try:
                task_result = run_ingestion.delay(str(run.id))
            except (ConnectionError, OSError, KombuOperationalError):
                request.status = CollectionStatus.FAILED
                request.error_code = "collection_unavailable"
                run.status = CollectionStatus.FAILED
                run.error_code = "collection_unavailable"
                session.commit()
                continue
            run.celery_task_id = str(task_result.id)
            session.commit()
            enqueued += 1
        return {"enqueued": enqueued, "skipped_active": skipped_active}
    finally:
        session.close()

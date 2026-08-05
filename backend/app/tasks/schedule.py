"""Daily scheduling of stale company collection runs."""

from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import kombu.exceptions as kombu_exceptions  # type: ignore[import-untyped]
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from app.collection.repository import CollectionRepository
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import CollectionRequest, CollectionStatus, Company, CrawlRun, RunType
from app.models.base import utc_now
from app.tasks.celery_app import celery_app
from app.tasks.collection import run_ingestion

_ACTIVE_STATUSES = (CollectionStatus.QUEUED, CollectionStatus.RUNNING)
_ACTIVE_REQUEST_INDEX = "uq_collection_requests_active_query"
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
                    CollectionRequest.normalized_query == company.normalized_name,
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
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                if not _is_active_request_conflict(error):
                    raise
                active_request = session.scalar(
                    select(CollectionRequest.id).where(
                        CollectionRequest.normalized_query == company.normalized_name,
                        CollectionRequest.status.in_(_ACTIVE_STATUSES),
                    )
                )
                if active_request is None:
                    raise
                skipped_active += 1
                continue
            try:
                task_result = run_ingestion.delay(str(run.id))
            except (ConnectionError, OSError, KombuOperationalError):
                CollectionRepository(session).fail_queued_dispatch(
                    run.id, failed_at=utc_now()
                )
                continue
            run.celery_task_id = str(task_result.id)
            session.commit()
            enqueued += 1
        return {"enqueued": enqueued, "skipped_active": skipped_active}
    finally:
        session.close()


@celery_app.task(name="app.tasks.schedule.redispatch_stale_queued_runs")
def redispatch_stale_queued_runs() -> dict[str, int]:
    """Recover worker/broker crash gaps without creating new crawl runs."""
    now = utc_now()
    queued_cutoff = now - timedelta(seconds=settings.collection_stale_queued_seconds)
    running_cutoff = now - timedelta(seconds=settings.collection_stale_running_seconds)
    session = SessionLocal()
    try:
        repository = CollectionRepository(session)
        stale_running = session.scalars(
            select(CrawlRun)
            .where(
                CrawlRun.status == CollectionStatus.RUNNING,
                CrawlRun.started_at < running_cutoff,
            )
            .order_by(CrawlRun.created_at, CrawlRun.id)
        ).all()
        requeued = 0
        for run in stale_running:
            if run.claim_token is None:
                continue
            recovered = repository.requeue_for_retry(
                run.id, expected_claim_token=run.claim_token
            )
            if recovered is not None and recovered.status is CollectionStatus.QUEUED:
                requeued += 1

        stale_queued = session.scalars(
            select(CrawlRun)
            .where(
                CrawlRun.status == CollectionStatus.QUEUED,
                CrawlRun.celery_task_id.is_(None),
                CrawlRun.created_at < queued_cutoff,
            )
            .order_by(CrawlRun.created_at, CrawlRun.id)
        ).all()
        redispatched = 0
        for run in stale_queued:
            try:
                task_result = run_ingestion.delay(str(run.id))
            except (ConnectionError, OSError, KombuOperationalError):
                continue
            result = session.execute(
                update(CrawlRun)
                .where(
                    CrawlRun.id == run.id,
                    CrawlRun.status == CollectionStatus.QUEUED,
                    CrawlRun.celery_task_id.is_(None),
                )
                .values(celery_task_id=str(task_result.id))
            )
            session.commit()
            if cast(CursorResult[Any], result).rowcount == 1:
                redispatched += 1
        return {"redispatched": redispatched, "requeued": requeued}
    finally:
        session.close()


def _is_active_request_conflict(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == _ACTIVE_REQUEST_INDEX:
        return True
    return "collection_requests.normalized_query" in str(error.orig).lower()

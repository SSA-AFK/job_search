"""Synchronous Celery boundary for the asynchronous ingestion pipeline."""

import asyncio
from contextlib import suppress
from importlib import import_module
from typing import Any, cast
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.core.config import settings
from app.core.database import SessionLocal
from app.ingestion.errors import RetryableInfrastructureError
from app.ingestion.orchestrator import IngestionOrchestrator
from app.ingestion.production import (
    ProductionRuntimeConfigurationError,
    create_runtime_components,
)
from app.ingestion.result import IngestionResult, RunResultSource
from app.ingestion.runtime import RuntimeComponents
from app.ingestion.runtime import build_ingestion_orchestrator as compose_orchestrator
from app.models import CollectionStatus
from app.tasks.celery_app import celery_app

_MAX_INFRASTRUCTURE_RETRIES = 3


class RuntimeUnavailableError(Exception):
    """Raised when collection has no explicitly configured runtime components."""


def load_runtime_components() -> RuntimeComponents:
    """Load an optional override or the checked-in production composition."""
    factory_path = settings.collection_runtime_factory
    if not factory_path:
        try:
            return create_runtime_components(settings)
        except ProductionRuntimeConfigurationError as error:
            raise RuntimeUnavailableError("collection runtime is not configured") from error
    module_name, separator, attribute = factory_path.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeUnavailableError("COLLECTION_RUNTIME_FACTORY must use module:callable")
    try:
        factory = getattr(import_module(module_name), attribute)
        components = factory()
    except Exception as error:
        raise RuntimeUnavailableError("collection runtime factory is unavailable") from error
    if not isinstance(components, RuntimeComponents):
        raise RuntimeUnavailableError("collection runtime factory returned invalid components")
    return components


def build_runtime_orchestrator() -> tuple[
    IngestionOrchestrator, tuple[Session, Session, Session, Session]
]:
    """Create the caller-owned distinct sessions required by the ingestion runtime."""
    sessions: list[Session] = []
    try:
        for _ in range(4):
            sessions.append(SessionLocal())
        components = load_runtime_components()
        orchestrator = compose_orchestrator(
            run_state_session=sessions[0],
            dedup_read_session=sessions[1],
            identity_review_write_session=sessions[2],
            persistence_write_session=sessions[3],
            providers=components.providers,
            extractor=components.extractor,
            semantic_judge=components.semantic_judge,
        )
    except Exception:
        for session in sessions:
            session.close()
        raise
    return orchestrator, (sessions[0], sessions[1], sessions[2], sessions[3])


def _result_payload(result: IngestionResult) -> dict[str, Any]:
    return {
        "run_id": str(result.run_id),
        "status": result.status.value,
        "company_id": str(result.company_id) if result.company_id is not None else None,
        "providers_attempted": list(result.providers_attempted),
        "documents_found": result.documents_found,
        "jobs_found": result.jobs_found,
        "jobs_written": result.jobs_written,
        "error_code": result.error_code,
    }


def _mark_collection_unavailable(run_id: UUID) -> IngestionResult:
    session = SessionLocal()
    try:
        repository = CollectionRepository(session)
        claim = repository.claim_queued(run_id)
        if claim is None:
            return IngestionResult.unknown_run(run_id)
        run = claim.run
        if run.status in {CollectionStatus.SUCCEEDED, CollectionStatus.PARTIAL, CollectionStatus.FAILED}:
            return IngestionResult.from_run(cast(RunResultSource, run))
        if not claim.claimed:
            return IngestionResult.from_run(cast(RunResultSource, run))
        if claim.claim_token is None:
            return IngestionResult.from_run(cast(RunResultSource, run))
        finished = repository.finish(
            run,
            expected_claim_token=claim.claim_token,
            status=CollectionStatus.FAILED,
            providers_attempted=(),
            documents_found=0,
            jobs_found=0,
            persistence=None,
            error_code="collection_unavailable",
            error_detail="collection_unavailable",
        )
        return IngestionResult.from_run(cast(RunResultSource, finished))
    finally:
        session.close()


def _recover_retry_state(
    run_id: UUID, *, exhausted: bool, expected_claim_token: str
) -> IngestionResult:
    """Use a fresh, clean session after an ingestion state-write failure."""
    session = SessionLocal()
    try:
        session.rollback()
        repository = CollectionRepository(session)
        run = (
            repository.fail_retry_exhausted(
                run_id, expected_claim_token=expected_claim_token
            )
            if exhausted
            else repository.requeue_for_retry(
                run_id, expected_claim_token=expected_claim_token
            )
        )
        return IngestionResult.unknown_run(run_id) if run is None else IngestionResult.from_run(
            cast(RunResultSource, run)
        )
    finally:
        session.close()


@celery_app.task(bind=True, name="app.tasks.collection.run_ingestion", max_retries=_MAX_INFRASTRUCTURE_RETRIES)
def run_ingestion(
    task: Task, run_id: str, claim_token: str | None = None
) -> dict[str, Any]:
    """Run an existing crawl run; repeated delivery is terminally idempotent."""
    parsed_run_id = UUID(run_id)
    if task.request.retries and claim_token is not None:
        try:
            _recover_retry_state(
                parsed_run_id,
                exhausted=False,
                expected_claim_token=claim_token,
            )
        except Exception as error:
            retry_error = RetryableInfrastructureError(claim_token=claim_token)
            if task.request.retries < _MAX_INFRASTRUCTURE_RETRIES:
                raise task.retry(
                    args=(run_id, claim_token),
                    exc=retry_error,
                    countdown=min(60, 2 ** task.request.retries),
                ) from error
            raise retry_error from error
    try:
        orchestrator, sessions = build_runtime_orchestrator()
    except RuntimeUnavailableError:
        return _result_payload(_mark_collection_unavailable(parsed_run_id))

    try:
        result = asyncio.run(orchestrator.run(parsed_run_id))
    except RetryableInfrastructureError as error:
        if task.request.retries >= _MAX_INFRASTRUCTURE_RETRIES:
            if error.claim_token is None:
                raise
            return _result_payload(
                _recover_retry_state(
                    parsed_run_id,
                    exhausted=True,
                    expected_claim_token=error.claim_token,
                )
            )
        if error.claim_token is not None:
            with suppress(OSError, SQLAlchemyError):
                _recover_retry_state(
                    parsed_run_id,
                    exhausted=False,
                    expected_claim_token=error.claim_token,
                )
        raise task.retry(
            args=(run_id, error.claim_token),
            exc=error,
            countdown=min(60, 2 ** task.request.retries),
        ) from error
    finally:
        for session in sessions:
            session.close()
    return _result_payload(result)

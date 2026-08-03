"""Synchronous Celery boundary for the asynchronous ingestion pipeline."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.core.config import settings
from app.core.database import SessionLocal
from app.ingestion.contracts import Provider
from app.ingestion.deduplication.semantic import SemanticDuplicateJudge
from app.ingestion.extraction.crew import Extractor
from app.ingestion.orchestrator import IngestionOrchestrator
from app.ingestion.result import IngestionResult, RunResultSource
from app.ingestion.runtime import build_ingestion_orchestrator as compose_orchestrator
from app.models import CollectionStatus
from app.tasks.celery_app import celery_app

_MAX_INFRASTRUCTURE_RETRIES = 3


class RuntimeUnavailableError(Exception):
    """Raised when collection has no explicitly configured runtime components."""


@dataclass(frozen=True)
class RuntimeComponents:
    providers: Sequence[Provider]
    extractor: Extractor
    semantic_judge: SemanticDuplicateJudge


def load_runtime_components() -> RuntimeComponents:
    """Load the production integration factory without importing optional providers by default."""
    factory_path = settings.collection_runtime_factory
    if not factory_path:
        raise RuntimeUnavailableError("COLLECTION_RUNTIME_FACTORY is not configured")
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


def build_runtime_orchestrator() -> tuple[IngestionOrchestrator, tuple[Session, Session, Session]]:
    """Create the caller-owned distinct sessions required by the ingestion runtime."""
    sessions = (SessionLocal(), SessionLocal(), SessionLocal())
    try:
        components = load_runtime_components()
        orchestrator = compose_orchestrator(
            run_state_session=sessions[0],
            dedup_read_session=sessions[1],
            persistence_write_session=sessions[2],
            providers=components.providers,
            extractor=components.extractor,
            semantic_judge=components.semantic_judge,
        )
    except Exception:
        for session in sessions:
            session.close()
        raise
    return orchestrator, sessions


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
        run = repository.start_or_get_terminal(run_id)
        if run is None:
            return IngestionResult.unknown_run(run_id)
        if run.status in {CollectionStatus.SUCCEEDED, CollectionStatus.PARTIAL, CollectionStatus.FAILED}:
            return IngestionResult.from_run(cast(RunResultSource, run))
        finished = repository.finish(
            run,
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


@celery_app.task(bind=True, name="app.tasks.collection.run_ingestion", max_retries=_MAX_INFRASTRUCTURE_RETRIES)
def run_ingestion(task: Task, run_id: str) -> dict[str, Any]:
    """Run an existing crawl run; repeated delivery is terminally idempotent."""
    parsed_run_id = UUID(run_id)
    try:
        orchestrator, sessions = build_runtime_orchestrator()
    except RuntimeUnavailableError:
        return _result_payload(_mark_collection_unavailable(parsed_run_id))

    try:
        result = asyncio.run(orchestrator.run(parsed_run_id))
    except (ConnectionError, OperationalError) as error:
        raise task.retry(exc=error, countdown=min(60, 2 ** task.request.retries)) from error
    finally:
        for session in sessions:
            session.close()
    return _result_payload(result)

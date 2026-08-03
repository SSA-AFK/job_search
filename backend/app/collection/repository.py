from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.persistence.result import PersistenceResult
from app.models import CollectionRequest, CollectionStatus, CrawlRun, RunType
from app.models.base import utc_now

_TERMINAL_STATUSES = {
    CollectionStatus.SUCCEEDED,
    CollectionStatus.PARTIAL,
    CollectionStatus.FAILED,
}


class CollectionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_request(self, request_id: UUID) -> CollectionRequest | None:
        return self.session.get(CollectionRequest, request_id)

    def get_active_request(self, normalized_query: str) -> CollectionRequest | None:
        statement = (
            select(CollectionRequest)
            .where(
                CollectionRequest.normalized_query == normalized_query,
                CollectionRequest.status.in_((CollectionStatus.QUEUED, CollectionStatus.RUNNING)),
            )
            .order_by(CollectionRequest.created_at)
        )
        return self.session.scalar(statement)

    def create_request(self, query: str, normalized_query: str) -> tuple[CollectionRequest, CrawlRun]:
        request = CollectionRequest(id=uuid4(), query=query, normalized_query=normalized_query)
        run = CrawlRun(
            collection_request_id=request.id,
            run_type=RunType.ON_DEMAND,
            status=CollectionStatus.QUEUED,
        )
        self.session.add_all((request, run))
        return request, run

    def get_run_for_request(self, request_id: UUID) -> CrawlRun | None:
        return self.session.scalar(
            select(CrawlRun).where(CrawlRun.collection_request_id == request_id)
        )

    def get_run(self, run_id: UUID) -> CrawlRun | None:
        return self.session.get(CrawlRun, run_id)

    def get_request_for_run(self, run: CrawlRun) -> CollectionRequest | None:
        if run.collection_request_id is None:
            return None
        return self.get_request(run.collection_request_id)

    def start_or_get_terminal(self, run_id: UUID) -> CrawlRun | None:
        run = self.get_run(run_id)
        if run is None or run.status in _TERMINAL_STATUSES:
            return run
        if run.status is not CollectionStatus.QUEUED:
            raise ValueError("invalid_run_state")
        request = self.get_request_for_run(run)
        if request is None or request.status is not CollectionStatus.QUEUED:
            raise ValueError("invalid_run_state")
        now = utc_now()
        run.status = CollectionStatus.RUNNING
        run.started_at = now
        request.status = CollectionStatus.RUNNING
        self.session.commit()
        return run

    def finish(
        self,
        run: CrawlRun,
        *,
        status: CollectionStatus,
        providers_attempted: tuple[str, ...],
        documents_found: int,
        jobs_found: int,
        persistence: PersistenceResult | None,
        error_code: str | None,
        error_detail: str | None,
    ) -> CrawlRun:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("terminal status required")
        request = self.get_request_for_run(run)
        if request is None:
            raise ValueError("invalid_run_state")
        now = utc_now()
        run.status = status
        run.providers_attempted = list(providers_attempted)
        run.documents_found = documents_found
        run.jobs_found = jobs_found
        run.jobs_written = persistence.jobs_written if persistence is not None else 0
        run.company_id = persistence.company_id if persistence is not None else None
        run.error_code = error_code
        run.error_detail = error_detail
        run.completed_at = now
        request.status = status
        request.company_id = run.company_id
        request.error_code = error_code
        request.completed_at = now
        self.session.commit()
        return run

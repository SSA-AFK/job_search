from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ingestion.errors import RunClaimError
from app.ingestion.persistence.result import PersistenceResult
from app.models import CollectionRequest, CollectionStatus, CrawlRun, RunType
from app.models.base import utc_now

_TERMINAL_STATUSES = {
    CollectionStatus.SUCCEEDED,
    CollectionStatus.PARTIAL,
    CollectionStatus.FAILED,
}


@dataclass(frozen=True)
class RunClaim:
    run: CrawlRun
    claimed: bool
    claim_token: str | None


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

    def _lock_run_request(
        self, run_id: UUID
    ) -> tuple[CrawlRun, CollectionRequest] | None:
        self.session.rollback()
        self.session.expire_all()
        run = self.session.scalar(
            select(CrawlRun)
            .where(CrawlRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None or run.collection_request_id is None:
            return None
        request = self.session.scalar(
            select(CollectionRequest)
            .where(CollectionRequest.id == run.collection_request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if request is None:
            return None
        return run, request

    def _reload_run(self, run_id: UUID, *, commit: bool = False) -> CrawlRun | None:
        if commit:
            self.session.commit()
        else:
            self.session.rollback()
        self.session.expire_all()
        return self.get_run(run_id)

    def claim_queued(self, run_id: UUID) -> RunClaim | None:
        claim_token = str(uuid4())
        try:
            return self._claim_queued(run_id, claim_token=claim_token)
        except (ConnectionError, SQLAlchemyError) as error:
            raise RunClaimError(claim_token=claim_token) from error

    def _claim_queued(self, run_id: UUID, *, claim_token: str) -> RunClaim | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        if run.status in _TERMINAL_STATUSES or run.status is CollectionStatus.RUNNING:
            return RunClaim(run, False, run.claim_token)
        request = self.get_request_for_run(run)
        if (
            run.status is not CollectionStatus.QUEUED
            or request is None
            or request.status is not CollectionStatus.QUEUED
        ):
            return RunClaim(run, False, run.claim_token)
        request_id = request.id
        now = utc_now()
        self.session.rollback()
        run_update = self.session.execute(
            update(CrawlRun)
            .where(CrawlRun.id == run_id, CrawlRun.status == CollectionStatus.QUEUED)
            .values(
                status=CollectionStatus.RUNNING,
                claim_token=claim_token,
                started_at=now,
            )
        )
        request_update = self.session.execute(
            update(CollectionRequest)
            .where(
                CollectionRequest.id == request_id,
                CollectionRequest.status == CollectionStatus.QUEUED,
            )
            .values(status=CollectionStatus.RUNNING)
        )
        if run_update.rowcount != 1 or request_update.rowcount != 1:
            self.session.rollback()
            self.session.expire_all()
            current = self.get_run(run_id)
            return (
                None
                if current is None
                else RunClaim(current, False, current.claim_token)
            )
        self.session.commit()
        self.session.expire_all()
        claimed = self.get_run(run_id)
        return None if claimed is None else RunClaim(claimed, True, claim_token)

    def owns_claim(self, run_id: UUID, *, expected_claim_token: str) -> bool:
        """Check the paired run/request ownership immediately before persistence."""
        self.session.rollback()
        self.session.expire_all()
        statement = (
            select(CrawlRun.id)
            .join(
                CollectionRequest,
                CollectionRequest.id == CrawlRun.collection_request_id,
            )
            .where(
                CrawlRun.id == run_id,
                CrawlRun.status == CollectionStatus.RUNNING,
                CrawlRun.claim_token == expected_claim_token,
                CollectionRequest.status == CollectionStatus.RUNNING,
            )
        )
        return self.session.scalar(statement) is not None

    def recover_claim_token(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> str | None:
        """Clear a failed transaction before inspecting a possibly committed claim."""
        self.session.rollback()
        self.session.expire_all()
        run = self.get_run(run_id)
        if (
            run is None
            or run.status is not CollectionStatus.RUNNING
            or run.claim_token != expected_claim_token
        ):
            return None
        return run.claim_token

    def fail_queued_dispatch(
        self, run_id: UUID, *, failed_at: datetime | None = None
    ) -> CrawlRun | None:
        """Fail only work that is still undispatched; preserve a worker's claim."""
        pair = self._lock_run_request(run_id)
        if pair is None:
            return self._reload_run(run_id)
        run, request = pair
        if (
            run.status is not CollectionStatus.QUEUED
            or request.status is not CollectionStatus.QUEUED
        ):
            return self._reload_run(run_id)
        request_id = request.id
        now = failed_at or utc_now()
        run_update = self.session.execute(
            update(CrawlRun)
            .where(CrawlRun.id == run_id, CrawlRun.status == CollectionStatus.QUEUED)
            .values(
                status=CollectionStatus.FAILED,
                error_code="collection_unavailable",
                error_detail="collection_unavailable",
                completed_at=now,
            )
        )
        request_update = self.session.execute(
            update(CollectionRequest)
            .where(
                CollectionRequest.id == request_id,
                CollectionRequest.status == CollectionStatus.QUEUED,
            )
            .values(
                status=CollectionStatus.FAILED,
                error_code="collection_unavailable",
                completed_at=now,
            )
        )
        if run_update.rowcount != 1 or request_update.rowcount != 1:
            return self._reload_run(run_id)
        return self._reload_run(run_id, commit=True)

    def _fail_invalid_locked(
        self,
        run: CrawlRun,
        request: CollectionRequest,
        *,
        expected_claim_token: str,
    ) -> CrawlRun | None:
        if (
            run.status is not CollectionStatus.RUNNING
            or run.claim_token != expected_claim_token
            or request.status is not CollectionStatus.QUEUED
        ):
            return self._reload_run(run.id)
        now = utc_now()
        run_update = self.session.execute(
            update(CrawlRun)
            .where(
                CrawlRun.id == run.id,
                CrawlRun.status == CollectionStatus.RUNNING,
                CrawlRun.claim_token == expected_claim_token,
            )
            .values(
                status=CollectionStatus.FAILED,
                error_code="invalid_run_state",
                error_detail="invalid_run_state",
                completed_at=now,
            )
        )
        request_update = self.session.execute(
            update(CollectionRequest)
            .where(
                CollectionRequest.id == request.id,
                CollectionRequest.status == CollectionStatus.QUEUED,
            )
            .values(
                status=CollectionStatus.FAILED,
                error_code="invalid_run_state",
                completed_at=now,
            )
        )
        if run_update.rowcount != 1 or request_update.rowcount != 1:
            return self._reload_run(run.id)
        return self._reload_run(run.id, commit=True)

    def requeue_for_retry(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> CrawlRun | None:
        pair = self._lock_run_request(run_id)
        if pair is None:
            return self._reload_run(run_id)
        run, request = pair
        if run.status in _TERMINAL_STATUSES:
            return self._reload_run(run_id)
        if run.status is CollectionStatus.QUEUED and request.status is CollectionStatus.QUEUED:
            return self._reload_run(run_id)
        if run.claim_token != expected_claim_token:
            return self._reload_run(run_id)
        if (
            run.status is not CollectionStatus.RUNNING
            or request.status is not CollectionStatus.RUNNING
        ):
            return self._fail_invalid_locked(
                run,
                request,
                expected_claim_token=expected_claim_token,
            )
        request_id = request.id
        run_update = self.session.execute(
            update(CrawlRun)
            .where(
                CrawlRun.id == run_id,
                CrawlRun.status == CollectionStatus.RUNNING,
                CrawlRun.claim_token == expected_claim_token,
            )
            .values(
                status=CollectionStatus.QUEUED,
                claim_token=None,
                started_at=None,
                celery_task_id=None,
            )
        )
        request_update = self.session.execute(
            update(CollectionRequest)
            .where(
                CollectionRequest.id == request_id,
                CollectionRequest.status == CollectionStatus.RUNNING,
            )
            .values(status=CollectionStatus.QUEUED)
        )
        if run_update.rowcount != 1 or request_update.rowcount != 1:
            return self._reload_run(run_id)
        return self._reload_run(run_id, commit=True)

    def fail_retry_exhausted(
        self, run_id: UUID, *, expected_claim_token: str
    ) -> CrawlRun | None:
        pair = self._lock_run_request(run_id)
        if pair is None:
            return self._reload_run(run_id)
        run, request = pair
        if run.status in _TERMINAL_STATUSES:
            return self._reload_run(run_id)
        if run.claim_token != expected_claim_token:
            return self._reload_run(run_id)
        if (
            run.status is not CollectionStatus.RUNNING
            or request.status is not CollectionStatus.RUNNING
        ):
            return self._fail_invalid_locked(
                run,
                request,
                expected_claim_token=expected_claim_token,
            )
        request_id = request.id
        now = utc_now()
        run_update = self.session.execute(
            update(CrawlRun)
            .where(
                CrawlRun.id == run_id,
                CrawlRun.status == CollectionStatus.RUNNING,
                CrawlRun.claim_token == expected_claim_token,
            )
            .values(
                status=CollectionStatus.FAILED,
                error_code="collection_unavailable",
                error_detail="collection_unavailable",
                completed_at=now,
            )
        )
        request_update = self.session.execute(
            update(CollectionRequest)
            .where(
                CollectionRequest.id == request_id,
                CollectionRequest.status == CollectionStatus.RUNNING,
            )
            .values(
                status=CollectionStatus.FAILED,
                error_code="collection_unavailable",
                completed_at=now,
            )
        )
        if run_update.rowcount != 1 or request_update.rowcount != 1:
            return self._reload_run(run_id)
        return self._reload_run(run_id, commit=True)

    def finish(
        self,
        run: CrawlRun,
        *,
        expected_claim_token: str,
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
        request_id = run.collection_request_id
        if request_id is None:
            raise ValueError("invalid_run_state")
        run_id = run.id
        now = utc_now()
        company_id = persistence.company_id if persistence is not None else None
        self.session.rollback()
        run_update = self.session.execute(
            update(CrawlRun)
            .where(
                CrawlRun.id == run_id,
                CrawlRun.status == CollectionStatus.RUNNING,
                CrawlRun.claim_token == expected_claim_token,
            )
            .values(
                status=status,
                providers_attempted=list(providers_attempted),
                documents_found=documents_found,
                jobs_found=jobs_found,
                jobs_written=persistence.jobs_written if persistence is not None else 0,
                company_id=company_id,
                error_code=error_code,
                error_detail=error_detail,
                completed_at=now,
            )
        )
        request_update = self.session.execute(
            update(CollectionRequest)
            .where(
                CollectionRequest.id == request_id,
                CollectionRequest.status == CollectionStatus.RUNNING,
            )
            .values(
                status=status,
                company_id=company_id,
                error_code=error_code,
                completed_at=now,
            )
        )
        if run_update.rowcount != 1 or request_update.rowcount != 1:
            self.session.rollback()
            self.session.expire_all()
            current = self.get_run(run_id)
            if current is None:
                raise ValueError("invalid_run_state")
            return current
        self.session.commit()
        self.session.expire_all()
        finished = self.get_run(run_id)
        if finished is None:
            raise ValueError("invalid_run_state")
        return finished

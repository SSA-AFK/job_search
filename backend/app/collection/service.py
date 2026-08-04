from collections.abc import Callable
from uuid import UUID

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collection.repository import CollectionRepository
from app.collection.schemas import CollectionRequestRead
from app.core.errors import DomainError
from app.core.normalization import normalize_name
from app.models import CollectionRequest, CollectionStatus
from app.models.base import utc_now

ACTIVE_REQUEST_INDEX = "uq_collection_requests_active_query"


class CollectionService:
    def __init__(self, session: Session, dispatch_collection: Callable[[UUID], str]) -> None:
        self.session = session
        self.repository = CollectionRepository(session)
        self.dispatch_collection = dispatch_collection

    def submit(self, query: str) -> CollectionRequestRead:
        normalized_query = normalize_name(query)
        existing = self.repository.get_active_request(normalized_query)
        if existing is not None:
            return self._read(existing)

        request, run = self.repository.create_request(query.strip(), normalized_query)
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            if not self._is_active_request_conflict(error):
                raise
            existing = self.repository.get_active_request(normalized_query)
            if existing is None:
                raise
            return self._read(existing)

        try:
            task_id = self.dispatch_collection(run.id)
        except Exception:  # The task never started, so queued may become failed here.
            persisted_request = self.repository.get_request(request.id)
            failed_run = (
                self.repository.get_run_for_request(persisted_request.id)
                if persisted_request
                else None
            )
            if persisted_request is None or failed_run is None:
                raise
            persisted_request.status = CollectionStatus.FAILED
            persisted_request.error_code = "collection_unavailable"
            persisted_request.completed_at = utc_now()
            failed_run.status = CollectionStatus.FAILED
            failed_run.error_code = "collection_unavailable"
            failed_run.completed_at = utc_now()
            self.session.commit()
            return self._read(persisted_request)

        persisted_run = self.session.get(type(run), run.id)
        if persisted_run is None:
            raise RuntimeError("Collection run disappeared before task id could be stored")
        persisted_run.celery_task_id = task_id
        self.session.commit()
        persisted_request = self.repository.get_request(request.id)
        if persisted_request is None:
            raise RuntimeError("Collection request disappeared after dispatch")
        return self._read(persisted_request)

    def get(self, request_id: UUID) -> CollectionRequestRead:
        request = self.repository.get_request(request_id)
        if request is None:
            raise DomainError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="collection_request_not_found",
                message="Collection request not found",
            )
        return self._read(request)

    @staticmethod
    def _is_active_request_conflict(error: IntegrityError) -> bool:
        diagnostic = getattr(error.orig, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name == ACTIVE_REQUEST_INDEX:
            return True
        return "collection_requests.normalized_query" in str(error.orig).lower()

    @staticmethod
    def _read(request: CollectionRequest) -> CollectionRequestRead:
        return CollectionRequestRead.model_validate(request)

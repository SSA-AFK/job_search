from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.collection.schemas import CollectionRequestCreate, CollectionRequestRead
from app.collection.service import CollectionService
from app.core.config import settings
from app.core.database import get_session
from app.core.errors import DomainError

router = APIRouter(prefix="/collection-requests", tags=["collection-requests"])


def dispatch_collection(run_id: UUID) -> str:
    from app.tasks.collection import run_ingestion

    return str(run_ingestion.delay(str(run_id)).id)


def get_collection_service(
    session: Annotated[Session, Depends(get_session)],
) -> CollectionService:
    return CollectionService(session, dispatch_collection)


def require_collection_enabled() -> None:
    if not settings.collection_enabled:
        raise DomainError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="collection_unavailable",
            message="Collection service is unavailable.",
        )


@router.post(
    "",
    response_model=CollectionRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_collection_enabled)],
)
def create_collection_request(
    payload: CollectionRequestCreate,
    service: Annotated[CollectionService, Depends(get_collection_service)],
) -> CollectionRequestRead:
    return service.submit(payload.query)


@router.get("/{request_id}", response_model=CollectionRequestRead)
def get_collection_request(
    request_id: UUID,
    service: Annotated[CollectionService, Depends(get_collection_service)],
) -> CollectionRequestRead:
    return service.get(request_id)

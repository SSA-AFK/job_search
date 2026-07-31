from uuid import UUID

from fastapi import APIRouter, status

from app.collection.schemas import CollectionRequestCreate
from app.core.config import settings
from app.core.errors import DomainError

router = APIRouter(prefix="/collection-requests", tags=["collection-requests"])


def _raise_collection_unavailable() -> None:
    raise DomainError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="collection_unavailable",
        message="Collection service is unavailable.",
    )


@router.post("")
def create_collection_request(_payload: CollectionRequestCreate) -> None:
    if not settings.collection_enabled:
        _raise_collection_unavailable()
    _raise_collection_unavailable()


@router.get("/{request_id}")
def get_collection_request(request_id: UUID) -> None:
    if not settings.collection_enabled:
        _raise_collection_unavailable()
    _raise_collection_unavailable()

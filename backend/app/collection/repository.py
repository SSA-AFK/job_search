from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CollectionRequest, CollectionStatus, CrawlRun, RunType


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

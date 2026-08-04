from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UTCDateTime, utc_now
from app.models.enums import CollectionStatus, RunType


def enum_column(
    enum_type: type[CollectionStatus] | type[RunType], name: str, length: int
) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda enum: [member.value for member in enum],
        native_enum=False,
        create_constraint=True,
        name=name,
        length=length,
    )


class CollectionRequest(Base, TimestampMixin):
    __tablename__ = "collection_requests"
    __table_args__ = (
        Index("ix_collection_requests_status_query", "status", "normalized_query"),
        Index(
            "uq_collection_requests_active_query",
            "normalized_query",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    query: Mapped[str] = mapped_column(String(255))
    normalized_query: Mapped[str] = mapped_column(String(255))
    status: Mapped[CollectionStatus] = mapped_column(
        enum_column(CollectionStatus, "collection_status", 20), default=CollectionStatus.QUEUED
    )
    company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(50))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    collection_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("collection_requests.id", ondelete="SET NULL")
    )
    company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    run_type: Mapped[RunType] = mapped_column(enum_column(RunType, "run_type", 30))
    status: Mapped[CollectionStatus] = mapped_column(
        enum_column(CollectionStatus, "crawl_run_status", 20), default=CollectionStatus.QUEUED
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    providers_attempted: Mapped[list[str]] = mapped_column(JSON, default=list)
    documents_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    jobs_written: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_detail: Mapped[str | None] = mapped_column(Text)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

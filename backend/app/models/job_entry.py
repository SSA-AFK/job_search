from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UTCDateTime, utc_now
from app.models.enums import JobEntryStatus, JobSnapshotStatus


def _enum_column(
    enum_type: type[JobEntryStatus] | type[JobSnapshotStatus], name: str
) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda enum: [member.value for member in enum],
        native_enum=False,
        create_constraint=True,
        name=name,
        length=20,
    )


class JobEntry(Base, TimestampMixin):
    __tablename__ = "job_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "normalized_url", name="uq_job_entry_company_url"),
        Index("ix_job_entries_status_checked", "status", "last_checked_at"),
        Index("ix_job_entries_platform_status", "platform", "status"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2000))
    normalized_url: Mapped[str] = mapped_column(String(2000))
    provider: Mapped[str] = mapped_column(String(50))
    platform: Mapped[str] = mapped_column(String(50))
    requires_rendering: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    status: Mapped[JobEntryStatus] = mapped_column(
        _enum_column(JobEntryStatus, "job_entry_status"),
        default=JobEntryStatus.UNKNOWN,
        server_default=JobEntryStatus.UNKNOWN.value,
        nullable=False,
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class JobCollectionSnapshot(Base):
    __tablename__ = "job_collection_snapshots"
    __table_args__ = (
        UniqueConstraint("job_entry_id", "crawl_run_id", name="uq_job_snapshot_entry_run"),
        Index("ix_job_snapshots_entry_completed", "job_entry_id", "completed_at"),
        Index("ix_job_snapshots_status_completed", "status", "completed_at"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    job_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_entries.id", ondelete="CASCADE"), nullable=False
    )
    crawl_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="SET NULL")
    )
    status: Mapped[JobSnapshotStatus] = mapped_column(
        _enum_column(JobSnapshotStatus, "job_snapshot_status"), nullable=False
    )
    lifecycle_applied: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    pagination_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    empty_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    reported_total: Mapped[int | None] = mapped_column(Integer)
    observed_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    pages_fetched: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0", nullable=False
    )
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    command_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UTCDateTime, utc_now
from app.models.enums import JobType, VerificationStatus


class JobPosting(Base, TimestampMixin):
    __tablename__ = "job_postings"
    __table_args__ = (Index("ix_job_postings_company_active", "company_id", "is_active"),)

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255))
    normalized_title: Mapped[str] = mapped_column(String(255))
    job_type: Mapped[JobType] = mapped_column(
        Enum(
            JobType,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
            name="job_type",
            length=50,
        ),
        default=JobType.UNKNOWN,
    )
    city: Mapped[str] = mapped_column(String(50))
    salary_min_monthly: Mapped[int | None] = mapped_column(Integer)
    salary_max_monthly: Mapped[int | None] = mapped_column(Integer)
    salary_months: Mapped[int | None] = mapped_column(SmallInteger)
    description: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class JobSource(Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        UniqueConstraint("provider", "source_raw_id", name="uq_job_source_provider_raw_id"),
        Index("ix_job_sources_entry_active", "job_entry_id", "is_active"),
        Index("ix_job_sources_posting_active", "job_posting_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    job_posting_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )
    job_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("job_entries.id", ondelete="SET NULL")
    )
    last_seen_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("job_collection_snapshots.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(50))
    source_raw_id: Mapped[str] = mapped_column(String(255))
    apply_url: Mapped[str] = mapped_column(String(2000))
    first_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(
            VerificationStatus,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
            name="verification_status",
            length=50,
        ),
        default=VerificationStatus.PENDING_VERIFICATION,
        nullable=False,
    )
    missing_complete_snapshots: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    lifecycle_managed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

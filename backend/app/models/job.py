from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, utc_now
from app.models.enums import JobType


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
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    job_posting_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(50))
    source_raw_id: Mapped[str] = mapped_column(String(255))
    apply_url: Mapped[str] = mapped_column(String(2000))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

"""Funding events, investors, and independently sourced verification evidence."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, UTCDateTime
from app.models.enums import VerificationStatus


class FundingEvent(Base):
    __tablename__ = "funding_events"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "round_label", "announced_at", name="uq_funding_event_company_round_date"
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    round_label: Mapped[str] = mapped_column(String(50), nullable=False)
    announced_at: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(12))
    verification_status: Mapped[VerificationStatus] = mapped_column(
        String(50), default=VerificationStatus.PENDING_VERIFICATION, nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class FundingInvestor(Base):
    __tablename__ = "funding_investors"
    __table_args__ = (
        UniqueConstraint("funding_event_id", "normalized_name", name="uq_funding_investor_event_name"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    funding_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("funding_events.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)


class FundingEventSource(Base):
    __tablename__ = "funding_event_sources"

    funding_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("funding_events.id", ondelete="CASCADE"), primary_key=True
    )
    source_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), primary_key=True
    )

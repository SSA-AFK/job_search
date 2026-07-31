from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Date, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin
from app.models.enums import FilingType


class RegulatoryFiling(Base, TimestampMixin):
    __tablename__ = "regulatory_filings"
    __table_args__ = (
        UniqueConstraint("filing_type", "filing_number", name="uq_filing_type_number"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )
    filing_type: Mapped[FilingType] = mapped_column(
        Enum(
            FilingType,
            values_callable=lambda enum: [member.value for member in enum],
            native_enum=False,
            create_constraint=True,
            name="filing_type",
            length=50,
        )
    )
    filing_number: Mapped[str] = mapped_column(String(255))
    filing_name: Mapped[str] = mapped_column(String(255))
    filing_authority: Mapped[str | None] = mapped_column(String(255))
    filing_date: Mapped[date | None] = mapped_column(Date)
    filing_status: Mapped[str | None] = mapped_column(String(50))
    detail_url: Mapped[str | None] = mapped_column(String(2000))

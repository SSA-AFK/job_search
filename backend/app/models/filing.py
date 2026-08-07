from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Date, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.normalization import normalize_name
from app.models.base import GUID, Base, TimestampMixin
from app.models.enums import FilingType


class RegulatoryFiling(Base, TimestampMixin):
    __tablename__ = "regulatory_filings"
    __table_args__ = (
        UniqueConstraint("filing_type", "filing_number", name="uq_filing_type_number"),
        Index(
            "ix_regulatory_filings_normalized_filing_number",
            "normalized_filing_number",
        ),
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
    normalized_filing_number: Mapped[str] = mapped_column(String(255), nullable=False)
    filing_name: Mapped[str] = mapped_column(String(255))
    filing_authority: Mapped[str | None] = mapped_column(String(255))
    filing_date: Mapped[date | None] = mapped_column(Date)
    filing_status: Mapped[str | None] = mapped_column(String(50))
    detail_url: Mapped[str | None] = mapped_column(String(2000))

    @validates("filing_number")
    def normalize_identity_filing_number(self, _key: str, value: str) -> str:
        normalized = normalize_name(value)
        if not normalized:
            raise ValueError("filing_number is required")
        if len(normalized) > 255:
            raise ValueError("filing_number exceeds database length")
        self.normalized_filing_number = normalized
        return normalized

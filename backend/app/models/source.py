from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_id", name="uq_source_document_provider_external_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str | None] = mapped_column(String(500))
    text_excerpt: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    authority_level: Mapped[int | None] = mapped_column(SmallInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CompanySource(Base):
    __tablename__ = "company_sources"

    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    source_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), primary_key=True
    )
    covered_fields: Mapped[list[str]] = mapped_column(JSON)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))

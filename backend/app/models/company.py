from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.core.normalization import normalize_public_identity_url
from app.models.base import GUID, Base, TimestampMixin, UTCDateTime


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_company_normalized_name"),
        Index("ix_companies_normalized_name", "normalized_name"),
        Index("ix_companies_normalized_website", "normalized_website"),
        Index("ix_companies_industry", "industry"),
        Index("ix_companies_sub_industry", "sub_industry"),
        Index("ix_companies_funding_stage", "funding_stage"),
        Index("ix_companies_scale", "scale"),
        Index("ix_companies_city", "city"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    canonical_name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str | None] = mapped_column(String(100))
    sub_industry: Mapped[str | None] = mapped_column(String(100))
    funding_stage: Mapped[str] = mapped_column(String(50), default="unknown")
    scale: Mapped[str] = mapped_column(String(50), default="unknown")
    city: Mapped[str | None] = mapped_column(String(50))
    headquarters: Mapped[str | None] = mapped_column(String(300))
    founded_year: Mapped[int | None] = mapped_column()
    logo_url: Mapped[str | None] = mapped_column(String(1000))
    website: Mapped[str | None] = mapped_column(String(1000))
    normalized_website: Mapped[str] = mapped_column(
        String(1000), nullable=False, default=""
    )
    description: Mapped[str | None] = mapped_column(Text)
    last_collected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    @validates("website")
    def normalize_identity_website(self, _key: str, value: str | None) -> str | None:
        if value is None:
            self.normalized_website = ""
            return None
        normalized = normalize_public_identity_url(value)
        if len(normalized) > 1_000:
            raise ValueError("website exceeds database length")
        self.normalized_website = normalized
        return normalized


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (
        UniqueConstraint("normalized_alias", name="uq_company_alias_normalized_alias"),
        Index("ix_company_aliases_normalized_alias", "normalized_alias"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(255))
    normalized_alias: Mapped[str] = mapped_column(String(255))

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, UTCDateTime


class RankingPilot(Base):
    __tablename__ = "ranking_pilots"
    __table_args__ = (
        UniqueConstraint("industry", "input_sha256", "selection_seed", name="uq_ranking_pilot_input"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    industry: Mapped[str] = mapped_column(String(50), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_seed: Mapped[str] = mapped_column(String(128), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RankingPilotMember(Base):
    __tablename__ = "ranking_pilot_members"
    __table_args__ = (
        UniqueConstraint("pilot_id", "company_id", name="uq_ranking_pilot_member_company"),
        UniqueConstraint("pilot_id", "source_row", name="uq_ranking_pilot_member_row"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    pilot_id: Mapped[UUID] = mapped_column(
        ForeignKey("ranking_pilots.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    source_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stratum: Mapped[str] = mapped_column(String(500), nullable=False)
    selection_reason: Mapped[str] = mapped_column(String(100), nullable=False)


class CompanyRankingSnapshot(Base):
    __tablename__ = "company_ranking_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "pilot_id", "company_id", "rule_version", name="uq_company_ranking_snapshot_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    pilot_id: Mapped[UUID] = mapped_column(
        ForeignKey("ranking_pilots.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    industry: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
    total_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    component_scores: Mapped[dict[str, int]] = mapped_column(nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(nullable=False)
    is_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CompanyRankingSnapshotEvidence(Base):
    __tablename__ = "company_ranking_snapshot_evidence"

    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("company_ranking_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    source_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), primary_key=True
    )

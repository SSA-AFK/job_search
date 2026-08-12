from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, UTCDateTime


class RankingPilot(Base):
    __tablename__ = "ranking_pilots"
    __table_args__ = (
        UniqueConstraint(
            "industry", "input_sha256", "selection_seed", name="uq_ranking_pilot_input"
        ),
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
    company_size: Mapped[str | None] = mapped_column(String(50))
    established_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    insured_employee_count: Mapped[int | None] = mapped_column(Integer)
    employee_report_year: Mapped[int | None] = mapped_column(Integer)


class RankingCollectionRun(Base):
    __tablename__ = "ranking_collection_runs"
    __table_args__ = (
        UniqueConstraint(
            "pilot_id",
            "company_id",
            "category",
            "run_key",
            name="uq_ranking_collection_run_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    pilot_id: Mapped[UUID] = mapped_column(
        ForeignKey("ranking_pilots.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    run_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    logical_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CompanyRankingSignal(Base):
    __tablename__ = "company_ranking_signals"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "category",
            "signal_key",
            "source_fingerprint",
            name="uq_company_ranking_signal_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    signal_key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    event_date: Mapped[datetime | None] = mapped_column(UTCDateTime())
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


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
    component_scores: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    raw_component_scores: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    stage_percentiles: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_coverage: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False, default=dict)
    company_stage: Mapped[str | None] = mapped_column(String(20))
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    eligibility_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
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

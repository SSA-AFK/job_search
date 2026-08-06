"""Persistence models for immutable Gate 1 manifest evidence and observations."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.manifest.contracts import (
    AiCategory,
    CandidateDecisionStatus,
    ConfidenceTier,
    DiscoveryStatus,
    ReviewAction,
)
from app.models.base import GUID, Base, TimestampMixin, UTCDateTime


def _enum_column(enum_type: type[StrEnum], name: str, *, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda enum: [member.value for member in enum],
        native_enum=False,
        create_constraint=True,
        name=name,
        length=length,
    )


class CandidateFact(Base, TimestampMixin):
    __tablename__ = "candidate_facts"
    __table_args__ = (
        UniqueConstraint("stable_evidence_id", name="uq_candidate_fact_evidence"),
        Index("ix_candidate_facts_decision_category", "decision_status", "primary_category"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    stable_evidence_id: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    primary_category: Mapped[AiCategory] = mapped_column(
        _enum_column(AiCategory, "ai_category", length=50), nullable=False
    )
    official_website: Mapped[str | None] = mapped_column(String(2000))
    recruitment_url: Mapped[str | None] = mapped_column(String(2000))
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_tier: Mapped[ConfidenceTier] = mapped_column(
        _enum_column(ConfidenceTier, "candidate_confidence_tier", length=20), nullable=False
    )
    confidence_reason: Mapped[str] = mapped_column(Text, nullable=False)
    decision_status: Mapped[CandidateDecisionStatus] = mapped_column(
        _enum_column(CandidateDecisionStatus, "candidate_decision_status", length=20),
        default=CandidateDecisionStatus.REVIEW_REQUIRED,
        server_default=CandidateDecisionStatus.REVIEW_REQUIRED.value,
        nullable=False,
    )
    company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )


class CandidateReview(Base):
    __tablename__ = "candidate_reviews"
    __table_args__ = (Index("ix_candidate_reviews_candidate_decided", "candidate_fact_id", "decided_at"),)

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    candidate_fact_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_facts.id", ondelete="CASCADE"), nullable=False
    )
    prior_status: Mapped[CandidateDecisionStatus] = mapped_column(
        _enum_column(CandidateDecisionStatus, "candidate_review_prior_status", length=20),
        nullable=False,
    )
    action: Mapped[ReviewAction] = mapped_column(
        _enum_column(ReviewAction, "candidate_review_action", length=20), nullable=False
    )
    resulting_status: Mapped[CandidateDecisionStatus] = mapped_column(
        _enum_column(CandidateDecisionStatus, "candidate_review_resulting_status", length=20),
        nullable=False,
    )
    resulting_company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CompanyManifest(Base):
    __tablename__ = "company_manifests"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_quota: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CompanyManifestMember(Base):
    __tablename__ = "company_manifest_members"
    __table_args__ = (
        UniqueConstraint("manifest_version", "position", name="uq_manifest_member_position"),
        UniqueConstraint("manifest_version", "company_id", name="uq_manifest_member_company"),
        Index("ix_manifest_members_company", "company_id"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    manifest_version: Mapped[str] = mapped_column(
        ForeignKey("company_manifests.version", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    primary_category: Mapped[AiCategory] = mapped_column(
        _enum_column(AiCategory, "manifest_member_category", length=50), nullable=False
    )
    official_website: Mapped[str | None] = mapped_column(String(2000))
    recruitment_url: Mapped[str | None] = mapped_column(String(2000))


class EntryDiscoveryObservation(Base):
    __tablename__ = "entry_discovery_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ("job_entry_id", "company_id"),
            ("job_entries.id", "job_entries.company_id"),
            name="fk_discovery_observation_entry_company",
            ondelete="RESTRICT",
        ),
        Index("ix_discovery_observations_manifest_status", "manifest_version", "status"),
        Index("ix_discovery_observations_company_observed", "company_id", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    manifest_version: Mapped[str] = mapped_column(
        ForeignKey("company_manifests.version", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[DiscoveryStatus] = mapped_column(
        _enum_column(DiscoveryStatus, "entry_discovery_status", length=20), nullable=False
    )
    candidate_url: Mapped[str | None] = mapped_column(String(2000))
    normalized_url: Mapped[str | None] = mapped_column(String(2000))
    source_id: Mapped[str | None] = mapped_column(String(50))
    ownership_evidence: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str | None] = mapped_column(String(50))
    requires_rendering: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    job_entry_id: Mapped[UUID | None] = mapped_column(GUID())
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

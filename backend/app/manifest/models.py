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
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))


class CandidateReview(Base):
    __tablename__ = "candidate_reviews"
    __table_args__ = (
        Index("ix_candidate_reviews_candidate_decided", "candidate_fact_id", "decided_at"),
    )

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


class EntryDiscoveryRound(Base):
    __tablename__ = "entry_discovery_rounds"
    __table_args__ = (
        ForeignKeyConstraint(
            ("predecessor_round_id", "manifest_version"),
            ("entry_discovery_rounds.id", "entry_discovery_rounds.manifest_version"),
            name="fk_discovery_round_predecessor_manifest",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "manifest_version", name="uq_discovery_round_id_manifest"),
        UniqueConstraint("manifest_version", "name", name="uq_discovery_round_manifest_name"),
        Index("ix_discovery_rounds_manifest_started", "manifest_version", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    manifest_version: Mapped[str] = mapped_column(
        ForeignKey("company_manifests.version", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    model_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    membership_fingerprint: Mapped[str | None] = mapped_column(String(64))
    intended_member_count: Mapped[int | None] = mapped_column(Integer)
    predecessor_round_id: Mapped[UUID | None] = mapped_column(GUID())
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EntryDiscoveryObservation(Base):
    __tablename__ = "entry_discovery_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ("job_entry_id", "company_id"),
            ("job_entries.id", "job_entries.company_id"),
            name="fk_discovery_observation_entry_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("discovery_round_id", "manifest_version"),
            ("entry_discovery_rounds.id", "entry_discovery_rounds.manifest_version"),
            name="fk_discovery_observation_round_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("predecessor_observation_id", "manifest_version", "company_id"),
            (
                "entry_discovery_observations.id",
                "entry_discovery_observations.manifest_version",
                "entry_discovery_observations.company_id",
            ),
            name="fk_discovery_observation_predecessor_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id", "manifest_version", "company_id", name="uq_discovery_observation_identity"
        ),
        UniqueConstraint("id", "discovery_round_id", name="uq_discovery_observation_id_round"),
        UniqueConstraint(
            "discovery_round_id", "company_id", name="uq_discovery_observation_round_company"
        ),
        Index("ix_discovery_observations_manifest_status", "manifest_version", "status"),
        Index("ix_discovery_observations_company_observed", "company_id", "observed_at"),
        Index("ix_discovery_observations_round_status", "discovery_round_id", "status"),
        Index("ix_discovery_observations_predecessor", "predecessor_observation_id"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    manifest_version: Mapped[str] = mapped_column(
        ForeignKey("company_manifests.version", ondelete="RESTRICT"), nullable=False
    )
    discovery_round_id: Mapped[UUID | None] = mapped_column(GUID())
    predecessor_observation_id: Mapped[UUID | None] = mapped_column(GUID())
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
    public_evidence: Mapped[dict[str, object] | None] = mapped_column(JSON)
    model_assessment: Mapped[dict[str, object] | None] = mapped_column(JSON)
    independent_validation: Mapped[dict[str, object] | None] = mapped_column(JSON)
    prompt_fingerprint: Mapped[str | None] = mapped_column(String(64))
    schema_fingerprint: Mapped[str | None] = mapped_column(String(64))
    policy_fingerprint: Mapped[str | None] = mapped_column(String(64))
    registry_fingerprint: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EntryEvidenceAuditSample(Base):
    __tablename__ = "entry_evidence_audit_samples"
    __table_args__ = (
        ForeignKeyConstraint(
            ("observation_id", "discovery_round_id"),
            (
                "entry_discovery_observations.id",
                "entry_discovery_observations.discovery_round_id",
            ),
            name="fk_evidence_audit_sample_observation_round",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "discovery_round_id", "observation_id", name="uq_evidence_audit_sample_observation"
        ),
        Index(
            "ix_evidence_audit_samples_stratum",
            "discovery_round_id",
            "source_id",
            "platform",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    discovery_round_id: Mapped[UUID] = mapped_column(
        ForeignKey("entry_discovery_rounds.id", ondelete="RESTRICT"), nullable=False
    )
    observation_id: Mapped[UUID] = mapped_column(GUID(), nullable=False)
    source_id: Mapped[str] = mapped_column(String(50), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    selected_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EntryEvidenceAuditFinding(Base):
    __tablename__ = "entry_evidence_audit_findings"
    __table_args__ = (
        UniqueConstraint("audit_sample_id", name="uq_evidence_audit_finding_sample"),
        Index("ix_evidence_audit_findings_severe_audited", "severe_error", "audited_at"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    audit_sample_id: Mapped[UUID] = mapped_column(
        ForeignKey("entry_evidence_audit_samples.id", ondelete="RESTRICT"), nullable=False
    )
    severe_error: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    audited_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class EntryEvidenceQuarantine(Base):
    __tablename__ = "entry_evidence_quarantines"
    __table_args__ = (
        UniqueConstraint("observation_id", name="uq_evidence_quarantine_observation"),
        UniqueConstraint("audit_finding_id", name="uq_evidence_quarantine_finding"),
        Index("ix_evidence_quarantines_time", "quarantined_at"),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    observation_id: Mapped[UUID] = mapped_column(
        ForeignKey("entry_discovery_observations.id", ondelete="RESTRICT"), nullable=False
    )
    audit_finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("entry_evidence_audit_findings.id", ondelete="RESTRICT"), nullable=False
    )
    quarantined_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

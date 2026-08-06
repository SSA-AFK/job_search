"""Add Gate 1 manifest evidence and entry discovery storage.

Revision ID: 0008_gate1_manifest_discovery
Revises: 0007_job_source_snapshot_lifecycle
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0008_gate1_manifest_discovery"
down_revision: str | None = "0007_job_source_snapshot_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_ENTRY_COMPANY_UNIQUE = "uq_job_entries_id_company"

ai_category = sa.Enum(
    "foundation_models",
    "ai_cloud_model_platforms",
    "ai_chips_compute",
    "autonomous_driving_transport",
    "robotics_embodied_ai",
    "computer_vision_imaging",
    "speech_language_technology",
    "enterprise_vertical_ai",
    "data_infrastructure_mlops",
    name="ai_category",
    native_enum=False,
    create_constraint=True,
    length=50,
)
candidate_confidence_tier = sa.Enum(
    "high",
    "medium",
    "low",
    name="candidate_confidence_tier",
    native_enum=False,
    create_constraint=True,
    length=20,
)
candidate_decision_status = sa.Enum(
    "review_required",
    "accepted",
    "rejected",
    name="candidate_decision_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
candidate_review_prior_status = sa.Enum(
    "review_required",
    "accepted",
    "rejected",
    name="candidate_review_prior_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
candidate_review_action = sa.Enum(
    "accept",
    "reject",
    name="candidate_review_action",
    native_enum=False,
    create_constraint=True,
    length=20,
)
candidate_review_resulting_status = sa.Enum(
    "review_required",
    "accepted",
    "rejected",
    name="candidate_review_resulting_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
manifest_member_category = sa.Enum(
    "foundation_models",
    "ai_cloud_model_platforms",
    "ai_chips_compute",
    "autonomous_driving_transport",
    "robotics_embodied_ai",
    "computer_vision_imaging",
    "speech_language_technology",
    "enterprise_vertical_ai",
    "data_infrastructure_mlops",
    name="manifest_member_category",
    native_enum=False,
    create_constraint=True,
    length=50,
)
entry_discovery_status = sa.Enum(
    "accepted",
    "review_required",
    "not_found",
    "blocked",
    "failed",
    name="entry_discovery_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _create_job_entry_company_unique() -> None:
    if _is_sqlite():
        op.create_index(
            JOB_ENTRY_COMPANY_UNIQUE,
            "job_entries",
            ["id", "company_id"],
            unique=True,
        )
        return
    op.create_unique_constraint(
        JOB_ENTRY_COMPANY_UNIQUE,
        "job_entries",
        ["id", "company_id"],
    )


def _drop_job_entry_company_unique() -> None:
    if _is_sqlite():
        op.drop_index(JOB_ENTRY_COMPANY_UNIQUE, table_name="job_entries")
        return
    op.drop_constraint(JOB_ENTRY_COMPANY_UNIQUE, "job_entries", type_="unique")


def upgrade() -> None:
    _create_job_entry_company_unique()

    op.create_table(
        "candidate_facts",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("stable_evidence_id", sa.String(64), nullable=False),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("primary_category", ai_category, nullable=False),
        sa.Column("official_website", sa.String(2000), nullable=True),
        sa.Column("recruitment_url", sa.String(2000), nullable=True),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("retrieved_at", UTCDateTime(), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("confidence_tier", candidate_confidence_tier, nullable=False),
        sa.Column("confidence_reason", sa.Text(), nullable=False),
        sa.Column(
            "decision_status",
            candidate_decision_status,
            server_default="review_required",
            nullable=False,
        ),
        sa.Column("company_id", GUID(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_candidate_facts_company_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stable_evidence_id",
            name="uq_candidate_fact_evidence",
        ),
    )
    op.create_index(
        "ix_candidate_facts_decision_category",
        "candidate_facts",
        ["decision_status", "primary_category"],
    )

    op.create_table(
        "candidate_reviews",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("candidate_fact_id", GUID(), nullable=False),
        sa.Column("prior_status", candidate_review_prior_status, nullable=False),
        sa.Column("action", candidate_review_action, nullable=False),
        sa.Column(
            "resulting_status",
            candidate_review_resulting_status,
            nullable=False,
        ),
        sa.Column("resulting_company_id", GUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_fact_id"],
            ["candidate_facts.id"],
            name="fk_candidate_reviews_candidate_fact_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_company_id"],
            ["companies.id"],
            name="fk_candidate_reviews_resulting_company_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_reviews_candidate_decided",
        "candidate_reviews",
        ["candidate_fact_id", "decided_at"],
    )

    op.create_table(
        "company_manifests",
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("canonical_quota", sa.JSON(), nullable=False),
        sa.Column("frozen_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("version"),
    )

    op.create_table(
        "company_manifest_members",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("manifest_version", sa.String(64), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("primary_category", manifest_member_category, nullable=False),
        sa.Column("official_website", sa.String(2000), nullable=True),
        sa.Column("recruitment_url", sa.String(2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["manifest_version"],
            ["company_manifests.version"],
            name="fk_manifest_members_manifest_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_manifest_members_company_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_version",
            "position",
            name="uq_manifest_member_position",
        ),
        sa.UniqueConstraint(
            "manifest_version",
            "company_id",
            name="uq_manifest_member_company",
        ),
    )
    op.create_index(
        "ix_manifest_members_company",
        "company_manifest_members",
        ["company_id"],
    )

    op.create_table(
        "entry_discovery_observations",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("manifest_version", sa.String(64), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("method", sa.String(100), nullable=False),
        sa.Column("status", entry_discovery_status, nullable=False),
        sa.Column("candidate_url", sa.String(2000), nullable=True),
        sa.Column("normalized_url", sa.String(2000), nullable=True),
        sa.Column("source_id", sa.String(50), nullable=True),
        sa.Column("ownership_evidence", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(50), nullable=True),
        sa.Column(
            "requires_rendering",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("job_entry_id", GUID(), nullable=True),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["manifest_version"],
            ["company_manifests.version"],
            name="fk_discovery_observations_manifest_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_discovery_observations_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_entry_id", "company_id"],
            ["job_entries.id", "job_entries.company_id"],
            name="fk_discovery_observation_entry_company",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_discovery_observations_manifest_status",
        "entry_discovery_observations",
        ["manifest_version", "status"],
    )
    op.create_index(
        "ix_discovery_observations_company_observed",
        "entry_discovery_observations",
        ["company_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_discovery_observations_company_observed",
        table_name="entry_discovery_observations",
    )
    op.drop_index(
        "ix_discovery_observations_manifest_status",
        table_name="entry_discovery_observations",
    )
    op.drop_table("entry_discovery_observations")

    op.drop_index(
        "ix_manifest_members_company",
        table_name="company_manifest_members",
    )
    op.drop_table("company_manifest_members")
    op.drop_table("company_manifests")

    op.drop_index(
        "ix_candidate_reviews_candidate_decided",
        table_name="candidate_reviews",
    )
    op.drop_table("candidate_reviews")
    op.drop_index(
        "ix_candidate_facts_decision_category",
        table_name="candidate_facts",
    )
    op.drop_table("candidate_facts")

    _drop_job_entry_company_unique()

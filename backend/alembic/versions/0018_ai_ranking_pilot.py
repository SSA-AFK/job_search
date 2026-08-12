"""Add internal-only AI ranking pilot records."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0018_ai_ranking_pilot"
down_revision: str | None = "0017_verified_primary_recruiting_entry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ranking_pilots",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("industry", sa.String(length=50), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("selection_seed", sa.String(length=128), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint(
            "industry", "input_sha256", "selection_seed", name="uq_ranking_pilot_input"
        ),
    )
    op.create_table(
        "ranking_pilot_members",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("pilot_id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("stratum", sa.String(length=500), nullable=False),
        sa.Column("selection_reason", sa.String(length=100), nullable=False),
        sa.Column("company_size", sa.String(length=50)),
        sa.Column("established_at", UTCDateTime()),
        sa.Column("insured_employee_count", sa.Integer()),
        sa.Column("employee_report_year", sa.Integer()),
        sa.ForeignKeyConstraint(["pilot_id"], ["ranking_pilots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("pilot_id", "company_id", name="uq_ranking_pilot_member_company"),
        sa.UniqueConstraint("pilot_id", "source_row", name="uq_ranking_pilot_member_row"),
    )
    op.create_table(
        "company_ranking_snapshots",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("pilot_id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("industry", sa.String(length=50), nullable=False),
        sa.Column("rule_version", sa.String(length=50), nullable=False),
        sa.Column("total_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("component_scores", sa.JSON(), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("is_eligible", sa.Boolean(), nullable=False),
        sa.Column("calculated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["pilot_id"], ["ranking_pilots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "pilot_id", "company_id", "rule_version", name="uq_company_ranking_snapshot_version"
        ),
    )
    op.create_table(
        "company_ranking_snapshot_evidence",
        sa.Column("snapshot_id", GUID(), nullable=False),
        sa.Column("source_document_id", GUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["company_ranking_snapshots.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "source_document_id"),
    )


def downgrade() -> None:
    op.drop_table("company_ranking_snapshot_evidence")
    op.drop_table("company_ranking_snapshots")
    op.drop_table("ranking_pilot_members")
    op.drop_table("ranking_pilots")

"""Add recruitment entries and list snapshots.

Revision ID: 0006_job_entries_and_snapshots
Revises: 0005_extend_job_type_values
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0006_job_entries_and_snapshots"
down_revision: str | None = "0005_extend_job_type_values"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

job_entry_status = sa.Enum(
    "unknown",
    "active",
    "stale",
    "disabled",
    name="job_entry_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
job_snapshot_status = sa.Enum(
    "succeeded",
    "partial",
    "failed",
    name="job_snapshot_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)


def upgrade() -> None:
    op.create_table(
        "job_entries",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("normalized_url", sa.String(2000), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("requires_rendering", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", job_entry_status, server_default="unknown", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_checked_at", UTCDateTime(), nullable=True),
        sa.Column("last_success_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "normalized_url", name="uq_job_entry_company_url"),
    )
    op.create_index("ix_job_entries_status_checked", "job_entries", ["status", "last_checked_at"])
    op.create_index("ix_job_entries_platform_status", "job_entries", ["platform", "status"])

    op.create_table(
        "job_collection_snapshots",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("job_entry_id", GUID(), nullable=False),
        sa.Column("crawl_run_id", GUID(), nullable=True),
        sa.Column("status", job_snapshot_status, nullable=False),
        sa.Column("pagination_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("empty_confirmed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reported_total", sa.Integer(), nullable=True),
        sa.Column("observed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pages_fetched", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("completed_at", UTCDateTime(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["crawl_run_id"], ["crawl_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_entry_id"], ["job_entries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_entry_id", "crawl_run_id", name="uq_job_snapshot_entry_run"),
    )
    op.create_index(
        "ix_job_snapshots_entry_completed",
        "job_collection_snapshots",
        ["job_entry_id", "completed_at"],
    )
    op.create_index(
        "ix_job_snapshots_status_completed",
        "job_collection_snapshots",
        ["status", "completed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_snapshots_status_completed", table_name="job_collection_snapshots")
    op.drop_index("ix_job_snapshots_entry_completed", table_name="job_collection_snapshots")
    op.drop_table("job_collection_snapshots")
    op.drop_index("ix_job_entries_platform_status", table_name="job_entries")
    op.drop_index("ix_job_entries_status_checked", table_name="job_entries")
    op.drop_table("job_entries")

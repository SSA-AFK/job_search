"""Add minimized AI ranking signals and collection audit records."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0019_ai_ranking_signals"
down_revision: str | None = "0018_ai_ranking_pilot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ranking_collection_runs",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("pilot_id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("run_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("logical_call_count", sa.Integer(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("response_sha256", sa.String(length=64)),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime()),
        sa.ForeignKeyConstraint(["pilot_id"], ["ranking_pilots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "pilot_id", "company_id", "category", "run_key", name="uq_ranking_collection_run_key"
        ),
    )
    op.create_table(
        "company_ranking_signals",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("source_document_id", GUID()),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("signal_key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("event_date", UTCDateTime()),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_sha256", sa.String(length=64)),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("verification_status", sa.String(length=50), nullable=False),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime()),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "company_id",
            "category",
            "signal_key",
            "source_fingerprint",
            name="uq_company_ranking_signal_source",
        ),
    )
    with op.batch_alter_table("company_ranking_snapshots") as batch:
        batch.add_column(
            sa.Column("raw_component_scores", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("stage_percentiles", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("evidence_coverage", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("company_stage", sa.String(length=20)))
        batch.add_column(
            sa.Column("eligibility_reasons", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("company_ranking_snapshots") as batch:
        batch.drop_column("eligibility_reasons")
        batch.drop_column("company_stage")
        batch.drop_column("evidence_coverage")
        batch.drop_column("stage_percentiles")
        batch.drop_column("raw_component_scores")
    op.drop_table("company_ranking_signals")
    op.drop_table("ranking_collection_runs")

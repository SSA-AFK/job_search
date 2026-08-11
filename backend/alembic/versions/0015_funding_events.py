"""Store funding events, investors, and independent source links."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0015_funding_events"
down_revision: str | None = "0014_company_profile_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "funding_events",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("round_label", sa.String(length=50), nullable=False),
        sa.Column("announced_at", sa.Date()),
        sa.Column("amount", sa.Numeric(precision=18, scale=2)),
        sa.Column("currency", sa.String(length=12)),
        sa.Column("verification_status", sa.String(length=50), nullable=False, server_default="pending_verification"),
        sa.Column("collected_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("company_id", "round_label", "announced_at", name="uq_funding_event_company_round_date"),
    )
    op.create_table(
        "funding_investors",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("funding_event_id", GUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["funding_event_id"], ["funding_events.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("funding_event_id", "normalized_name", name="uq_funding_investor_event_name"),
    )
    op.create_table(
        "funding_event_sources",
        sa.Column("funding_event_id", GUID(), nullable=False),
        sa.Column("source_document_id", GUID(), nullable=False),
        sa.ForeignKeyConstraint(["funding_event_id"], ["funding_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("funding_event_id", "source_document_id"),
    )


def downgrade() -> None:
    op.drop_table("funding_event_sources")
    op.drop_table("funding_investors")
    op.drop_table("funding_events")

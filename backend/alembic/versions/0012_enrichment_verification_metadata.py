"""Store verification metadata for enriched company detail records.

Revision ID: 0012_enrichment_verification_metadata
Revises: 0011_entry_evidence_integrity
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_enrichment_verification_metadata"
down_revision: str | None = "0011_entry_evidence_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("regulatory_filings", "job_sources"):
        op.add_column(
            table_name,
            sa.Column(
                "verification_status",
                sa.String(length=50),
                nullable=False,
                server_default="pending_verification",
            ),
        )
    op.add_column(
        "company_sources",
        sa.Column("field_verification", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("company_sources", "field_verification")
    op.drop_column("job_sources", "verification_status")
    op.drop_column("regulatory_filings", "verification_status")

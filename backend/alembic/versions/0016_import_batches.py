"""Store local workbook cohort provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import GUID, UTCDateTime

revision: str = "0016_import_batches"
down_revision: str | None = "0015_funding_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("workbook_filename", sa.String(length=500), nullable=False),
        sa.Column("worksheet_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("workbook_filename", "worksheet_name", name="uq_import_batch_workbook_sheet"),
    )
    op.create_table(
        "import_items",
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("import_batch_id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_source_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["import_batch_id"], ["import_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("import_batch_id", "source_row", name="uq_import_item_batch_row"),
    )


def downgrade() -> None:
    op.drop_table("import_items")
    op.drop_table("import_batches")

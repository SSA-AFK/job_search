"""Persist one verified primary recruiting URL per company."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import UTCDateTime

revision: str = "0017_verified_primary_recruiting_entry"
down_revision: str | None = "0016_import_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    verification_status = sa.Enum(
        "verified",
        "pending_verification",
        name="job_entry_verification_status",
        native_enum=False,
        create_constraint=True,
        length=30,
    )
    with op.batch_alter_table("job_entries") as batch:
        batch.add_column(
            sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False)
        )
        batch.add_column(
            sa.Column(
                "verification_status",
                verification_status,
                server_default="pending_verification",
                nullable=False,
            )
        )
        batch.add_column(sa.Column("verified_at", UTCDateTime(), nullable=True))
    op.create_index(
        "uq_job_entries_primary_per_company",
        "job_entries",
        ["company_id"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1"),
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    op.drop_index("uq_job_entries_primary_per_company", table_name="job_entries")
    with op.batch_alter_table("job_entries") as batch:
        batch.drop_column("verified_at")
        batch.drop_column("verification_status")
        batch.drop_column("is_primary")

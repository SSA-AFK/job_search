"""Track job source snapshot lifecycle state.

Revision ID: 0007_job_source_snapshot_lifecycle
Revises: 0006_job_entries_and_snapshots
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op
from app.models.base import GUID

revision: str = "0007_job_source_snapshot_lifecycle"
down_revision: str | None = "0006_job_entries_and_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTRY_FK_NAME = "fk_job_sources_job_entry_id"
SNAPSHOT_FK_NAME = "fk_job_sources_last_seen_snapshot_id"
ENTRY_ACTIVE_INDEX_NAME = "ix_job_sources_entry_active"


def _is_sqlite_online() -> bool:
    return op.get_context().dialect.name == "sqlite" and not context.is_offline_mode()


def upgrade() -> None:
    if _is_sqlite_online():
        with op.batch_alter_table("job_sources", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("job_entry_id", GUID(), nullable=True))
            batch_op.add_column(sa.Column("last_seen_snapshot_id", GUID(), nullable=True))
            batch_op.add_column(
                sa.Column(
                    "missing_complete_snapshots",
                    sa.Integer(),
                    server_default=sa.text("0"),
                    nullable=False,
                )
            )
            batch_op.create_foreign_key(
                ENTRY_FK_NAME,
                "job_entries",
                ["job_entry_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_foreign_key(
                SNAPSHOT_FK_NAME,
                "job_collection_snapshots",
                ["last_seen_snapshot_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                ENTRY_ACTIVE_INDEX_NAME,
                ["job_entry_id", "is_active"],
            )
        return

    op.add_column("job_sources", sa.Column("job_entry_id", GUID(), nullable=True))
    op.add_column("job_sources", sa.Column("last_seen_snapshot_id", GUID(), nullable=True))
    op.add_column(
        "job_sources",
        sa.Column(
            "missing_complete_snapshots",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        ENTRY_FK_NAME,
        "job_sources",
        "job_entries",
        ["job_entry_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        SNAPSHOT_FK_NAME,
        "job_sources",
        "job_collection_snapshots",
        ["last_seen_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        ENTRY_ACTIVE_INDEX_NAME,
        "job_sources",
        ["job_entry_id", "is_active"],
    )


def downgrade() -> None:
    if _is_sqlite_online():
        with op.batch_alter_table("job_sources", recreate="always") as batch_op:
            batch_op.drop_index(ENTRY_ACTIVE_INDEX_NAME)
            batch_op.drop_constraint(SNAPSHOT_FK_NAME, type_="foreignkey")
            batch_op.drop_constraint(ENTRY_FK_NAME, type_="foreignkey")
            batch_op.drop_column("missing_complete_snapshots")
            batch_op.drop_column("last_seen_snapshot_id")
            batch_op.drop_column("job_entry_id")
        return

    op.drop_index(ENTRY_ACTIVE_INDEX_NAME, table_name="job_sources")
    op.drop_constraint(SNAPSHOT_FK_NAME, "job_sources", type_="foreignkey")
    op.drop_constraint(ENTRY_FK_NAME, "job_sources", type_="foreignkey")
    op.drop_column("job_sources", "missing_complete_snapshots")
    op.drop_column("job_sources", "last_seen_snapshot_id")
    op.drop_column("job_sources", "job_entry_id")

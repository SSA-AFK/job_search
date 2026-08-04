"""Add an unambiguous worker claim generation token.

Revision ID: 0004_crawl_run_claim_token
Revises: 0003_source_document_null_external_identity
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_crawl_run_claim_token"
down_revision: str | None = "0003_source_document_null_external_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CRAWL_RUNS = sa.table(
    "crawl_runs",
    sa.column("id", sa.Uuid()),
    sa.column("status", sa.String()),
    sa.column("claim_token", sa.String(36)),
)
BACKFILL_RUNNING_CLAIMS = (
    sa.update(_CRAWL_RUNS)
    .where(
        _CRAWL_RUNS.c.status == "running",
        _CRAWL_RUNS.c.claim_token.is_(None),
    )
    .values(claim_token=sa.cast(_CRAWL_RUNS.c.id, sa.String(36)))
)


def upgrade() -> None:
    op.add_column("crawl_runs", sa.Column("claim_token", sa.String(36), nullable=True))
    op.execute(BACKFILL_RUNNING_CLAIMS)


def downgrade() -> None:
    op.drop_column("crawl_runs", "claim_token")

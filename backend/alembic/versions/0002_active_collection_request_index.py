"""Add the active collection request uniqueness constraint.

Revision ID: 0002_active_collection_request_index
Revises: 0001_initial_schema
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_active_collection_request_index"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_REQUESTS = sa.text("status IN ('queued', 'running')")


def upgrade() -> None:
    op.create_index(
        "uq_collection_requests_active_query",
        "collection_requests",
        ["normalized_query"],
        unique=True,
        sqlite_where=ACTIVE_REQUESTS,
        postgresql_where=ACTIVE_REQUESTS,
    )


def downgrade() -> None:
    op.drop_index("uq_collection_requests_active_query", table_name="collection_requests")

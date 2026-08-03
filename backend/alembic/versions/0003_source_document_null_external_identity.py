"""Add identity enforcement for source documents without external IDs.

Revision ID: 0003_source_document_null_external_identity
Revises: 0002_active_collection_request_index
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_source_document_null_external_identity"
down_revision: str | None = "0002_active_collection_request_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WITHOUT_EXTERNAL_ID = sa.text("external_id IS NULL")
INDEX_NAME = "uq_source_document_provider_url_hash_without_external_id"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "source_documents",
        ["provider", "url", "content_hash"],
        unique=True,
        sqlite_where=WITHOUT_EXTERNAL_ID,
        postgresql_where=WITHOUT_EXTERNAL_ID,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="source_documents")

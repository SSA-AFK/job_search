"""Add durable company profile basics for detail pages.

Revision ID: 0013_company_profile_basics
Revises: 0012_enrichment_verification_metadata
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_company_profile_basics"
down_revision: str | None = "0012_enrichment_verification_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("headquarters", sa.String(length=300), nullable=True))
    op.add_column("companies", sa.Column("founded_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "founded_year")
    op.drop_column("companies", "headquarters")

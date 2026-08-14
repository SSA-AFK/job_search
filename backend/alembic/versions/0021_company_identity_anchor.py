"""Add internal Tianyancha company identity anchor fields."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_company_identity_anchor"
down_revision: str | None = "0020_company_public_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("legal_name", sa.String(length=255)))
    op.add_column("companies", sa.Column("tianyancha_company_id", sa.String(length=64)))
    op.add_column("companies", sa.Column("uscc_sha256", sa.String(length=64)))
    op.add_column(
        "companies",
        sa.Column(
            "identity_anchor_status",
            sa.String(length=32),
            nullable=False,
            server_default="unverified",
        ),
    )
    op.add_column("companies", sa.Column("identity_anchored_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for name in (
        "identity_anchored_at",
        "identity_anchor_status",
        "uscc_sha256",
        "tianyancha_company_id",
        "legal_name",
    ):
        op.drop_column("companies", name)

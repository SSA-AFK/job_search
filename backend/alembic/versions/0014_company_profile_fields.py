"""Store evidence-backed industry profile fields."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0014_company_profile_fields"
down_revision: str | None = "0013_company_profile_basics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_profile_fields",
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("field_key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("source_document_id", GUID(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum(
                "verified",
                "pending_verification",
                name="profile_field_verification_status",
                native_enum=False,
                create_constraint=True,
                length=50,
            ),
            nullable=False,
            server_default="pending_verification",
        ),
        sa.Column("collected_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("company_id", "field_key"),
    )


def downgrade() -> None:
    op.drop_table("company_profile_fields")

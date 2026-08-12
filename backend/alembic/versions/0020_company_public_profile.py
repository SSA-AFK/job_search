"""Add public, non-personal company profile fields for ranking detail pages."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_company_public_profile"
down_revision: str | None = "0019_ai_ranking_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("established_at", sa.Date()))
    op.add_column("companies", sa.Column("province", sa.String(length=50)))
    op.add_column("companies", sa.Column("district", sa.String(length=50)))
    op.add_column("companies", sa.Column("company_type", sa.String(length=100)))
    op.add_column("companies", sa.Column("registered_capital", sa.String(length=100)))
    op.add_column("companies", sa.Column("paid_in_capital", sa.String(length=100)))
    op.add_column("companies", sa.Column("industry_sector", sa.String(length=100)))
    op.add_column("companies", sa.Column("industry_middle", sa.String(length=100)))
    op.add_column("companies", sa.Column("insured_employee_count", sa.Integer()))
    op.add_column("companies", sa.Column("employee_report_year", sa.Integer()))
    op.add_column("companies", sa.Column("business_scope", sa.Text()))


def downgrade() -> None:
    for name in (
        "business_scope",
        "employee_report_year",
        "insured_employee_count",
        "industry_middle",
        "industry_sector",
        "paid_in_capital",
        "registered_capital",
        "company_type",
        "district",
        "province",
        "established_at",
    ):
        op.drop_column("companies", name)

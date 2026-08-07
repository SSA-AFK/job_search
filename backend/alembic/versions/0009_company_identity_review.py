"""Add company identity review persistence and bounded similarity indexes.

Revision ID: 0009_company_identity_review
Revises: 0008_gate1_manifest_discovery
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0009_company_identity_review"
down_revision: str | None = "0008_gate1_manifest_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

identity_review_status = sa.Enum(
    "pending",
    "resolved",
    "rejected",
    name="identity_review_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
identity_review_action = sa.Enum(
    "link_as_alias",
    "create_new",
    "rename_canonical",
    "reject",
    name="identity_review_action",
    native_enum=False,
    create_constraint=True,
    length=30,
)


def _lowercase_hex_check(column_name: str) -> str:
    remainder = column_name
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column_name}) = 64 AND "
        f"{column_name} = lower({column_name}) AND {remainder} = ''"
    )


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "company_identity_review_items",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("stable_identity_hash", sa.String(64), nullable=False),
        sa.Column("first_crawl_run_id", GUID(), nullable=False),
        sa.Column(
            "status",
            identity_review_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("candidate_name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("official_website", sa.String(2000), nullable=True),
        sa.Column("recruitment_identity", sa.String(255), nullable=True),
        sa.Column("legal_identifiers", sa.JSON(), nullable=False),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("public_evidence_refs", sa.JSON(), nullable=False),
        sa.Column("candidate_matches", sa.JSON(), nullable=False),
        sa.Column("review_reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.CheckConstraint(
            _lowercase_hex_check("stable_identity_hash"),
            name="ck_identity_review_item_hash_format",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR "
            "(status IN ('resolved', 'rejected') AND resolved_at IS NOT NULL)",
            name="ck_identity_review_item_status_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["first_crawl_run_id"],
            ["crawl_runs.id"],
            name="fk_company_identity_review_items_first_crawl_run_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stable_identity_hash",
            name="uq_identity_review_item_stable_hash",
        ),
    )
    op.create_index(
        "ix_company_identity_review_items_status_created",
        "company_identity_review_items",
        ["status", "created_at"],
    )

    op.create_table(
        "company_identity_review_decisions",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("review_item_id", GUID(), nullable=False),
        sa.Column("action", identity_review_action, nullable=False),
        sa.Column("target_company_id", GUID(), nullable=True),
        sa.Column("resulting_company_id", GUID(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_at", UTCDateTime(), nullable=False),
        sa.Column("decision_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            _lowercase_hex_check("decision_hash"),
            name="ck_identity_review_decision_hash_format",
        ),
        sa.CheckConstraint(
            "length(reason) BETWEEN 1 AND 2000",
            name="ck_identity_review_decision_reason_length",
        ),
        sa.CheckConstraint(
            "(action IN ('link_as_alias', 'rename_canonical') "
            "AND target_company_id IS NOT NULL) OR "
            "(action IN ('create_new', 'reject') AND target_company_id IS NULL)",
            name="ck_identity_review_decision_action_target",
        ),
        sa.ForeignKeyConstraint(
            ["review_item_id"],
            ["company_identity_review_items.id"],
            name="fk_company_identity_review_decisions_review_item_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_company_id"],
            ["companies.id"],
            name="fk_company_identity_review_decisions_target_company_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_company_id"],
            ["companies.id"],
            name="fk_company_identity_review_decisions_resulting_company_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_hash",
            name="uq_identity_review_decision_hash",
        ),
        sa.UniqueConstraint(
            "review_item_id",
            name="uq_identity_review_decision_item",
        ),
    )

    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX ix_companies_normalized_name_trgm "
            "ON companies USING gist (normalized_name gist_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_company_aliases_normalized_alias_trgm "
            "ON company_aliases USING gist (normalized_alias gist_trgm_ops)"
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS ix_company_aliases_normalized_alias_trgm")
        op.execute("DROP INDEX IF EXISTS ix_companies_normalized_name_trgm")

    op.drop_table("company_identity_review_decisions")
    op.drop_index(
        "ix_company_identity_review_items_status_created",
        table_name="company_identity_review_items",
    )
    op.drop_table("company_identity_review_items")

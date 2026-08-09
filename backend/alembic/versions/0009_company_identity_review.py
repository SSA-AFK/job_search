"""Add company identity review persistence and bounded similarity indexes.

Revision ID: 0009_company_identity_review
Revises: 0008_gate1_manifest_discovery
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from alembic import op
from app.core.normalization import normalize_name, normalize_public_identity_url
from app.models.base import GUID, UTCDateTime

revision: str = "0009_company_identity_review"
down_revision: str | None = "0008_gate1_manifest_discovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RAW_FILING_UNIQUE = "uq_filing_type_number"
_NORMALIZED_FILING_UNIQUE = "uq_filing_type_normalized_number"
_SQLITE_FILING_PREFLIGHT_TABLE = "_0009_normalized_filing_identity_preflight"
_POSTGRESQL_PG_TRGM_EXTENSION_SQL = (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public"
)
_POSTGRESQL_COMPANIES_TRGM_INDEX_SQL = (
    "CREATE INDEX ix_companies_normalized_name_trgm "
    "ON companies USING gist (normalized_name public.gist_trgm_ops)"
)
_POSTGRESQL_ALIASES_TRGM_INDEX_SQL = (
    "CREATE INDEX ix_company_aliases_normalized_alias_trgm "
    "ON company_aliases USING gist (normalized_alias public.gist_trgm_ops)"
)

_POSTGRESQL_OFFLINE_BACKFILL_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM companies WHERE website IS NOT NULL)
       OR EXISTS (SELECT 1 FROM regulatory_filings) THEN
        RAISE EXCEPTION '0009 normalized evidence backfill requires online migration';
    END IF;
END
$$
"""

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

_filing_type = sa.Enum(
    "icp",
    "algorithm",
    "business_license",
    name="filing_type",
    native_enum=False,
    create_constraint=True,
    length=50,
)
_REGULATORY_FILINGS_WITH_RAW_UNIQUE_METADATA = sa.MetaData()
_REGULATORY_FILINGS_WITH_RAW_UNIQUE = sa.Table(
    "regulatory_filings",
    _REGULATORY_FILINGS_WITH_RAW_UNIQUE_METADATA,
    sa.Column("id", GUID(), primary_key=True, nullable=False),
    sa.Column("company_id", GUID(), nullable=False),
    sa.Column("source_document_id", GUID(), nullable=True),
    sa.Column("filing_type", _filing_type, nullable=False),
    sa.Column("filing_number", sa.String(255), nullable=False),
    sa.Column(
        "normalized_filing_number",
        sa.String(255),
        server_default=sa.text("''"),
        nullable=False,
    ),
    sa.Column("filing_name", sa.String(255), nullable=False),
    sa.Column("filing_authority", sa.String(255), nullable=True),
    sa.Column("filing_date", sa.Date(), nullable=True),
    sa.Column("filing_status", sa.String(50), nullable=True),
    sa.Column("detail_url", sa.String(2000), nullable=True),
    sa.Column("created_at", UTCDateTime(), nullable=False),
    sa.Column("updated_at", UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(
        ["source_document_id"], ["source_documents.id"], ondelete="SET NULL"
    ),
    sa.UniqueConstraint(
        "filing_type",
        "filing_number",
        name=_RAW_FILING_UNIQUE,
    ),
)
_REGULATORY_FILINGS_WITH_NORMALIZED_UNIQUE_METADATA = sa.MetaData()
_REGULATORY_FILINGS_WITH_NORMALIZED_UNIQUE = _REGULATORY_FILINGS_WITH_RAW_UNIQUE.to_metadata(
    _REGULATORY_FILINGS_WITH_NORMALIZED_UNIQUE_METADATA
)
for constraint in tuple(_REGULATORY_FILINGS_WITH_NORMALIZED_UNIQUE.constraints):
    if constraint.name == _RAW_FILING_UNIQUE:
        _REGULATORY_FILINGS_WITH_NORMALIZED_UNIQUE.constraints.remove(constraint)
_REGULATORY_FILINGS_WITH_NORMALIZED_UNIQUE.append_constraint(
    sa.UniqueConstraint(
        "filing_type",
        "normalized_filing_number",
        name=_NORMALIZED_FILING_UNIQUE,
    )
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


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _normalized_filing_number_for_backfill(filing_number: str) -> str:
    normalized_filing_number = normalize_name(filing_number)
    if not normalized_filing_number:
        raise RuntimeError("filing number backfill is not normalizable")
    if len(normalized_filing_number) > 255:
        raise RuntimeError("filing number backfill exceeds normalized length")
    return normalized_filing_number


def _preflight_sqlite_normalized_filing_identities() -> None:
    if not _is_sqlite() or op.get_context().as_sql:
        return

    connection = op.get_bind()
    filings = sa.table(
        "regulatory_filings",
        sa.column("id", GUID()),
        sa.column("filing_type", sa.String(50)),
        sa.column("filing_number", sa.String(255)),
    )
    connection.exec_driver_sql(
        f"CREATE TEMPORARY TABLE {_SQLITE_FILING_PREFLIGHT_TABLE} ("
        "filing_type VARCHAR(50) NOT NULL, "
        "normalized_filing_number VARCHAR(255) NOT NULL, "
        "PRIMARY KEY (filing_type, normalized_filing_number))"
    )
    try:
        last_filing_id = None
        while True:
            statement = (
                sa.select(
                    filings.c.id,
                    filings.c.filing_type,
                    filings.c.filing_number,
                )
                .order_by(filings.c.id)
                .limit(500)
            )
            if last_filing_id is not None:
                statement = statement.where(filings.c.id > last_filing_id)
            rows = connection.execute(statement).all()
            if not rows:
                break
            identities = [
                {
                    "filing_type": filing_type,
                    "normalized_filing_number": (
                        _normalized_filing_number_for_backfill(filing_number)
                    ),
                }
                for _filing_id, filing_type, filing_number in rows
            ]
            try:
                connection.execute(
                    sa.text(
                        f"INSERT INTO {_SQLITE_FILING_PREFLIGHT_TABLE} "
                        "(filing_type, normalized_filing_number) "
                        "VALUES (:filing_type, :normalized_filing_number)"
                    ),
                    identities,
                )
            except IntegrityError:
                raise RuntimeError("0009 normalized filing identity collision") from None
            last_filing_id = rows[-1][0]
    finally:
        connection.exec_driver_sql(f"DROP TABLE {_SQLITE_FILING_PREFLIGHT_TABLE}")


def _backfill_normalized_evidence() -> None:
    if op.get_context().as_sql:
        return

    connection = op.get_bind()
    companies = sa.table(
        "companies",
        sa.column("id", GUID()),
        sa.column("website", sa.String(1000)),
        sa.column("normalized_website", sa.String(1000)),
    )
    filings = sa.table(
        "regulatory_filings",
        sa.column("id", GUID()),
        sa.column("filing_number", sa.String(255)),
        sa.column("normalized_filing_number", sa.String(255)),
    )

    last_company_id = None
    while True:
        statement = (
            sa.select(companies.c.id, companies.c.website)
            .where(
                companies.c.website.is_not(None),
            )
            .order_by(companies.c.id)
            .limit(500)
        )
        if last_company_id is not None:
            statement = statement.where(companies.c.id > last_company_id)
        rows = connection.execute(statement).all()
        if not rows:
            break
        for company_id, website in rows:
            try:
                normalized_website = normalize_public_identity_url(website)
            except (UnicodeError, ValueError) as exc:
                raise RuntimeError("company website backfill is not normalizable") from exc
            if len(normalized_website) > 1_000:
                raise RuntimeError("company website backfill exceeds normalized length")
            connection.execute(
                sa.update(companies)
                .where(companies.c.id == company_id)
                .values(normalized_website=normalized_website)
            )
        last_company_id = rows[-1][0]

    last_filing_id = None
    while True:
        statement = (
            sa.select(filings.c.id, filings.c.filing_number)
            .order_by(filings.c.id)
            .limit(500)
        )
        if last_filing_id is not None:
            statement = statement.where(filings.c.id > last_filing_id)
        rows = connection.execute(statement).all()
        if not rows:
            break
        for filing_id, filing_number in rows:
            normalized_filing_number = _normalized_filing_number_for_backfill(
                filing_number
            )
            connection.execute(
                sa.update(filings)
                .where(filings.c.id == filing_id)
                .values(normalized_filing_number=normalized_filing_number)
            )
        last_filing_id = rows[-1][0]


def _require_unique_normalized_filing_identities() -> None:
    if op.get_context().as_sql:
        return

    filings = sa.table(
        "regulatory_filings",
        sa.column("filing_type", sa.String(50)),
        sa.column("normalized_filing_number", sa.String(255)),
    )
    collision = op.get_bind().execute(
        sa.select(filings.c.filing_type)
        .group_by(filings.c.filing_type, filings.c.normalized_filing_number)
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if collision is not None:
        raise RuntimeError("0009 normalized filing identity collision")


def _use_normalized_filing_unique() -> None:
    if _is_sqlite():
        with op.batch_alter_table(
            "regulatory_filings",
            recreate="always",
            copy_from=(
                _REGULATORY_FILINGS_WITH_RAW_UNIQUE
                if op.get_context().as_sql
                else None
            ),
        ) as batch_op:
            batch_op.drop_constraint(_RAW_FILING_UNIQUE, type_="unique")
            batch_op.create_unique_constraint(
                _NORMALIZED_FILING_UNIQUE,
                ["filing_type", "normalized_filing_number"],
            )
        return

    op.drop_constraint(_RAW_FILING_UNIQUE, "regulatory_filings", type_="unique")
    op.create_unique_constraint(
        _NORMALIZED_FILING_UNIQUE,
        "regulatory_filings",
        ["filing_type", "normalized_filing_number"],
    )


def _restore_raw_filing_unique_and_drop_normalized_column() -> None:
    if _is_sqlite():
        with op.batch_alter_table(
            "regulatory_filings",
            recreate="always",
            copy_from=(
                _REGULATORY_FILINGS_WITH_NORMALIZED_UNIQUE
                if op.get_context().as_sql
                else None
            ),
        ) as batch_op:
            batch_op.drop_constraint(_NORMALIZED_FILING_UNIQUE, type_="unique")
            batch_op.create_unique_constraint(
                _RAW_FILING_UNIQUE,
                ["filing_type", "filing_number"],
            )
            batch_op.drop_column("normalized_filing_number")
        return

    op.drop_constraint(_NORMALIZED_FILING_UNIQUE, "regulatory_filings", type_="unique")
    op.create_unique_constraint(
        _RAW_FILING_UNIQUE,
        "regulatory_filings",
        ["filing_type", "filing_number"],
    )
    op.drop_column("regulatory_filings", "normalized_filing_number")


def upgrade() -> None:
    _preflight_sqlite_normalized_filing_identities()
    if _is_postgresql() and op.get_context().as_sql:
        op.execute(_POSTGRESQL_OFFLINE_BACKFILL_GUARD)

    op.add_column(
        "companies",
        sa.Column(
            "normalized_website",
            sa.String(1000),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.add_column(
        "regulatory_filings",
        sa.Column(
            "normalized_filing_number",
            sa.String(255),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    _backfill_normalized_evidence()
    _require_unique_normalized_filing_identities()
    _use_normalized_filing_unique()
    op.create_index(
        "ix_companies_normalized_website",
        "companies",
        ["normalized_website"],
    )
    op.create_index(
        "ix_regulatory_filings_normalized_filing_number",
        "regulatory_filings",
        ["normalized_filing_number"],
    )

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
        op.execute(_POSTGRESQL_PG_TRGM_EXTENSION_SQL)
        op.execute(_POSTGRESQL_COMPANIES_TRGM_INDEX_SQL)
        op.execute(_POSTGRESQL_ALIASES_TRGM_INDEX_SQL)


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
    op.drop_index(
        "ix_regulatory_filings_normalized_filing_number",
        table_name="regulatory_filings",
    )
    op.drop_index(
        "ix_companies_normalized_website",
        table_name="companies",
    )
    _restore_raw_filing_unique_and_drop_normalized_column()
    op.drop_column("companies", "normalized_website")

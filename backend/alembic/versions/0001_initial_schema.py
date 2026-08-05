"""Create the normalized company data schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


collection_status = sa.Enum(
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    name="collection_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
run_type = sa.Enum(
    "discovery",
    "company_refresh",
    "on_demand",
    "expiration",
    name="run_type",
    native_enum=False,
    create_constraint=True,
    length=30,
)
job_type = sa.Enum(
    "full_time",
    "internship",
    "campus",
    "experienced",
    "unknown",
    name="job_type",
    native_enum=False,
    create_constraint=True,
    length=50,
)
filing_type = sa.Enum(
    "icp",
    "algorithm",
    "business_license",
    name="filing_type",
    native_enum=False,
    create_constraint=True,
    length=50,
)
crawl_run_status = sa.Enum(
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    name="crawl_run_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
    op.create_table(
        "companies",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("sub_industry", sa.String(100), nullable=True),
        sa.Column("funding_stage", sa.String(50), nullable=False),
        sa.Column("scale", sa.String(50), nullable=False),
        sa.Column("city", sa.String(50), nullable=True),
        sa.Column("logo_url", sa.String(1000), nullable=True),
        sa.Column("website", sa.String(1000), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("last_collected_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name", name="uq_company_normalized_name"),
    )
    op.create_index("ix_companies_normalized_name", "companies", ["normalized_name"])
    op.create_index("ix_companies_industry", "companies", ["industry"])
    op.create_index("ix_companies_sub_industry", "companies", ["sub_industry"])
    op.create_index("ix_companies_funding_stage", "companies", ["funding_stage"])
    op.create_index("ix_companies_scale", "companies", ["scale"])
    op.create_index("ix_companies_city", "companies", ["city"])

    op.create_table(
        "source_documents",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("text_excerpt", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("authority_level", sa.SmallInteger(), nullable=True),
        sa.Column("published_at", UTCDateTime(), nullable=True),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_source_document_provider_external_id"
        ),
    )

    op.create_table(
        "company_aliases",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_alias", name="uq_company_alias_normalized_alias"),
    )
    op.create_index(
        "ix_company_aliases_normalized_alias", "company_aliases", ["normalized_alias"]
    )

    op.create_table(
        "company_sources",
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("source_document_id", GUID(), nullable=False),
        sa.Column("covered_fields", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("company_id", "source_document_id"),
    )

    op.create_table(
        "job_postings",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("normalized_title", sa.String(255), nullable=False),
        sa.Column("job_type", job_type, nullable=False),
        sa.Column("city", sa.String(50), nullable=False),
        sa.Column("salary_min_monthly", sa.Integer(), nullable=True),
        sa.Column("salary_max_monthly", sa.Integer(), nullable=True),
        sa.Column("salary_months", sa.SmallInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_postings_company_active", "job_postings", ["company_id", "is_active"]
    )

    op.create_table(
        "job_sources",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("job_posting_id", GUID(), nullable=False),
        sa.Column("source_document_id", GUID(), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("source_raw_id", sa.String(255), nullable=False),
        sa.Column("apply_url", sa.String(2000), nullable=False),
        sa.Column("first_seen_at", UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_posting_id"], ["job_postings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["source_documents.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "source_raw_id", name="uq_job_source_provider_raw_id"),
    )

    op.create_table(
        "regulatory_filings",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("company_id", GUID(), nullable=False),
        sa.Column("source_document_id", GUID(), nullable=True),
        sa.Column("filing_type", filing_type, nullable=False),
        sa.Column("filing_number", sa.String(255), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filing_type", "filing_number", name="uq_filing_type_number"),
    )

    op.create_table(
        "collection_requests",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("query", sa.String(255), nullable=False),
        sa.Column("normalized_query", sa.String(255), nullable=False),
        sa.Column("status", collection_status, nullable=False),
        sa.Column("company_id", GUID(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_requests_status_query",
        "collection_requests",
        ["status", "normalized_query"],
    )

    op.create_table(
        "crawl_runs",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("collection_request_id", GUID(), nullable=True),
        sa.Column("company_id", GUID(), nullable=True),
        sa.Column("run_type", run_type, nullable=False),
        sa.Column("status", crawl_run_status, nullable=False),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("providers_attempted", sa.JSON(), nullable=False),
        sa.Column("documents_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("jobs_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("jobs_written", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("completed_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_request_id"], ["collection_requests.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("crawl_runs")
    op.drop_index("ix_collection_requests_status_query", table_name="collection_requests")
    op.drop_table("collection_requests")
    op.drop_table("regulatory_filings")
    op.drop_table("job_sources")
    op.drop_index("ix_job_postings_company_active", table_name="job_postings")
    op.drop_table("job_postings")
    op.drop_table("company_sources")
    op.drop_index("ix_company_aliases_normalized_alias", table_name="company_aliases")
    op.drop_table("company_aliases")
    op.drop_table("source_documents")
    op.drop_index("ix_companies_city", table_name="companies")
    op.drop_index("ix_companies_scale", table_name="companies")
    op.drop_index("ix_companies_funding_stage", table_name="companies")
    op.drop_index("ix_companies_sub_industry", table_name="companies")
    op.drop_index("ix_companies_industry", table_name="companies")
    op.drop_index("ix_companies_normalized_name", table_name="companies")
    op.drop_table("companies")
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=128),
            type_=sa.String(length=32),
            existing_nullable=False,
        )

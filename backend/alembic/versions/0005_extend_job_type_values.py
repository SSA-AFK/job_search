"""Add part-time and temporary job type values.

Revision ID: 0005_extend_job_type_values
Revises: 0004_crawl_run_claim_token
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

revision: str = "0005_extend_job_type_values"
down_revision: str | None = "0004_crawl_run_claim_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_JOB_TYPES = (
    "full_time",
    "internship",
    "campus",
    "experienced",
    "unknown",
)
_NEW_JOB_TYPES = (
    "full_time",
    "part_time",
    "internship",
    "temporary",
    "campus",
    "experienced",
    "unknown",
)
_JOB_POSTINGS = sa.table(
    "job_postings",
    sa.column("job_type", sa.String(50)),
)
_SQLITE_DEPENDENT_COLUMNS = {
    "job_sources": (
        "id",
        "job_posting_id",
        "source_document_id",
        "provider",
        "source_raw_id",
        "apply_url",
        "first_seen_at",
        "last_seen_at",
        "is_active",
    ),
}


def _check_constraint(values: tuple[str, ...]) -> str:
    literals = ", ".join(f"'{value}'" for value in values)
    return f"job_type IN ({literals})"


def _backup_sqlite_dependents(
    connection: Connection,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    inspector = sa.inspect(connection)
    dependent_tables = {
        table_name
        for table_name in inspector.get_table_names()
        for foreign_key in inspector.get_foreign_keys(table_name)
        if foreign_key["referred_table"] == "job_postings"
    }
    unsupported = dependent_tables.difference(_SQLITE_DEPENDENT_COLUMNS)
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise RuntimeError(f"unhandled job_postings dependents: {names}")

    quote = connection.dialect.identifier_preparer.quote
    backups = []
    for table_name in sorted(dependent_tables):
        columns = _SQLITE_DEPENDENT_COLUMNS[table_name]
        backup_name = f"_alembic_0005_{table_name}_backup"
        column_sql = ", ".join(quote(column) for column in columns)
        op.execute(
            sa.text(
                f"CREATE TEMPORARY TABLE {quote(backup_name)} AS "
                f"SELECT {column_sql} FROM {quote(table_name)}"
            )
        )
        backups.append((table_name, backup_name, columns))
    return tuple(backups)


def _restore_sqlite_dependents(
    connection: Connection,
    backups: tuple[tuple[str, str, tuple[str, ...]], ...],
) -> None:
    quote = connection.dialect.identifier_preparer.quote
    for table_name, backup_name, columns in backups:
        column_sql = ", ".join(quote(column) for column in columns)
        op.execute(
            sa.text(
                f"INSERT INTO {quote(table_name)} ({column_sql}) "
                f"SELECT {column_sql} FROM {quote(backup_name)}"
            )
        )
        op.execute(sa.text(f"DROP TABLE {quote(backup_name)}"))


def _replace_job_type_constraint(values: tuple[str, ...]) -> None:
    connection = op.get_bind()
    backups = (
        _backup_sqlite_dependents(connection)
        if connection.dialect.name == "sqlite"
        else ()
    )
    with op.batch_alter_table("job_postings") as batch_op:
        batch_op.drop_constraint("job_type", type_="check")
        batch_op.create_check_constraint("job_type", _check_constraint(values))
    if backups:
        _restore_sqlite_dependents(connection, backups)


def upgrade() -> None:
    _replace_job_type_constraint(_NEW_JOB_TYPES)


def downgrade() -> None:
    op.execute(
        sa.update(_JOB_POSTINGS)
        .where(_JOB_POSTINGS.c.job_type.in_(("part_time", "temporary")))
        .values(job_type="unknown")
    )
    _replace_job_type_constraint(_OLD_JOB_TYPES)

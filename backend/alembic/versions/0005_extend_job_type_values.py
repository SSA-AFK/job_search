"""Add part-time and temporary job type values.

Revision ID: 0005_extend_job_type_values
Revises: 0004_crawl_run_claim_token
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

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


def _check_constraint(values: tuple[str, ...]) -> str:
    literals = ", ".join(f"'{value}'" for value in values)
    return f"job_type IN ({literals})"


def _replace_job_type_constraint(values: tuple[str, ...]) -> None:
    with op.batch_alter_table("job_postings") as batch_op:
        batch_op.drop_constraint("job_type", type_="check")
        batch_op.create_check_constraint("job_type", _check_constraint(values))


def upgrade() -> None:
    _replace_job_type_constraint(_NEW_JOB_TYPES)


def downgrade() -> None:
    op.execute(
        sa.update(_JOB_POSTINGS)
        .where(_JOB_POSTINGS.c.job_type.in_(("part_time", "temporary")))
        .values(job_type="unknown")
    )
    _replace_job_type_constraint(_OLD_JOB_TYPES)

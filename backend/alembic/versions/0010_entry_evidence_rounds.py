"""Add immutable discovery rounds and append-only evidence audits.

Revision ID: 0010_entry_evidence_rounds
Revises: 0009_company_identity_review
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0010_entry_evidence_rounds"
down_revision: str | None = "0009_company_identity_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

entry_discovery_status = sa.Enum(
    "accepted",
    "review_required",
    "not_found",
    "blocked",
    "failed",
    name="entry_discovery_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)


def _is_sqlite_offline() -> bool:
    context = op.get_context()
    return context.dialect.name == "sqlite" and context.as_sql


def _observation_table(*, with_rounds: bool) -> sa.Table:
    metadata = sa.MetaData()
    columns: list[sa.SchemaItem] = [
        sa.Column("id", GUID(), primary_key=True, nullable=False),
        sa.Column("manifest_version", sa.String(64), nullable=False),
    ]
    if with_rounds:
        columns.extend(
            [
                sa.Column("discovery_round_id", GUID(), nullable=True),
                sa.Column("predecessor_observation_id", GUID(), nullable=True),
            ]
        )
    columns.extend(
        [
            sa.Column("company_id", GUID(), nullable=False),
            sa.Column("method", sa.String(100), nullable=False),
            sa.Column("status", entry_discovery_status, nullable=False),
            sa.Column("candidate_url", sa.String(2000), nullable=True),
            sa.Column("normalized_url", sa.String(2000), nullable=True),
            sa.Column("source_id", sa.String(50), nullable=True),
            sa.Column("ownership_evidence", sa.Text(), nullable=True),
            sa.Column("platform", sa.String(50), nullable=True),
            sa.Column(
                "requires_rendering",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            ),
            sa.Column("error_code", sa.String(100), nullable=True),
            sa.Column("job_entry_id", GUID(), nullable=True),
            sa.Column("observed_at", UTCDateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["manifest_version"],
                ["company_manifests.version"],
                name="fk_discovery_observations_manifest_version",
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["company_id"],
                ["companies.id"],
                name="fk_discovery_observations_company_id",
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["job_entry_id", "company_id"],
                ["job_entries.id", "job_entries.company_id"],
                name="fk_discovery_observation_entry_company",
                ondelete="RESTRICT",
            ),
        ]
    )
    if with_rounds:
        columns.extend(
            [
                sa.ForeignKeyConstraint(
                    ["discovery_round_id", "manifest_version"],
                    ["entry_discovery_rounds.id", "entry_discovery_rounds.manifest_version"],
                    name="fk_discovery_observation_round_manifest",
                    ondelete="RESTRICT",
                ),
                sa.ForeignKeyConstraint(
                    ["predecessor_observation_id", "manifest_version", "company_id"],
                    [
                        "entry_discovery_observations.id",
                        "entry_discovery_observations.manifest_version",
                        "entry_discovery_observations.company_id",
                    ],
                    name="fk_discovery_observation_predecessor_identity",
                    ondelete="RESTRICT",
                ),
                sa.UniqueConstraint(
                    "id",
                    "manifest_version",
                    "company_id",
                    name="uq_discovery_observation_identity",
                ),
                sa.UniqueConstraint(
                    "id",
                    "discovery_round_id",
                    name="uq_discovery_observation_id_round",
                ),
                sa.UniqueConstraint(
                    "discovery_round_id",
                    "company_id",
                    name="uq_discovery_observation_round_company",
                ),
            ]
        )
    table = sa.Table("entry_discovery_observations", metadata, *columns)
    sa.Index(
        "ix_discovery_observations_manifest_status",
        table.c.manifest_version,
        table.c.status,
    )
    sa.Index(
        "ix_discovery_observations_company_observed",
        table.c.company_id,
        table.c.observed_at,
    )
    return table


def _upgrade_observations() -> None:
    copy_from = _observation_table(with_rounds=False) if _is_sqlite_offline() else None
    with op.batch_alter_table(
        "entry_discovery_observations",
        copy_from=copy_from,
    ) as batch_op:
        batch_op.add_column(sa.Column("discovery_round_id", GUID(), nullable=True))
        batch_op.add_column(sa.Column("predecessor_observation_id", GUID(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_discovery_observation_identity",
            ["id", "manifest_version", "company_id"],
        )
        batch_op.create_unique_constraint(
            "uq_discovery_observation_id_round",
            ["id", "discovery_round_id"],
        )
        batch_op.create_unique_constraint(
            "uq_discovery_observation_round_company",
            ["discovery_round_id", "company_id"],
        )
        batch_op.create_foreign_key(
            "fk_discovery_observation_round_manifest",
            "entry_discovery_rounds",
            ["discovery_round_id", "manifest_version"],
            ["id", "manifest_version"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_discovery_observation_predecessor_identity",
            "entry_discovery_observations",
            ["predecessor_observation_id", "manifest_version", "company_id"],
            ["id", "manifest_version", "company_id"],
            ondelete="RESTRICT",
        )


def _downgrade_observations() -> None:
    copy_from = _observation_table(with_rounds=True) if _is_sqlite_offline() else None
    with op.batch_alter_table(
        "entry_discovery_observations",
        copy_from=copy_from,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_discovery_observation_predecessor_identity", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_discovery_observation_round_manifest", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "uq_discovery_observation_round_company", type_="unique"
        )
        batch_op.drop_constraint("uq_discovery_observation_id_round", type_="unique")
        batch_op.drop_constraint("uq_discovery_observation_identity", type_="unique")
        batch_op.drop_column("predecessor_observation_id")
        batch_op.drop_column("discovery_round_id")


def upgrade() -> None:
    op.create_table(
        "entry_discovery_rounds",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("manifest_version", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("config_fingerprint", sa.String(64), nullable=False),
        sa.Column("model_fingerprint", sa.String(64), nullable=False),
        sa.Column("predecessor_round_id", GUID(), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["manifest_version"],
            ["company_manifests.version"],
            name="fk_discovery_round_manifest_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_round_id", "manifest_version"],
            ["entry_discovery_rounds.id", "entry_discovery_rounds.manifest_version"],
            name="fk_discovery_round_predecessor_manifest",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "manifest_version", name="uq_discovery_round_id_manifest"),
        sa.UniqueConstraint(
            "manifest_version", "name", name="uq_discovery_round_manifest_name"
        ),
    )
    op.create_index(
        "ix_discovery_rounds_manifest_started",
        "entry_discovery_rounds",
        ["manifest_version", "started_at"],
    )

    _upgrade_observations()
    op.create_index(
        "ix_discovery_observations_round_status",
        "entry_discovery_observations",
        ["discovery_round_id", "status"],
    )
    op.create_index(
        "ix_discovery_observations_predecessor",
        "entry_discovery_observations",
        ["predecessor_observation_id"],
    )

    op.create_table(
        "entry_evidence_audit_samples",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("discovery_round_id", GUID(), nullable=False),
        sa.Column("observation_id", GUID(), nullable=False),
        sa.Column("source_id", sa.String(50), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("selected_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discovery_round_id"],
            ["entry_discovery_rounds.id"],
            name="fk_evidence_audit_sample_round_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "discovery_round_id"],
            ["entry_discovery_observations.id", "entry_discovery_observations.discovery_round_id"],
            name="fk_evidence_audit_sample_observation_round",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "discovery_round_id",
            "observation_id",
            name="uq_evidence_audit_sample_observation",
        ),
    )
    op.create_index(
        "ix_evidence_audit_samples_stratum",
        "entry_evidence_audit_samples",
        ["discovery_round_id", "source_id", "platform"],
    )

    op.create_table(
        "entry_evidence_audit_findings",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("audit_sample_id", GUID(), nullable=False),
        sa.Column("severe_error", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("audited_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_sample_id"],
            ["entry_evidence_audit_samples.id"],
            name="fk_evidence_audit_finding_sample_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audit_sample_id", name="uq_evidence_audit_finding_sample"),
    )
    op.create_index(
        "ix_evidence_audit_findings_severe_audited",
        "entry_evidence_audit_findings",
        ["severe_error", "audited_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evidence_audit_findings_severe_audited",
        table_name="entry_evidence_audit_findings",
    )
    op.drop_table("entry_evidence_audit_findings")
    op.drop_index(
        "ix_evidence_audit_samples_stratum",
        table_name="entry_evidence_audit_samples",
    )
    op.drop_table("entry_evidence_audit_samples")

    op.drop_index(
        "ix_discovery_observations_predecessor",
        table_name="entry_discovery_observations",
    )
    op.drop_index(
        "ix_discovery_observations_round_status",
        table_name="entry_discovery_observations",
    )
    _downgrade_observations()

    op.drop_index(
        "ix_discovery_rounds_manifest_started",
        table_name="entry_discovery_rounds",
    )
    op.drop_table("entry_discovery_rounds")

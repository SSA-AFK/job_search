"""Harden entry evidence replay, quarantine, and append-only integrity.

Revision ID: 0011_entry_evidence_integrity
Revises: 0010_entry_evidence_rounds
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID, UTCDateTime

revision: str = "0011_entry_evidence_integrity"
down_revision: str | None = "0010_entry_evidence_rounds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "entry_discovery_rounds",
    "entry_evidence_audit_samples",
    "entry_evidence_audit_findings",
    "entry_evidence_quarantines",
)


def _dialect_name() -> str:
    return op.get_context().dialect.name


def _create_sqlite_guards() -> None:
    for table in _IMMUTABLE_TABLES:
        for action in ("update", "delete"):
            op.execute(
                sa.text(
                    f"CREATE TRIGGER trg_{table}_no_{action} "
                    f"BEFORE {action.upper()} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'entry evidence is append-only'); END"
                )
            )
    for action in ("update", "delete"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_round_observations_no_{action} "
                f"BEFORE {action.upper()} ON entry_discovery_observations "
                "WHEN OLD.discovery_round_id IS NOT NULL BEGIN "
                "SELECT RAISE(ABORT, 'round evidence is append-only'); END"
            )
        )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_manifest_no_round_cascade "
            "BEFORE DELETE ON company_manifests "
            "WHEN EXISTS (SELECT 1 FROM entry_discovery_rounds "
            "WHERE manifest_version = OLD.version) BEGIN "
            "SELECT RAISE(ABORT, 'entry evidence manifest is restricted'); END"
        )
    )


def _drop_sqlite_guards() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_manifest_no_round_cascade"))
    for action in ("update", "delete"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_round_observations_no_{action}"))
    for table in reversed(_IMMUTABLE_TABLES):
        for action in ("update", "delete"):
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action}"))


def _create_postgresql_guards() -> None:
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_entry_evidence_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'entry evidence is append-only'; END; $$"
        )
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION prevent_entry_evidence_mutation()"
            )
        )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_round_observations_immutable "
            "BEFORE UPDATE OR DELETE ON entry_discovery_observations "
            "FOR EACH ROW WHEN (OLD.discovery_round_id IS NOT NULL) "
            "EXECUTE FUNCTION prevent_entry_evidence_mutation()"
        )
    )


def _drop_postgresql_guards() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_round_observations_immutable "
            "ON entry_discovery_observations"
        )
    )
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS prevent_entry_evidence_mutation()"))


def _restrict_postgresql_manifest_fks() -> None:
    op.drop_constraint(
        "fk_discovery_round_manifest_version",
        "entry_discovery_rounds",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_discovery_round_manifest_version",
        "entry_discovery_rounds",
        "company_manifests",
        ["manifest_version"],
        ["version"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "fk_discovery_observations_manifest_version",
        "entry_discovery_observations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_discovery_observations_manifest_version",
        "entry_discovery_observations",
        "company_manifests",
        ["manifest_version"],
        ["version"],
        ondelete="RESTRICT",
    )


def _restore_postgresql_manifest_cascades() -> None:
    for table, constraint in (
        ("entry_discovery_observations", "fk_discovery_observations_manifest_version"),
        ("entry_discovery_rounds", "fk_discovery_round_manifest_version"),
    ):
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(
            constraint,
            table,
            "company_manifests",
            ["manifest_version"],
            ["version"],
            ondelete="CASCADE",
        )


def upgrade() -> None:
    op.add_column(
        "entry_discovery_rounds",
        sa.Column("membership_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "entry_discovery_rounds",
        sa.Column("intended_member_count", sa.Integer(), nullable=True),
    )
    for name, column_type in (
        ("public_evidence", sa.JSON()),
        ("model_assessment", sa.JSON()),
        ("independent_validation", sa.JSON()),
        ("prompt_fingerprint", sa.String(64)),
        ("schema_fingerprint", sa.String(64)),
        ("policy_fingerprint", sa.String(64)),
        ("registry_fingerprint", sa.String(64)),
    ):
        op.add_column(
            "entry_discovery_observations",
            sa.Column(name, column_type, nullable=True),
        )

    op.create_table(
        "entry_evidence_quarantines",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("observation_id", GUID(), nullable=False),
        sa.Column("audit_finding_id", GUID(), nullable=False),
        sa.Column("quarantined_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["entry_discovery_observations.id"],
            name="fk_evidence_quarantine_observation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["audit_finding_id"],
            ["entry_evidence_audit_findings.id"],
            name="fk_evidence_quarantine_finding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("observation_id", name="uq_evidence_quarantine_observation"),
        sa.UniqueConstraint("audit_finding_id", name="uq_evidence_quarantine_finding"),
    )
    op.create_index(
        "ix_evidence_quarantines_time",
        "entry_evidence_quarantines",
        ["quarantined_at"],
    )
    op.execute(
        sa.text(
            "INSERT INTO entry_evidence_quarantines "
            "(id, observation_id, audit_finding_id, quarantined_at) "
            "SELECT f.id, s.observation_id, f.id, f.audited_at "
            "FROM entry_evidence_audit_findings f "
            "JOIN entry_evidence_audit_samples s ON s.id = f.audit_sample_id "
            "WHERE f.severe_error = TRUE"
        )
    )

    dialect = _dialect_name()
    if dialect == "postgresql":
        _restrict_postgresql_manifest_fks()
        _create_postgresql_guards()
    elif dialect == "sqlite":
        _create_sqlite_guards()


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        _drop_postgresql_guards()
        _restore_postgresql_manifest_cascades()
    elif dialect == "sqlite":
        _drop_sqlite_guards()

    op.drop_index("ix_evidence_quarantines_time", table_name="entry_evidence_quarantines")
    op.drop_table("entry_evidence_quarantines")
    for name in (
        "registry_fingerprint",
        "policy_fingerprint",
        "schema_fingerprint",
        "prompt_fingerprint",
        "independent_validation",
        "model_assessment",
        "public_evidence",
    ):
        op.drop_column("entry_discovery_observations", name)
    op.drop_column("entry_discovery_rounds", "intended_member_count")
    op.drop_column("entry_discovery_rounds", "membership_fingerprint")

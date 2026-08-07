"""Persistence models for immutable company identity review audit records."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.company_identity.contracts import IdentityReviewAction, IdentityReviewStatus
from app.models.base import GUID, Base, UTCDateTime, utc_now


def _lowercase_hex_check(column_name: str) -> str:
    remainder = column_name
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column_name}) = 64 AND "
        f"{column_name} = lower({column_name}) AND {remainder} = ''"
    )


def _enum_column(enum_type: type[StrEnum], name: str, *, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda enum: [member.value for member in enum],
        native_enum=False,
        create_constraint=True,
        name=name,
        length=length,
    )


class CompanyIdentityReviewItem(Base):
    __tablename__ = "company_identity_review_items"
    __table_args__ = (
        CheckConstraint(
            _lowercase_hex_check("stable_identity_hash"),
            name="ck_identity_review_item_hash_format",
        ),
        CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR "
            "(status IN ('resolved', 'rejected') AND resolved_at IS NOT NULL)",
            name="ck_identity_review_item_status_resolution",
        ),
        UniqueConstraint(
            "stable_identity_hash",
            name="uq_identity_review_item_stable_hash",
        ),
        Index(
            "ix_company_identity_review_items_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    stable_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_crawl_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "crawl_runs.id",
            name="fk_company_identity_review_items_first_crawl_run_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status: Mapped[IdentityReviewStatus] = mapped_column(
        _enum_column(IdentityReviewStatus, "identity_review_status", length=20),
        default=IdentityReviewStatus.PENDING,
        server_default=IdentityReviewStatus.PENDING.value,
        nullable=False,
    )
    candidate_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    official_website: Mapped[str | None] = mapped_column(String(2000))
    recruitment_identity: Mapped[str | None] = mapped_column(String(255))
    legal_identifiers: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    public_evidence_refs: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    candidate_matches: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False
    )
    review_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CompanyIdentityReviewDecision(Base):
    __tablename__ = "company_identity_review_decisions"
    __table_args__ = (
        CheckConstraint(
            _lowercase_hex_check("decision_hash"),
            name="ck_identity_review_decision_hash_format",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 1 AND 2000",
            name="ck_identity_review_decision_reason_length",
        ),
        CheckConstraint(
            "(action IN ('link_as_alias', 'rename_canonical') "
            "AND target_company_id IS NOT NULL) OR "
            "(action IN ('create_new', 'reject') AND target_company_id IS NULL)",
            name="ck_identity_review_decision_action_target",
        ),
        UniqueConstraint(
            "decision_hash",
            name="uq_identity_review_decision_hash",
        ),
        UniqueConstraint(
            "review_item_id",
            name="uq_identity_review_decision_item",
        ),
    )

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    review_item_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "company_identity_review_items.id",
            name="fk_company_identity_review_decisions_review_item_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    action: Mapped[IdentityReviewAction] = mapped_column(
        _enum_column(IdentityReviewAction, "identity_review_action", length=30),
        nullable=False,
    )
    target_company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "companies.id",
            name="fk_company_identity_review_decisions_target_company_id",
            ondelete="RESTRICT",
        )
    )
    resulting_company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "companies.id",
            name="fk_company_identity_review_decisions_resulting_company_id",
            ondelete="RESTRICT",
        )
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)

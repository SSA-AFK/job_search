import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.company_identity.contracts import IdentityReviewAction, IdentityReviewStatus
from app.company_identity.models import (
    CompanyIdentityReviewDecision,
    CompanyIdentityReviewItem,
)
from app.models import Base, CollectionStatus, Company, CrawlRun, RunType


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def utc(day: int) -> datetime:
    return datetime(2026, 8, day, 12, tzinfo=UTC)


def persisted_crawl_run(session: Session) -> CrawlRun:
    crawl_run = CrawlRun(
        run_type=RunType.DISCOVERY,
        status=CollectionStatus.SUCCEEDED,
        providers_attempted=[],
        created_at=utc(7),
    )
    session.add(crawl_run)
    session.flush()
    return crawl_run


def persisted_review_item(
    session: Session,
    *,
    stable_hash: str,
    crawl_run: CrawlRun | None = None,
    status: IdentityReviewStatus = IdentityReviewStatus.PENDING,
    resolved_at: datetime | None = None,
) -> CompanyIdentityReviewItem:
    item = CompanyIdentityReviewItem(
        stable_identity_hash=stable_hash,
        first_crawl_run_id=(crawl_run or persisted_crawl_run(session)).id,
        status=status,
        candidate_name="Example AI",
        normalized_name="exampleai",
        aliases=["Example Artificial Intelligence"],
        official_website="https://example.com/",
        recruitment_identity="tenant:example",
        legal_identifiers=["cn-123"],
        city="shanghai",
        public_evidence_refs=[
            {
                "provider": "official_site",
                "url": "https://example.com/about",
                "evidence_id": "document-1",
                "confidence": "0.9",
            }
        ],
        candidate_matches=[],
        review_reasons=["fuzzy_name_neighbor"],
        created_at=utc(7),
        resolved_at=resolved_at,
    )
    session.add(item)
    return item


def persisted_decision(
    session: Session,
    *,
    item: CompanyIdentityReviewItem,
    decision_hash: str,
    target_company: Company | None = None,
    resulting_company: Company | None = None,
    reason: str = "Reviewed public evidence.",
    action: IdentityReviewAction | None = None,
) -> CompanyIdentityReviewDecision:
    decision = CompanyIdentityReviewDecision(
        review_item_id=item.id,
        action=action
        or (
            IdentityReviewAction.LINK_AS_ALIAS
            if target_company is not None
            else IdentityReviewAction.REJECT
        ),
        target_company_id=None if target_company is None else target_company.id,
        resulting_company_id=(
            None if resulting_company is None else resulting_company.id
        ),
        reason=reason,
        decided_at=utc(8),
        decision_hash=decision_hash,
    )
    session.add(decision)
    return decision


def persisted_company(session: Session) -> Company:
    company = Company(
        canonical_name="Example AI",
        normalized_name="exampleai",
        funding_stage="unknown",
        scale="unknown",
    )
    session.add(company)
    session.flush()
    return company


def test_app_models_base_lazy_loader_registers_review_tables() -> None:
    script = (
        "from app.models import Base\n"
        "expected = {'company_identity_review_items', "
        "'company_identity_review_decisions'}\n"
        "missing = expected.difference(Base.metadata.tables)\n"
        "assert not missing, sorted(missing)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_review_schema_has_named_checks_constraints_and_status_index() -> None:
    item_table = CompanyIdentityReviewItem.__table__
    decision_table = CompanyIdentityReviewDecision.__table__

    assert item_table.name == "company_identity_review_items"
    assert decision_table.name == "company_identity_review_decisions"
    for column_name in (
        "aliases",
        "legal_identifiers",
        "public_evidence_refs",
        "candidate_matches",
        "review_reasons",
    ):
        assert item_table.c[column_name].nullable is False

    item_checks = {
        constraint.name
        for constraint in item_table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    decision_checks = {
        constraint.name
        for constraint in decision_table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert item_checks >= {
        "identity_review_status",
        "ck_identity_review_item_hash_format",
        "ck_identity_review_item_status_resolution",
    }
    assert decision_checks >= {
        "identity_review_action",
        "ck_identity_review_decision_hash_format",
        "ck_identity_review_decision_reason_length",
        "ck_identity_review_decision_action_target",
    }

    item_uniques = {
        constraint.name
        for constraint in item_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    decision_uniques = {
        constraint.name
        for constraint in decision_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert item_uniques >= {"uq_identity_review_item_stable_hash"}
    assert decision_uniques >= {
        "uq_identity_review_decision_hash",
        "uq_identity_review_decision_item",
    }
    assert {index.name for index in item_table.indexes} >= {
        "ix_company_identity_review_items_status_created"
    }


def test_review_foreign_keys_are_named_and_restrict_deletes() -> None:
    item_foreign_keys = {
        constraint.name: constraint.ondelete
        for constraint in CompanyIdentityReviewItem.__table__.foreign_key_constraints
    }
    decision_foreign_keys = {
        constraint.name: constraint.ondelete
        for constraint in CompanyIdentityReviewDecision.__table__.foreign_key_constraints
    }

    assert item_foreign_keys == {
        "fk_company_identity_review_items_first_crawl_run_id": "RESTRICT"
    }
    assert decision_foreign_keys == {
        "fk_company_identity_review_decisions_review_item_id": "RESTRICT",
        "fk_company_identity_review_decisions_target_company_id": "RESTRICT",
        "fk_company_identity_review_decisions_resulting_company_id": "RESTRICT",
    }


def test_review_hashes_are_unique_and_each_item_has_one_decision(
    session: Session,
) -> None:
    item = persisted_review_item(session, stable_hash="a" * 64)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            persisted_review_item(
                session,
                stable_hash="a" * 64,
                crawl_run=session.get(CrawlRun, item.first_crawl_run_id),
            )
        )
        session.flush()
    assert session.get(CompanyIdentityReviewItem, item.id) is not None

    decision = persisted_decision(session, item=item, decision_hash="b" * 64)
    session.flush()
    other_item = persisted_review_item(session, stable_hash="c" * 64)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_decision(
            session,
            item=other_item,
            decision_hash=decision.decision_hash,
        )
        session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_decision(session, item=item, decision_hash="d" * 64)
        session.flush()
    assert session.get(CompanyIdentityReviewDecision, decision.id) is not None


def test_review_hash_length_checks_reject_malformed_values(session: Session) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_review_item(session, stable_hash="short")
        session.flush()

    item = persisted_review_item(session, stable_hash="a" * 64)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_decision(session, item=item, decision_hash="short")
        session.flush()


@pytest.mark.parametrize("invalid_character", ("A", "g"))
def test_review_hash_checks_require_lowercase_hex(
    session: Session,
    invalid_character: str,
) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_review_item(session, stable_hash=invalid_character * 64)
        session.flush()

    item = persisted_review_item(session, stable_hash="a" * 64)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_decision(
            session,
            item=item,
            decision_hash=invalid_character * 64,
        )
        session.flush()


@pytest.mark.parametrize("reason", ("", "x" * 2001))
def test_decision_reason_rejects_values_outside_contract_bounds(
    session: Session,
    reason: str,
) -> None:
    item = persisted_review_item(session, stable_hash="a" * 64)
    session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_decision(
            session,
            item=item,
            decision_hash="b" * 64,
            reason=reason,
        )
        session.flush()


@pytest.mark.parametrize("reason", ("x", "x" * 2000))
def test_decision_reason_accepts_contract_boundaries(
    session: Session,
    reason: str,
) -> None:
    item = persisted_review_item(session, stable_hash="a" * 64)
    session.flush()

    decision = persisted_decision(
        session,
        item=item,
        decision_hash="b" * 64,
        reason=reason,
    )
    session.flush()

    assert session.get(CompanyIdentityReviewDecision, decision.id) is decision


@pytest.mark.parametrize(
    ("action", "has_target"),
    (
        (IdentityReviewAction.LINK_AS_ALIAS, False),
        (IdentityReviewAction.RENAME_CANONICAL, False),
        (IdentityReviewAction.CREATE_NEW, True),
        (IdentityReviewAction.REJECT, True),
    ),
)
def test_decision_action_rejects_invalid_target_presence(
    session: Session,
    action: IdentityReviewAction,
    has_target: bool,
) -> None:
    item = persisted_review_item(session, stable_hash="a" * 64)
    company = persisted_company(session) if has_target else None
    session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_decision(
            session,
            item=item,
            decision_hash="b" * 64,
            target_company=company,
            action=action,
        )
        session.flush()


@pytest.mark.parametrize(
    ("action", "has_target"),
    (
        (IdentityReviewAction.LINK_AS_ALIAS, True),
        (IdentityReviewAction.RENAME_CANONICAL, True),
        (IdentityReviewAction.CREATE_NEW, False),
        (IdentityReviewAction.REJECT, False),
    ),
)
def test_decision_action_accepts_valid_target_presence(
    session: Session,
    action: IdentityReviewAction,
    has_target: bool,
) -> None:
    item = persisted_review_item(session, stable_hash="a" * 64)
    company = persisted_company(session) if has_target else None
    session.flush()

    decision = persisted_decision(
        session,
        item=item,
        decision_hash="b" * 64,
        target_company=company,
        action=action,
    )
    session.flush()

    assert session.get(CompanyIdentityReviewDecision, decision.id) is decision


@pytest.mark.parametrize(
    ("status", "resolved_at"),
    (
        (IdentityReviewStatus.PENDING, utc(8)),
        (IdentityReviewStatus.RESOLVED, None),
        (IdentityReviewStatus.REJECTED, None),
    ),
)
def test_review_item_rejects_status_resolution_contradictions(
    session: Session,
    status: IdentityReviewStatus,
    resolved_at: datetime | None,
) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        persisted_review_item(
            session,
            stable_hash="a" * 64,
            status=status,
            resolved_at=resolved_at,
        )
        session.flush()


@pytest.mark.parametrize(
    ("status", "resolved_at"),
    (
        (IdentityReviewStatus.PENDING, None),
        (IdentityReviewStatus.RESOLVED, utc(8)),
        (IdentityReviewStatus.REJECTED, utc(8)),
    ),
)
def test_review_item_accepts_consistent_status_resolution(
    session: Session,
    status: IdentityReviewStatus,
    resolved_at: datetime | None,
) -> None:
    item = persisted_review_item(
        session,
        stable_hash="a" * 64,
        status=status,
        resolved_at=resolved_at,
    )
    session.flush()

    assert session.get(CompanyIdentityReviewItem, item.id) is item


def test_audit_foreign_keys_prevent_referenced_row_deletion(session: Session) -> None:
    company = persisted_company(session)
    crawl_run = persisted_crawl_run(session)
    item = persisted_review_item(
        session,
        stable_hash="a" * 64,
        crawl_run=crawl_run,
    )
    session.flush()
    decision = persisted_decision(
        session,
        item=item,
        decision_hash="b" * 64,
        target_company=company,
        resulting_company=company,
    )
    session.flush()

    for referenced in (crawl_run, item, company):
        with pytest.raises(IntegrityError), session.begin_nested():
            session.delete(referenced)
            session.flush()
        assert session.get(type(referenced), referenced.id) is not None
    assert session.get(CompanyIdentityReviewDecision, decision.id) is not None

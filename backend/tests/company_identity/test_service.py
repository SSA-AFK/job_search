import os
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event, Lock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.company_identity import service as identity_service
from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityReviewDraft,
    IdentityReviewAction,
    IdentityReviewDecisionInput,
    IdentityReviewReason,
    IdentityReviewStatus,
    PublicEvidenceReference,
)
from app.company_identity.models import (
    CompanyIdentityReviewDecision,
    CompanyIdentityReviewItem,
)
from app.company_identity.service import (
    IdentityOwnerChanged,
    IdentityReviewConflict,
    apply_identity_review_decisions,
    export_identity_review_queue,
    record_identity_review,
)
from app.models import Base, CollectionStatus, Company, CompanyAlias, CrawlRun, RunType

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)
COMPANY_A = UUID("00000000-0000-0000-0000-000000000001")


def _release_stuck_test_identity_mutex(mutex: Lock) -> None:
    if not mutex.locked():
        return
    try:
        mutex.release()
    except RuntimeError:
        return


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session
    engine.dispose()


def crawl_run_row(session: Session) -> CrawlRun:
    crawl_run = CrawlRun(
        run_type=RunType.DISCOVERY,
        status=CollectionStatus.SUCCEEDED,
        providers_attempted=[],
        created_at=NOW,
    )
    session.add(crawl_run)
    session.commit()
    return crawl_run


def candidate_match(*, score: Decimal = Decimal("91.5")) -> CompanyIdentityCandidateMatch:
    return CompanyIdentityCandidateMatch(
        company_id=COMPANY_A,
        canonical_name="Example Labs",
        normalized_name="examplelabs",
        match_kind="fuzzy_canonical",
        score=score,
        conflict_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
    )


def review_draft(
    *,
    name: str = "Example AI",
    observed_at: datetime = NOW,
) -> CompanyIdentityReviewDraft:
    return CompanyIdentityReviewDraft(
        identity=CompanyIdentityInput(
            canonical_name=name,
            aliases=(f"{name} Holdings",),
            official_website="HTTPS://Example.COM/about?private=drop#fragment",
            recruitment_identity=" Tenant:Example ",
            legal_identifiers=(" CN-123 ",),
            city=" Shanghai ",
            evidence=(
                PublicEvidenceReference(
                    provider=" Official Site ",
                    url="HTTPS://Example.COM/about?token=drop#fragment",
                    evidence_id=" public-document-1 ",
                    confidence=Decimal("0.90"),
                ),
            ),
        ),
        candidate_matches=(candidate_match(),),
        review_reasons=(IdentityReviewReason.FUZZY_NAME_NEIGHBOR,),
        observed_at=observed_at,
    )


def company_row(
    session: Session,
    canonical_name: str,
    *,
    website: str | None = None,
) -> Company:
    company = Company(
        canonical_name=canonical_name,
        normalized_name="".join(canonical_name.casefold().split()),
        website=website,
        funding_stage="unknown",
        scale="unknown",
    )
    session.add(company)
    session.commit()
    return company


def pending_review(
    session: Session,
    *,
    candidate_name: str,
    identity: CompanyIdentityInput | None = None,
) -> UUID:
    crawl_run = crawl_run_row(session)
    draft = review_draft(name=candidate_name)
    if identity is not None:
        draft = draft.model_copy(update={"identity": identity})
    return record_identity_review(
        session,
        crawl_run_id=crawl_run.id,
        draft=draft,
    ).review_item_id


def decision(
    review_item_id: UUID,
    *,
    action: IdentityReviewAction,
    target_company_id: UUID | None = None,
    reason: str = "Reviewed public evidence.",
    decided_at: datetime = NOW,
) -> IdentityReviewDecisionInput:
    return IdentityReviewDecisionInput(
        review_item_id=review_item_id,
        action=action,
        target_company_id=target_company_id,
        reason=reason,
        decided_at=decided_at,
    )


def alias_owner(session: Session, normalized_alias: str) -> UUID | None:
    return session.scalar(
        select(CompanyAlias.company_id).where(
            CompanyAlias.normalized_alias == normalized_alias
        )
    )


def test_review_record_is_exact_replay_idempotent(session: Session) -> None:
    crawl_run = crawl_run_row(session)

    first = record_identity_review(session, crawl_run_id=crawl_run.id, draft=review_draft())
    second = record_identity_review(session, crawl_run_id=crawl_run.id, draft=review_draft())

    assert first.review_item_id == second.review_item_id
    assert first.created is True
    assert second.created is False
    assert session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem)) == 1


def test_review_replay_from_another_run_preserves_first_run_and_content(
    session: Session,
) -> None:
    first_run = crawl_run_row(session)
    second_run = crawl_run_row(session)
    draft = review_draft()

    first = record_identity_review(session, crawl_run_id=first_run.id, draft=draft)
    second = record_identity_review(session, crawl_run_id=second_run.id, draft=draft)
    stored = session.get(CompanyIdentityReviewItem, first.review_item_id)

    assert second.review_item_id == first.review_item_id
    assert second.created is False
    assert stored is not None
    assert stored.first_crawl_run_id == first_run.id
    assert stored.candidate_name == "Example AI"
    assert stored.candidate_matches[0]["score"] == "91.5"


def test_review_record_rejects_dirty_session_without_mutation(session: Session) -> None:
    crawl_run = crawl_run_row(session)
    session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem))

    with pytest.raises(IdentityReviewConflict) as raised:
        record_identity_review(session, crawl_run_id=crawl_run.id, draft=review_draft())

    assert raised.value.code == "identity_review_conflict"
    assert str(raised.value) == "identity review conflict"
    session.rollback()
    assert session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem)) == 0


def test_review_record_revalidates_constructed_candidate_snapshot(session: Session) -> None:
    crawl_run = crawl_run_row(session)
    malformed_match = CompanyIdentityCandidateMatch.model_construct(
        company_id=COMPANY_A,
        canonical_name="Example Labs",
        normalized_name="different-owner",
        match_kind="fuzzy_canonical",
        score=Decimal("91.5"),
        conflict_reasons=(),
    )
    malformed = review_draft().model_copy(update={"candidate_matches": (malformed_match,)})

    with pytest.raises(IdentityReviewConflict) as raised:
        record_identity_review(session, crawl_run_id=crawl_run.id, draft=malformed)

    assert raised.value.code == "identity_review_conflict"
    assert "different-owner" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem)) == 0


def test_review_record_revalidates_bounded_candidate_snapshot(session: Session) -> None:
    crawl_run = crawl_run_row(session)
    matches = tuple(
        candidate_match().model_copy(update={"company_id": UUID(int=index + 1)})
        for index in range(21)
    )
    malformed = review_draft().model_copy(update={"candidate_matches": matches})

    with pytest.raises(IdentityReviewConflict):
        record_identity_review(session, crawl_run_id=crawl_run.id, draft=malformed)

    assert session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem)) == 0


def test_review_record_revalidates_and_sanitizes_public_evidence(session: Session) -> None:
    crawl_run = crawl_run_row(session)
    unsafe_reference = PublicEvidenceReference.model_construct(
        provider="official_site",
        url="https://user:secret@example.com/private?token=hostile",
        evidence_id="raw-document-secret",
        confidence=Decimal("0.9"),
    )
    identity = review_draft().identity.model_copy(update={"evidence": (unsafe_reference,)})
    malformed = review_draft().model_copy(update={"identity": identity})

    with pytest.raises(IdentityReviewConflict) as raised:
        record_identity_review(session, crawl_run_id=crawl_run.id, draft=malformed)

    assert raised.value.code == "identity_review_conflict"
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_review_record_stores_only_normalized_public_snapshot_fields(session: Session) -> None:
    crawl_run = crawl_run_row(session)

    summary = record_identity_review(
        session,
        crawl_run_id=crawl_run.id,
        draft=review_draft(),
    )
    stored = session.get(CompanyIdentityReviewItem, summary.review_item_id)

    assert stored is not None
    assert stored.official_website == "https://example.com/about"
    assert stored.public_evidence_refs == [
        {
            "provider": "officialsite",
            "url": "https://example.com/about",
            "evidence_id": "public-document-1",
            "confidence": "0.9",
        }
    ]
    assert set(stored.public_evidence_refs[0]) == {
        "provider",
        "url",
        "evidence_id",
        "confidence",
    }


def test_stable_hash_replay_preserves_first_volatile_review_snapshot(
    session: Session,
) -> None:
    first_run = crawl_run_row(session)
    replay_run = crawl_run_row(session)
    replay_observed_at = datetime.now(UTC)
    first_observed_at = replay_observed_at - timedelta(days=1)
    original = review_draft(observed_at=first_observed_at)
    replay = original.model_copy(
        update={
            "candidate_matches": (candidate_match(score=Decimal("84.25")),),
            "review_reasons": (IdentityReviewReason.SHORT_NAME_COLLISION,),
            "observed_at": replay_observed_at,
        }
    )
    assert replay.stable_identity_hash == original.stable_identity_hash

    first = record_identity_review(
        session,
        crawl_run_id=first_run.id,
        draft=original,
    )
    second = record_identity_review(
        session,
        crawl_run_id=replay_run.id,
        draft=replay,
    )
    stored = session.get(CompanyIdentityReviewItem, first.review_item_id)

    assert second.review_item_id == first.review_item_id
    assert second.created is False
    assert stored is not None
    assert stored.first_crawl_run_id == first_run.id
    assert stored.created_at == first_observed_at
    assert stored.candidate_matches[0]["score"] == "91.5"
    assert stored.review_reasons == [IdentityReviewReason.FUZZY_NAME_NEIGHBOR.value]


def test_changed_stable_evidence_records_a_distinct_review_item(
    session: Session,
) -> None:
    crawl_run = crawl_run_row(session)
    original = review_draft()
    changed_reference = PublicEvidenceReference(
        provider="official_site",
        url="https://example.com/different-evidence",
        evidence_id="public-document-2",
        confidence=Decimal("0.90"),
    )
    changed_identity = original.identity.model_copy(
        update={"evidence": (changed_reference,)}
    )
    changed = original.model_copy(update={"identity": changed_identity})
    assert changed.stable_identity_hash != original.stable_identity_hash

    first = record_identity_review(
        session,
        crawl_run_id=crawl_run.id,
        draft=original,
    )
    second = record_identity_review(
        session,
        crawl_run_id=crawl_run.id,
        draft=changed,
    )

    assert second.review_item_id != first.review_item_id
    assert second.created is True
    assert session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem)) == 2


def test_review_queue_export_is_pending_only_and_deterministic(session: Session) -> None:
    crawl_run = crawl_run_row(session)
    first = record_identity_review(
        session,
        crawl_run_id=crawl_run.id,
        draft=review_draft(name="Zulu AI"),
    )
    second = record_identity_review(
        session,
        crawl_run_id=crawl_run.id,
        draft=review_draft(name="Alpha AI"),
    )
    with session.begin():
        rows = tuple(session.scalars(select(CompanyIdentityReviewItem)))
        for row in rows:
            row.created_at = NOW

    exported = export_identity_review_queue(session)

    assert session.in_transaction() is False
    expected_ids = tuple(sorted((first.review_item_id, second.review_item_id), key=str))
    assert tuple(item.review_item_id for item in exported) == expected_ids
    assert tuple(item.draft.identity.canonical_name for item in exported) == tuple(
        "Zulu AI" if item_id == first.review_item_id else "Alpha AI"
        for item_id in expected_ids
    )
    assert all(item.draft.observed_at == NOW for item in exported)


def test_review_queue_export_rejects_dirty_session(session: Session) -> None:
    session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem))

    with pytest.raises(IdentityReviewConflict):
        export_identity_review_queue(session)

    session.rollback()


def test_contract_still_rejects_invalid_public_reference_directly() -> None:
    with pytest.raises(ValidationError):
        PublicEvidenceReference(
            provider="official_site",
            url="https://user:secret@example.com/private",
            evidence_id="public-document-1",
            confidence=Decimal("0.9"),
        )


def test_link_as_alias_keeps_canonical_name(session: Session) -> None:
    company = company_row(session, "OpenAI")
    item_id = pending_review(session, candidate_name="OpenAI China")

    summary = apply_identity_review_decisions(
        session,
        (
            decision(
                item_id,
                action=IdentityReviewAction.LINK_AS_ALIAS,
                target_company_id=company.id,
            ),
        ),
    )
    session.refresh(company)

    assert summary.applied == 1
    assert summary.replayed == 0
    assert company.canonical_name == "OpenAI"
    assert alias_owner(session, "openaichina") == company.id


def test_create_new_uses_stable_company_identity_and_all_aliases(session: Session) -> None:
    item_id = pending_review(session, candidate_name="New Company")

    summary = apply_identity_review_decisions(
        session,
        (decision(item_id, action=IdentityReviewAction.CREATE_NEW),),
    )
    item = session.get(CompanyIdentityReviewItem, item_id)
    created = session.scalar(select(Company).where(Company.normalized_name == "newcompany"))

    assert summary.applied == 1
    assert item is not None
    assert item.status is IdentityReviewStatus.RESOLVED
    assert item.resolved_at == NOW
    assert created is not None
    assert created.canonical_name == "New Company"
    assert created.website == "https://example.com/about"
    assert alias_owner(session, "newcompanyholdings") == created.id
    audit = session.scalar(
        select(CompanyIdentityReviewDecision).where(
            CompanyIdentityReviewDecision.review_item_id == item_id
        )
    )
    assert audit is not None
    assert audit.resulting_company_id == created.id


def test_rename_canonical_preserves_old_name_as_alias(session: Session) -> None:
    company = company_row(session, "Old Name")
    item_id = pending_review(session, candidate_name="New Name")

    apply_identity_review_decisions(
        session,
        (
            decision(
                item_id,
                action=IdentityReviewAction.RENAME_CANONICAL,
                target_company_id=company.id,
            ),
        ),
    )
    session.refresh(company)

    assert company.canonical_name == "New Name"
    assert company.normalized_name == "newname"
    assert alias_owner(session, "oldname") == company.id


def test_rename_reuses_same_company_new_name_alias(session: Session) -> None:
    company = company_row(session, "Old Name")
    session.add(
        CompanyAlias(
            company_id=company.id,
            alias="New Name",
            normalized_alias="newname",
        )
    )
    session.commit()
    item_id = pending_review(session, candidate_name="New Name")

    apply_identity_review_decisions(
        session,
        (
            decision(
                item_id,
                action=IdentityReviewAction.RENAME_CANONICAL,
                target_company_id=company.id,
            ),
        ),
    )

    assert alias_owner(session, "oldname") == company.id
    assert alias_owner(session, "newname") is None


def test_reject_records_append_only_audit_without_company_mutation(session: Session) -> None:
    item_id = pending_review(session, candidate_name="Rejected Candidate")

    summary = apply_identity_review_decisions(
        session,
        (decision(item_id, action=IdentityReviewAction.REJECT),),
    )
    item = session.get(CompanyIdentityReviewItem, item_id)
    audit = session.scalar(
        select(CompanyIdentityReviewDecision).where(
            CompanyIdentityReviewDecision.review_item_id == item_id
        )
    )

    assert summary.applied == 1
    assert item is not None
    assert item.status is IdentityReviewStatus.REJECTED
    assert item.resolved_at == NOW
    assert audit is not None
    assert audit.resulting_company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_decision_exact_replay_is_idempotent_and_audit_is_append_only(
    session: Session,
) -> None:
    item_id = pending_review(session, candidate_name="Rejected Candidate")
    command = decision(item_id, action=IdentityReviewAction.REJECT)

    first = apply_identity_review_decisions(session, (command,))
    second = apply_identity_review_decisions(session, (command,))

    assert first.applied == 1
    assert first.replayed == 0
    assert second.applied == 0
    assert second.replayed == 1
    assert session.scalar(
        select(func.count()).select_from(CompanyIdentityReviewDecision)
    ) == 1


def test_different_decision_replay_conflicts_without_reason_echo(session: Session) -> None:
    item_id = pending_review(session, candidate_name="Rejected Candidate")
    apply_identity_review_decisions(
        session,
        (decision(item_id, action=IdentityReviewAction.REJECT),),
    )

    with pytest.raises(IdentityReviewConflict) as raised:
        apply_identity_review_decisions(
            session,
            (
                decision(
                    item_id,
                    action=IdentityReviewAction.REJECT,
                    reason="Sensitive Acquisition Secret",
                ),
            ),
        )

    assert raised.value.code == "identity_review_conflict"
    assert str(raised.value) == "identity review conflict"
    assert "Sensitive" not in str(raised.value)


def test_link_rejects_global_cross_table_owner_conflict(session: Session) -> None:
    target = company_row(session, "Target Company")
    owner = company_row(session, "Taken Name")
    item_id = pending_review(session, candidate_name="Taken Name")

    with pytest.raises(IdentityOwnerChanged) as raised:
        apply_identity_review_decisions(
            session,
            (
                decision(
                    item_id,
                    action=IdentityReviewAction.LINK_AS_ALIAS,
                    target_company_id=target.id,
                ),
            ),
        )

    assert raised.value.code == "identity_owner_changed"
    assert str(raised.value) == "identity owner changed"
    assert owner.canonical_name == "Taken Name"
    assert alias_owner(session, "takenname") is None


def test_decision_rechecks_normalized_website_owner(session: Session) -> None:
    target = company_row(session, "Target Company")
    company_row(session, "Website Owner", website="https://example.com/about")
    item_id = pending_review(session, candidate_name="Candidate")

    with pytest.raises(IdentityOwnerChanged):
        apply_identity_review_decisions(
            session,
            (
                decision(
                    item_id,
                    action=IdentityReviewAction.LINK_AS_ALIAS,
                    target_company_id=target.id,
                ),
            ),
        )


def test_decision_rechecks_name_owner_after_queue_export(session: Session) -> None:
    target = company_row(session, "Target Company")
    target_id = target.id
    item_id = pending_review(session, candidate_name="Late Owner")
    exported = export_identity_review_queue(session)
    assert exported[0].review_item_id == item_id
    company_row(session, "Late Owner")

    with pytest.raises(IdentityOwnerChanged):
        apply_identity_review_decisions(
            session,
            (
                decision(
                    item_id,
                    action=IdentityReviewAction.LINK_AS_ALIAS,
                    target_company_id=target_id,
                ),
            ),
        )


def test_decision_batch_rolls_back_every_partial_company_and_alias_change(
    session: Session,
) -> None:
    first_target = company_row(
        session,
        "Original First",
        website="https://old.example/",
    )
    first_target.city = "old-city"
    session.commit()
    second_target = company_row(session, "Second Target")
    first_target_id = first_target.id
    second_target_id = second_target.id
    first_item_id = pending_review(
        session,
        candidate_name="Shared Identity",
        identity=CompanyIdentityInput(
            canonical_name="Shared Identity",
            official_website="https://new.example/",
            city="new-city",
        ),
    )
    second_item_id = pending_review(
        session,
        candidate_name="Shared Identity",
        identity=CompanyIdentityInput(
            canonical_name="Shared Identity",
            official_website="https://second.example/",
            city="second-city",
        ),
    )
    statements: list[str] = []
    engine = session.get_bind()

    def capture_mutation(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.upper().split()))

    event.listen(engine, "after_cursor_execute", capture_mutation)
    try:
        with pytest.raises(IdentityOwnerChanged):
            apply_identity_review_decisions(
                session,
                (
                    decision(
                        first_item_id,
                        action=IdentityReviewAction.RENAME_CANONICAL,
                        target_company_id=first_target_id,
                    ),
                    decision(
                        second_item_id,
                        action=IdentityReviewAction.LINK_AS_ALIAS,
                        target_company_id=second_target_id,
                    ),
                ),
            )
    finally:
        event.remove(engine, "after_cursor_execute", capture_mutation)

    company_update_index = next(
        index for index, sql in enumerate(statements) if sql.startswith("UPDATE COMPANIES")
    )
    assert any(
        sql.startswith("SELECT COMPANIES.ID")
        and "COMPANIES.NORMALIZED_NAME IN" in sql
        for sql in statements[company_update_index + 1 :]
    )
    assert any("INSERT INTO COMPANY_ALIASES" in sql for sql in statements)
    assert any("UPDATE COMPANY_IDENTITY_REVIEW_ITEMS" in sql for sql in statements)
    assert any("INSERT INTO COMPANY_IDENTITY_REVIEW_DECISIONS" in sql for sql in statements)

    companies = {
        company_id: (
            canonical_name,
            normalized_name,
            website,
            normalized_website,
            city,
        )
        for (
            company_id,
            canonical_name,
            normalized_name,
            website,
            normalized_website,
            city,
        ) in session.execute(
            select(
                Company.id,
                Company.canonical_name,
                Company.normalized_name,
                Company.website,
                Company.normalized_website,
                Company.city,
            ).where(
                Company.id.in_((first_target_id, second_target_id))
            )
        )
    }
    assert companies == {
        first_target_id: (
            "Original First",
            "originalfirst",
            "https://old.example/",
            "https://old.example/",
            "old-city",
        ),
        second_target_id: ("Second Target", "secondtarget", None, "", None),
    }
    assert tuple(
        session.scalars(
            select(CompanyAlias).where(
                CompanyAlias.normalized_alias.in_(("originalfirst", "sharedidentity"))
            )
        )
    ) == ()
    review_states = {
        item_id: (status, resolved_at)
        for item_id, status, resolved_at in session.execute(
            select(
                CompanyIdentityReviewItem.id,
                CompanyIdentityReviewItem.status,
                CompanyIdentityReviewItem.resolved_at,
            ).where(
                CompanyIdentityReviewItem.id.in_((first_item_id, second_item_id))
            )
        )
    }
    assert review_states == {
        first_item_id: (IdentityReviewStatus.PENDING, None),
        second_item_id: (IdentityReviewStatus.PENDING, None),
    }
    decision_rows = tuple(
        session.execute(
            select(
                CompanyIdentityReviewDecision.review_item_id,
                CompanyIdentityReviewDecision.decision_hash,
                CompanyIdentityReviewDecision.resulting_company_id,
            )
        )
    )
    assert decision_rows == ()
    resulting_name_owners = set(
        session.scalars(
            select(Company.id).where(Company.normalized_name == "sharedidentity")
        )
    )
    resulting_name_owners.update(
        session.scalars(
            select(CompanyAlias.company_id).where(
                CompanyAlias.normalized_alias == "sharedidentity"
            )
        )
    )
    assert resulting_name_owners == set()


def test_decision_revalidates_constructed_input_and_rejects_dirty_session(
    session: Session,
) -> None:
    item_id = pending_review(session, candidate_name="Rejected Candidate")
    malformed = IdentityReviewDecisionInput.model_construct(
        review_item_id=item_id,
        action=IdentityReviewAction.LINK_AS_ALIAS,
        target_company_id=None,
        reason="Sensitive Acquisition Secret",
        decided_at=NOW,
    )

    with pytest.raises(IdentityReviewConflict) as malformed_error:
        apply_identity_review_decisions(session, (malformed,))

    assert malformed_error.value.code == "identity_review_conflict"
    assert "Sensitive" not in str(malformed_error.value)
    assert malformed_error.value.__cause__ is None
    session.scalar(select(func.count()).select_from(CompanyIdentityReviewItem))
    with pytest.raises(IdentityReviewConflict):
        apply_identity_review_decisions(
            session,
            (decision(item_id, action=IdentityReviewAction.REJECT),),
        )
    session.rollback()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("official_website", f"https://example.com/{'x' * 1_050}"),
        ("city", "x" * 51),
    ),
)
@pytest.mark.parametrize(
    "action",
    (IdentityReviewAction.CREATE_NEW, IdentityReviewAction.RENAME_CANONICAL),
)
def test_company_write_bounds_fail_with_sanitized_conflict_and_no_partial_state(
    session: Session,
    field: str,
    value: str,
    action: IdentityReviewAction,
) -> None:
    identity_values: dict[str, object] = {
        "canonical_name": "Bounded Candidate",
        "aliases": ("Bounded Alias",),
        "official_website": None,
        "city": None,
    }
    identity_values[field] = value
    identity = CompanyIdentityInput.model_validate(identity_values)
    target = (
        company_row(session, "Original Target")
        if action is IdentityReviewAction.RENAME_CANONICAL
        else None
    )
    target_id = None if target is None else target.id
    item_id = pending_review(
        session,
        candidate_name=identity.canonical_name,
        identity=identity,
    )

    with pytest.raises(IdentityReviewConflict) as raised:
        apply_identity_review_decisions(
            session,
            (
                decision(
                    item_id,
                    action=action,
                    target_company_id=target_id,
                ),
            ),
        )

    assert raised.value.code == "identity_review_conflict"
    assert str(raised.value) == "identity review conflict"
    assert raised.value.__cause__ is None
    assert session.scalar(
        select(func.count()).select_from(CompanyIdentityReviewDecision)
    ) == 0
    assert alias_owner(session, "boundedalias") is None
    written = session.scalar(
        select(Company).where(Company.normalized_name == "boundedcandidate")
    )
    assert written is None
    if target is not None:
        session.refresh(target)
        assert target.canonical_name == "Original Target"


def test_sqlite_distinct_items_serialize_absent_cross_table_name_owner(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'identity-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as setup:
            target = company_row(setup, "Existing Target")
            first_identity = CompanyIdentityInput(
                canonical_name="Shared Identity",
                city="first",
            )
            second_identity = CompanyIdentityInput(
                canonical_name="Shared Identity",
                city="second",
            )
            first_item = pending_review(
                setup,
                candidate_name="Shared Identity",
                identity=first_identity,
            )
            second_item = pending_review(
                setup,
                candidate_name="Shared Identity",
                identity=second_identity,
            )
            commands = (
                decision(first_item, action=IdentityReviewAction.CREATE_NEW),
                decision(
                    second_item,
                    action=IdentityReviewAction.LINK_AS_ALIAS,
                    target_company_id=target.id,
                ),
            )

        started = (Event(), Event())
        finished = (Event(), Event())

        def apply(index: int) -> str:
            started[index].set()
            try:
                with Session(engine) as worker:
                    apply_identity_review_decisions(worker, (commands[index],))
            except IdentityOwnerChanged as error:
                return error.code
            except IdentityReviewConflict as error:
                return error.code
            finally:
                finished[index].set()
            return "applied"

        with Session(engine) as locker, ThreadPoolExecutor(max_workers=2) as pool:
            with identity_service._serialized_identity_keys(
                locker,
                (b"name\0sharedidentity",),
            ):
                futures = tuple(pool.submit(apply, index) for index in range(2))
                assert all(marker.wait(timeout=5) for marker in started)
                assert not any(marker.wait(timeout=0.2) for marker in finished)
            results = tuple(future.result(timeout=10) for future in futures)

        assert sorted(results) == ["applied", "identity_owner_changed"]
        with Session(engine) as verification:
            owner_ids = set(
                verification.scalars(
                    select(Company.id).where(Company.normalized_name == "sharedidentity")
                )
            )
            owner_ids.update(
                verification.scalars(
                    select(CompanyAlias.company_id).where(
                        CompanyAlias.normalized_alias == "sharedidentity"
                    )
                )
            )
            assert len(owner_ids) == 1
            assert verification.scalar(
                select(func.count()).select_from(CompanyIdentityReviewDecision)
            ) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_identity_lock_keys_are_domain_separated_deduplicated_and_key_ordered() -> None:
    material = (
        b"website\0https://example.com/",
        b"name\0sharedidentity",
        b"name\0sharedidentity",
    )

    keys = identity_service._identity_lock_keys(material)

    assert keys == (-6410938119080746435, -1983210520360554722)


@pytest.mark.parametrize("root_a_outcome", ["commit", "rollback"])
def test_sqlite_root_identity_mutex_prevents_opposite_order_deadlock(
    tmp_path,
    root_a_outcome: str,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'root-identity-{root_a_outcome}.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    identity_mutex = Lock()
    with identity_service._LOCAL_LOCKS_GUARD:  # type: ignore[attr-defined]
        identity_service._LOCAL_IDENTITY_MUTEXES[engine] = (  # type: ignore[attr-defined]
            identity_mutex
        )
    root_a_first_acquired = Event()
    root_a_repeat_requested = Event()
    root_a_repeated_call_completed = Event()
    root_a_completion_requested = Event()
    root_b_attempting = Event()
    root_b_first_acquired = Event()
    fallback_release = Event()
    root_b_should_repeat = Event()
    root_b_repeated_call_completed = Event()

    def run_root_a() -> None:
        with Session(engine) as root_a:
            transaction = root_a.begin()
            try:
                with identity_service.serialized_company_identities(
                    root_a,
                    ("zulu identity",),
                ):
                    root_a_first_acquired.set()
                if not root_a_repeat_requested.wait(timeout=15):
                    raise TimeoutError("root A repeat was not requested")
                with identity_service.serialized_company_identities(
                    root_a,
                    ("alpha identity",),
                ):
                    root_a_repeated_call_completed.set()
                if not root_a_completion_requested.wait(timeout=15):
                    raise TimeoutError("root A completion was not requested")
                if root_a_outcome == "commit":
                    transaction.commit()
                else:
                    transaction.rollback()
            finally:
                if root_a.in_transaction():
                    root_a.rollback()

    def run_root_b() -> None:
        with Session(engine) as root_b:
            transaction = root_b.begin()
            try:
                root_b_attempting.set()
                with identity_service.serialized_company_identities(
                    root_b,
                    ("alpha identity",),
                ):
                    root_b_first_acquired.set()
                    if not fallback_release.wait(timeout=15):
                        raise TimeoutError("root B continuation was not released")
                if root_b_should_repeat.is_set():
                    with identity_service.serialized_company_identities(
                        root_b,
                        ("zulu identity",),
                    ):
                        root_b_repeated_call_completed.set()
                transaction.rollback()
            finally:
                if root_b.in_transaction():
                    root_b.rollback()

    pool = ThreadPoolExecutor(max_workers=2)
    root_a_future = pool.submit(run_root_a)
    root_b_future = None
    try:
        assert root_a_first_acquired.wait(timeout=5)
        root_b_future = pool.submit(run_root_b)
        assert root_b_attempting.wait(timeout=5)
        assert not root_b_first_acquired.wait(timeout=0.2)
        root_a_repeat_requested.set()
        assert root_a_repeated_call_completed.wait(timeout=5)
        root_a_completion_requested.set()
        root_a_future.result(timeout=15)
        assert root_b_first_acquired.wait(timeout=5)
        root_b_should_repeat.set()
        fallback_release.set()
        assert root_b_repeated_call_completed.wait(timeout=5)
        root_b_future.result(timeout=15)
    finally:
        fallback_release.set()
        root_a_repeat_requested.set()
        root_a_completion_requested.set()
        try:
            try:
                for future in (root_a_future, root_b_future):
                    if future is not None:
                        future.exception(timeout=15)
            except TimeoutError:
                _release_stuck_test_identity_mutex(identity_mutex)
                for future in (root_a_future, root_b_future):
                    if future is not None:
                        future.exception(timeout=15)
        finally:
            _release_stuck_test_identity_mutex(identity_mutex)
            pool.shutdown(wait=False, cancel_futures=True)
            engine.dispose()


def test_sqlite_nested_end_keeps_root_mutex_and_reused_session_competes_fresh(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'root-identity-lifetime.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    identity_mutex = Lock()
    with identity_service._LOCAL_LOCKS_GUARD:  # type: ignore[attr-defined]
        identity_service._LOCAL_IDENTITY_MUTEXES[engine] = (  # type: ignore[attr-defined]
            identity_mutex
        )
    nested_ended = Event()
    root_b_attempting = Event()
    root_b_acquired = Event()
    root_b_release = Event()
    root_a_first_completion_requested = Event()
    root_a_second_attempt_requested = Event()
    root_a_second_attempting = Event()
    root_a_second_acquired = Event()

    def run_reused_root_a() -> None:
        with Session(engine) as root_a:
            first_transaction = root_a.begin()
            try:
                with identity_service.serialized_company_identities(
                    root_a,
                    ("shared identity",),
                ):
                    pass
                nested_transaction = root_a.begin_nested()
                with identity_service.serialized_company_identities(
                    root_a,
                    ("nested identity",),
                ):
                    pass
                nested_transaction.commit()
                nested_ended.set()
                if not root_a_first_completion_requested.wait(timeout=15):
                    raise TimeoutError("root A first completion was not requested")
                first_transaction.commit()
            finally:
                if root_a.in_transaction():
                    root_a.rollback()

            if not root_a_second_attempt_requested.wait(timeout=15):
                raise TimeoutError("root A second attempt was not requested")
            second_transaction = root_a.begin()
            try:
                root_a_second_attempting.set()
                with identity_service.serialized_company_identities(
                    root_a,
                    ("shared identity",),
                ):
                    root_a_second_acquired.set()
                second_transaction.rollback()
            finally:
                if root_a.in_transaction():
                    root_a.rollback()

    def run_root_b() -> None:
        with Session(engine) as root_b:
            transaction = root_b.begin()
            try:
                root_b_attempting.set()
                with identity_service.serialized_company_identities(
                    root_b,
                    ("shared identity",),
                ):
                    root_b_acquired.set()
                    if not root_b_release.wait(timeout=15):
                        raise TimeoutError("root B release was not requested")
                transaction.rollback()
            finally:
                if root_b.in_transaction():
                    root_b.rollback()

    pool = ThreadPoolExecutor(max_workers=2)
    root_a_future = pool.submit(run_reused_root_a)
    root_b_future = None
    try:
        assert nested_ended.wait(timeout=5)
        root_b_future = pool.submit(run_root_b)
        assert root_b_attempting.wait(timeout=5)
        assert not root_b_acquired.wait(timeout=0.2)
        root_a_first_completion_requested.set()
        assert root_b_acquired.wait(timeout=5)
        root_a_second_attempt_requested.set()
        assert root_a_second_attempting.wait(timeout=5)
        assert not root_a_second_acquired.wait(timeout=0.2)
        root_b_release.set()
        assert root_a_second_acquired.wait(timeout=5)
        root_a_future.result(timeout=15)
        root_b_future.result(timeout=15)
    finally:
        root_a_first_completion_requested.set()
        root_b_release.set()
        try:
            if root_b_future is not None:
                try:
                    root_b_future.exception(timeout=15)
                except TimeoutError:
                    _release_stuck_test_identity_mutex(identity_mutex)
                    root_b_future.exception(timeout=15)
        finally:
            root_a_second_attempt_requested.set()
            try:
                try:
                    root_a_future.exception(timeout=15)
                except TimeoutError:
                    _release_stuck_test_identity_mutex(identity_mutex)
                    root_a_future.exception(timeout=15)
            finally:
                _release_stuck_test_identity_mutex(identity_mutex)
                pool.shutdown(wait=False, cancel_futures=True)
                engine.dispose()


@pytest.mark.parametrize("failure_stage", ["listener", "ownership"])
def test_sqlite_root_identity_mutex_releases_when_ownership_setup_fails(
    tmp_path,
    monkeypatch,
    failure_stage: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'root-identity-failure.sqlite3'}")
    identity_mutex = Lock()
    with identity_service._LOCAL_LOCKS_GUARD:  # type: ignore[attr-defined]
        identity_service._LOCAL_IDENTITY_MUTEXES[engine] = (  # type: ignore[attr-defined]
            identity_mutex
        )

    class RejectOwnership(dict[object, object]):
        def __setitem__(self, key: object, value: object) -> None:
            raise RuntimeError("ownership setup failed")

    def reject_listener(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("listener setup failed")

    try:
        with Session(engine) as database_session:
            transaction = database_session.begin()
            if failure_stage == "listener":
                monkeypatch.setattr(identity_service.event, "listen", reject_listener)
                expected_message = "listener setup failed"
            else:
                state = identity_service._LocalTransactionLocks()  # type: ignore[attr-defined]
                state.by_transaction = RejectOwnership()  # type: ignore[assignment]
                database_session.info[
                    identity_service._LOCAL_TRANSACTION_LOCKS_INFO_KEY  # type: ignore[attr-defined]
                ] = state
                expected_message = "ownership setup failed"

            with (
                pytest.raises(RuntimeError, match=expected_message),
                identity_service.serialized_company_identities(
                    database_session,
                    ("failing identity",),
                ),
            ):
                pass

            assert not identity_mutex.locked()
            transaction.rollback()
    finally:
        _release_stuck_test_identity_mutex(identity_mutex)
        engine.dispose()


def test_company_identity_locks_use_shared_name_and_website_namespace_order() -> None:
    class PostgreSQLSession:
        class Bind:
            class Dialect:
                name = "postgresql"

            dialect = Dialect()

        bind = Bind()

        def __init__(self) -> None:
            self.events: list[tuple[str, int] | str] = []

        def get_bind(self) -> Bind:
            return self.bind

        def execute(
            self, statement: object, parameters: dict[str, int]
        ) -> None:
            assert "pg_advisory_xact_lock" in str(statement)
            self.events.append(("lock", parameters["lock_key"]))

    legacy_session = PostgreSQLSession()

    with identity_service.serialized_company_identity_names(  # type: ignore[attr-defined]
        legacy_session,  # type: ignore[arg-type]
        ("sharedidentity", "sharedidentity"),
        official_website="https://example.com/",
    ):
        legacy_session.events.append("body")

    assert legacy_session.events == [
        ("lock", -6410938119080746435),
        ("lock", -1983210520360554722),
        "body",
    ]

    batch_session = PostgreSQLSession()
    with identity_service.serialized_company_identities(  # type: ignore[attr-defined]
        batch_session,  # type: ignore[arg-type]
        ("sharedidentity", "aliasidentity", "sharedidentity"),
        official_websites=(
            "https://other.example/",
            "https://example.com/",
            "https://other.example/",
        ),
    ):
        batch_session.events.append("body")

    assert batch_session.events == [
        ("lock", -8061424528605705518),
        ("lock", -6410938119080746435),
        ("lock", -2675231333044069939),
        ("lock", -1983210520360554722),
        "body",
    ]


def test_legal_decision_locks_share_regulatory_filing_protocol() -> None:
    draft = review_draft().model_copy(
        update={
            "identity": CompanyIdentityInput(
                canonical_name="Legal Lock",
                legal_identifiers=("CN-123",),
            )
        }
    )

    keys = identity_service._decision_lock_keys((draft,))

    assert keys[-3:] == (
        -6964532361891529432,
        6139631598619119190,
        -1150840122584830540,
    )


def test_sqlite_concurrent_exact_review_recording_returns_one_replay(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'review-record-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as setup:
            run_ids = (crawl_run_row(setup).id, crawl_run_row(setup).id)
        draft = review_draft(name="Concurrent Record")

        started = (Event(), Event())
        finished = (Event(), Event())

        def record(index: int):
            started[index].set()
            with Session(engine) as worker:
                try:
                    return record_identity_review(
                        worker,
                        crawl_run_id=run_ids[index],
                        draft=draft,
                    )
                finally:
                    finished[index].set()

        review_key = b"review\0" + draft.stable_identity_hash.encode("ascii")
        with Session(engine) as locker, ThreadPoolExecutor(max_workers=2) as pool:
            with identity_service._serialized_identity_keys(locker, (review_key,)):
                futures = tuple(pool.submit(record, index) for index in range(2))
                assert all(marker.wait(timeout=5) for marker in started)
                assert not any(marker.wait(timeout=0.2) for marker in finished)
            results = tuple(future.result(timeout=10) for future in futures)

        assert {result.created for result in results} == {False, True}
        assert len({result.review_item_id for result in results}) == 1
        with Session(engine) as verification:
            assert verification.scalar(
                select(func.count()).select_from(CompanyIdentityReviewItem)
            ) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_item_lock_serializes_conflicting_decisions() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"task4_identity_{uuid4().hex}"
    assert re.fullmatch(r"task4_identity_[0-9a-f]{32}", schema_name)
    quoted_schema = f'"{schema_name}"'
    admin_engine = create_engine(database_url)
    scoped_url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    engine = create_engine(scoped_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as setup:
            first_target = company_row(setup, "First Target")
            second_target = company_row(setup, "Second Target")
            item_id = pending_review(setup, candidate_name="Concurrent Alias")
            commands = (
                decision(
                    item_id,
                    action=IdentityReviewAction.LINK_AS_ALIAS,
                    target_company_id=first_target.id,
                ),
                decision(
                    item_id,
                    action=IdentityReviewAction.LINK_AS_ALIAS,
                    target_company_id=second_target.id,
                ),
            )

        started = (Event(), Event())
        finished = (Event(), Event())

        def apply(index: int) -> tuple[str, UUID]:
            started[index].set()
            try:
                with Session(engine) as worker:
                    apply_identity_review_decisions(worker, (commands[index],))
            except IdentityReviewConflict as error:
                return error.code, commands[index].target_company_id  # type: ignore[return-value]
            finally:
                finished[index].set()
            return "applied", commands[index].target_company_id  # type: ignore[return-value]

        with Session(engine) as locker, ThreadPoolExecutor(max_workers=2) as pool:
            transaction = locker.begin()
            locked = locker.scalar(
                select(CompanyIdentityReviewItem)
                .where(CompanyIdentityReviewItem.id == item_id)
                .with_for_update()
            )
            assert locked is not None
            futures = tuple(pool.submit(apply, index) for index in range(2))
            assert all(marker.wait(timeout=5) for marker in started)
            assert not any(marker.wait(timeout=0.2) for marker in finished)
            transaction.commit()
            results = tuple(future.result(timeout=10) for future in futures)

        assert sorted(code for code, _ in results) == [
            "applied",
            "identity_review_conflict",
        ]
        winning_target = next(target_id for code, target_id in results if code == "applied")
        winning_command = next(
            command for command in commands if command.target_company_id == winning_target
        )
        with Session(engine) as verification:
            replay = apply_identity_review_decisions(verification, (winning_command,))
            assert replay.applied == 0
            assert replay.replayed == 1
            assert verification.scalar(
                select(func.count()).select_from(CompanyIdentityReviewDecision)
            ) == 1
            assert set(
                verification.scalars(
                    select(CompanyAlias.company_id).where(
                        CompanyAlias.normalized_alias.in_(
                            {"concurrentalias", "concurrentaliasholdings"}
                        )
                    )
                )
            ) == {winning_target}

        with Session(engine, expire_on_commit=False) as setup:
            record_run_ids = (crawl_run_row(setup).id, crawl_run_row(setup).id)
        concurrent_draft = review_draft(name="PostgreSQL Concurrent Record")

        record_started = (Event(), Event())
        record_finished = (Event(), Event())

        def record(index: int):
            record_started[index].set()
            with Session(engine) as worker:
                try:
                    return record_identity_review(
                        worker,
                        crawl_run_id=record_run_ids[index],
                        draft=concurrent_draft,
                    )
                finally:
                    record_finished[index].set()

        review_key = (
            b"review\0" + concurrent_draft.stable_identity_hash.encode("ascii")
        )
        with Session(engine) as locker, ThreadPoolExecutor(max_workers=2) as pool:
            transaction = locker.begin()
            with identity_service._serialized_identity_keys(locker, (review_key,)):
                futures = tuple(pool.submit(record, index) for index in range(2))
                assert all(marker.wait(timeout=5) for marker in record_started)
                assert not any(marker.wait(timeout=0.2) for marker in record_finished)
            transaction.commit()
            record_results = tuple(future.result(timeout=10) for future in futures)
        assert {result.created for result in record_results} == {False, True}
        assert len({result.review_item_id for result in record_results}) == 1

        with Session(engine, expire_on_commit=False) as setup:
            distinct_target = company_row(setup, "Distinct Item Target")
            name_item_ids = (
                pending_review(
                    setup,
                    candidate_name="Absent Shared Name",
                    identity=CompanyIdentityInput(
                        canonical_name="Absent Shared Name",
                        city="first",
                    ),
                ),
                pending_review(
                    setup,
                    candidate_name="Absent Shared Name",
                    identity=CompanyIdentityInput(
                        canonical_name="Absent Shared Name",
                        city="second",
                    ),
                ),
            )
            name_commands = (
                decision(name_item_ids[0], action=IdentityReviewAction.CREATE_NEW),
                decision(
                    name_item_ids[1],
                    action=IdentityReviewAction.LINK_AS_ALIAS,
                    target_company_id=distinct_target.id,
                ),
            )

        def run_blocked_decision_race(
            commands: tuple[IdentityReviewDecisionInput, IdentityReviewDecisionInput],
            material: bytes,
        ) -> tuple[str, str]:
            race_started = (Event(), Event())
            race_finished = (Event(), Event())

            def apply_distinct(index: int) -> str:
                race_started[index].set()
                try:
                    with Session(engine) as worker:
                        apply_identity_review_decisions(worker, (commands[index],))
                except IdentityOwnerChanged as error:
                    return error.code
                finally:
                    race_finished[index].set()
                return "applied"

            with Session(engine) as locker, ThreadPoolExecutor(max_workers=2) as pool:
                transaction = locker.begin()
                with identity_service._serialized_identity_keys(locker, (material,)):
                    futures = tuple(
                        pool.submit(apply_distinct, index) for index in range(2)
                    )
                    assert all(marker.wait(timeout=5) for marker in race_started)
                    assert not any(
                        marker.wait(timeout=0.2) for marker in race_finished
                    )
                transaction.commit()
                return tuple(  # type: ignore[return-value]
                    future.result(timeout=10) for future in futures
                )

        name_results = run_blocked_decision_race(
            name_commands,
            b"name\0absentsharedname",
        )
        assert sorted(name_results) == ["applied", "identity_owner_changed"]
        with Session(engine) as verification:
            name_owners = set(
                verification.scalars(
                    select(Company.id).where(
                        Company.normalized_name == "absentsharedname"
                    )
                )
            )
            name_owners.update(
                verification.scalars(
                    select(CompanyAlias.company_id).where(
                        CompanyAlias.normalized_alias == "absentsharedname"
                    )
                )
            )
            assert len(name_owners) == 1
            assert verification.scalar(
                select(func.count())
                .select_from(CompanyIdentityReviewDecision)
                .where(CompanyIdentityReviewDecision.review_item_id.in_(name_item_ids))
            ) == 1

        shared_website = "https://shared-evidence.example/"
        with Session(engine, expire_on_commit=False) as setup:
            evidence_item_ids = (
                pending_review(
                    setup,
                    candidate_name="Evidence First",
                    identity=CompanyIdentityInput(
                        canonical_name="Evidence First",
                        official_website=shared_website,
                    ),
                ),
                pending_review(
                    setup,
                    candidate_name="Evidence Second",
                    identity=CompanyIdentityInput(
                        canonical_name="Evidence Second",
                        official_website=shared_website,
                    ),
                ),
            )
            evidence_commands = tuple(
                decision(item_id, action=IdentityReviewAction.CREATE_NEW)
                for item_id in evidence_item_ids
            )

        evidence_results = run_blocked_decision_race(
            evidence_commands,  # type: ignore[arg-type]
            b"website\0https://shared-evidence.example/",
        )
        assert sorted(evidence_results) == ["applied", "identity_owner_changed"]
        with Session(engine) as verification:
            assert verification.scalar(
                select(func.count())
                .select_from(Company)
                .where(Company.normalized_website == shared_website)
            ) == 1
            assert verification.scalar(
                select(func.count())
                .select_from(CompanyIdentityReviewDecision)
                .where(
                    CompanyIdentityReviewDecision.review_item_id.in_(evidence_item_ids)
                )
            ) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
        with admin_engine.begin() as connection:
            assert inspect(connection).get_table_names(schema=schema_name) == []
            connection.execute(text(f"DROP SCHEMA {quoted_schema}"))
        admin_engine.dispose()

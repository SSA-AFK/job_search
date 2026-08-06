from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name
from app.manifest.contracts import (
    AiCategory,
    CandidateDecisionStatus,
    ConfidenceTier,
    ReviewAction,
    ReviewDecisionInput,
)
from app.manifest.identity import (
    IdentityResolutionSummary,
    ReviewDecisionConflict,
    ReviewSummary,
    apply_review_decisions,
    auto_resolve_candidates,
    export_review_queue,
)
from app.manifest.models import CandidateFact, CandidateReview
from app.models import Base, Company, CompanyAlias, JobEntry


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def evidence_id(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def candidate(
    label: str,
    canonical_name: str,
    *,
    aliases: tuple[str, ...] = (),
    category: AiCategory = AiCategory.FOUNDATION_MODELS,
    official_website: str | None = None,
    recruitment_url: str | None = None,
    source_url: str = "https://registry.example/public-list",
    evidence_summary: str = "Public registry identifies the recruiting organization.",
) -> CandidateFact:
    return CandidateFact(
        stable_evidence_id=evidence_id(label),
        canonical_name=canonical_name,
        normalized_name=normalize_name(canonical_name),
        aliases=list(aliases),
        primary_category=category,
        official_website=official_website,
        recruitment_url=recruitment_url,
        source_id="public_registry",
        source_url=source_url,
        retrieved_at=datetime(2026, 8, 6, tzinfo=UTC),
        evidence_summary=evidence_summary,
        confidence_tier=ConfidenceTier.HIGH,
        confidence_reason="Government registry with public identity evidence.",
        decision_status=CandidateDecisionStatus.REVIEW_REQUIRED,
    )


def persist_candidates(session: Session, *facts: CandidateFact) -> None:
    session.add_all(facts)
    session.commit()


def decision(
    fact: CandidateFact,
    *,
    action: ReviewAction = ReviewAction.ACCEPT,
    resulting_status: CandidateDecisionStatus = CandidateDecisionStatus.ACCEPTED,
    resolved_company_id: object | None = None,
    reason: str = "Reviewer confirmed the public recruiting identity.",
    decided_at: datetime = datetime(2026, 8, 6, 12, tzinfo=UTC),
) -> ReviewDecisionInput:
    return ReviewDecisionInput(
        stable_evidence_id=fact.stable_evidence_id,
        action=action,
        resulting_status=resulting_status,
        resolved_company_id=resolved_company_id,
        reason=reason,
        decided_at=decided_at,
    )


def test_exact_alias_group_auto_merges_and_owns_aliases_globally(session: Session) -> None:
    persist_candidates(
        session,
        candidate("alias-a", "Acme Research", aliases=("Acme AI",)),
        candidate("alias-b", "Acme Labs", aliases=("Acme Research",)),
    )

    summary = auto_resolve_candidates(session)

    facts = tuple(session.scalars(select(CandidateFact).order_by(CandidateFact.canonical_name)))
    companies = tuple(session.scalars(select(Company)))
    aliases = tuple(session.scalars(select(CompanyAlias)))
    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert len(companies) == 1
    assert {fact.company_id for fact in facts} == {companies[0].id}
    assert {fact.decision_status for fact in facts} == {CandidateDecisionStatus.ACCEPTED}
    assert {alias.company_id for alias in aliases} == {companies[0].id}
    assert {companies[0].normalized_name} | {
        alias.normalized_alias for alias in aliases
    } == {
        normalize_name("Acme AI"),
        normalize_name("Acme Labs"),
        normalize_name("Acme Research"),
    }


def test_exact_recruitment_entry_auto_merges_inseparable_names(session: Session) -> None:
    shared_entry = "https://careers.example/jobs"
    persist_candidates(
        session,
        candidate("entry-a", "Example Group", recruitment_url=shared_entry),
        candidate("entry-b", "Example Product", recruitment_url=shared_entry),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_exact_ats_tenant_auto_merges_different_pages(session: Session) -> None:
    persist_candidates(
        session,
        candidate(
            "tenant-a",
            "Example Group",
            recruitment_url="https://jobs.lever.co/example/jobs/engineering",
        ),
        candidate(
            "tenant-b",
            "Example Product",
            recruitment_url="https://jobs.lever.co/example/jobs/research",
        ),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_shared_ats_hostname_alone_does_not_merge_tenants(session: Session) -> None:
    persist_candidates(
        session,
        candidate(
            "tenant-one",
            "One Robotics",
            recruitment_url="https://jobs.lever.co/one/jobs",
        ),
        candidate(
            "tenant-two",
            "Two Vision",
            recruitment_url="https://jobs.lever.co/two/jobs",
        ),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 2


@pytest.mark.parametrize(
    "shared_root_url",
    ["https://jobs.lever.co/", "https://jobs.lever.co./"],
)
def test_shared_ats_root_url_alone_does_not_merge_companies(
    session: Session,
    shared_root_url: str,
) -> None:
    persist_candidates(
        session,
        candidate(
            "ats-root-one",
            "Root One Robotics",
            recruitment_url=shared_root_url,
        ),
        candidate(
            "ats-root-two",
            "Root Two Vision",
            recruitment_url=shared_root_url,
        ),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 2


def test_embedded_greenhouse_tenant_query_prevents_shared_host_merge(
    session: Session,
) -> None:
    persist_candidates(
        session,
        candidate(
            "greenhouse-one",
            "Greenhouse One",
            recruitment_url=(
                "https://boards.greenhouse.io/embed/job_board?for=greenhouse-one"
            ),
        ),
        candidate(
            "greenhouse-two",
            "Greenhouse Two",
            recruitment_url=(
                "https://boards.greenhouse.io/embed/job_board?for=greenhouse-two"
            ),
        ),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 2


@pytest.mark.parametrize(
    "generic_url",
    [
        "https://boards.greenhouse.io/embed/job_board",
        "https://apply.workable.com/j/",
    ],
)
def test_generic_shared_ats_path_does_not_merge_without_identity_evidence(
    session: Session,
    generic_url: str,
) -> None:
    persist_candidates(
        session,
        candidate("generic-one", "Generic One Robotics", recruitment_url=generic_url),
        candidate("generic-two", "Generic Two Vision", recruitment_url=generic_url),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 2


def test_independently_attributable_subsidiary_inventories_require_review(
    session: Session,
) -> None:
    persist_candidates(
        session,
        candidate(
            "parent",
            "Example Holdings",
            aliases=("Example AI",),
            recruitment_url="https://jobs.lever.co/example-parent/jobs",
        ),
        candidate(
            "subsidiary",
            "Example AI",
            recruitment_url="https://jobs.lever.co/example-subsidiary/jobs",
        ),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_conflicting_categories_in_exact_group_require_review(session: Session) -> None:
    persist_candidates(
        session,
        candidate("category-a", "Example", aliases=("Example AI",)),
        candidate(
            "category-b",
            "Example AI",
            category=AiCategory.ROBOTICS_EMBODIED_AI,
        ),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_incremental_category_conflict_with_accepted_fact_requires_review(
    session: Session,
) -> None:
    company = Company(
        canonical_name="Incremental Identity",
        normalized_name=normalize_name("Incremental Identity"),
    )
    session.add(company)
    session.flush()
    accepted = candidate("accepted-category", "Incremental Identity")
    accepted.company_id = company.id
    accepted.decision_status = CandidateDecisionStatus.ACCEPTED
    pending = candidate(
        "pending-category",
        "Incremental Identity",
        category=AiCategory.ROBOTICS_EMBODIED_AI,
    )
    session.add_all([accepted, pending])
    session.commit()

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert pending.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert pending.company_id is None


def test_incremental_inventory_conflict_with_accepted_fact_requires_review(
    session: Session,
) -> None:
    company = Company(
        canonical_name="Incremental Inventory",
        normalized_name=normalize_name("Incremental Inventory"),
    )
    session.add(company)
    session.flush()
    accepted = candidate(
        "accepted-inventory",
        "Incremental Inventory",
        recruitment_url="https://jobs.lever.co/inventory-one/jobs",
    )
    accepted.company_id = company.id
    accepted.decision_status = CandidateDecisionStatus.ACCEPTED
    pending = candidate(
        "pending-inventory",
        "Incremental Inventory",
        recruitment_url="https://jobs.lever.co/inventory-two/jobs",
    )
    session.add_all([accepted, pending])
    session.commit()

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert pending.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert pending.company_id is None


def test_incremental_inventory_conflict_with_existing_job_entry_requires_review(
    session: Session,
) -> None:
    company = Company(
        canonical_name="Entry Inventory",
        normalized_name=normalize_name("Entry Inventory"),
    )
    session.add(company)
    session.flush()
    session.add(
        JobEntry(
            company_id=company.id,
            url="https://jobs.lever.co/entry-one/jobs",
            normalized_url="https://jobs.lever.co/entry-one/jobs",
            provider="official",
            platform="lever",
        )
    )
    pending = candidate(
        "pending-entry-inventory",
        "Entry Inventory",
        recruitment_url="https://jobs.lever.co/entry-two/jobs",
    )
    session.add(pending)
    session.commit()

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert pending.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert pending.company_id is None


def test_fuzzy_name_match_requires_review(session: Session) -> None:
    persist_candidates(
        session,
        candidate("fuzzy-a", "Example Artificial Intelligence"),
        candidate("fuzzy-b", "Example Artificial Intelligenc"),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


@pytest.mark.parametrize("existing_as_alias", [False, True])
def test_fuzzy_name_near_existing_company_or_alias_requires_review(
    session: Session,
    existing_as_alias: bool,
) -> None:
    existing_name = "Example Artificial Intelligence"
    company = Company(
        canonical_name="Existing Owner" if existing_as_alias else existing_name,
        normalized_name=normalize_name(
            "Existing Owner" if existing_as_alias else existing_name
        ),
    )
    session.add(company)
    session.flush()
    if existing_as_alias:
        session.add(
            CompanyAlias(
                company_id=company.id,
                alias=existing_name,
                normalized_alias=normalize_name(existing_name),
            )
        )
    fact = candidate("existing-fuzzy", "Example Artificial Intelligenc")
    session.add(fact)
    session.commit()

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert fact.company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_exact_existing_alias_links_without_creating_company(session: Session) -> None:
    company = Company(canonical_name="Example AI", normalized_name=normalize_name("Example AI"))
    session.add(company)
    session.flush()
    session.add(
        CompanyAlias(
            company_id=company.id,
            alias="Example Labs",
            normalized_alias=normalize_name("Example Labs"),
        )
    )
    fact = candidate("existing-alias", "Example Labs")
    session.add(fact)
    session.commit()

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=1, review_required=0)
    assert fact.company_id == company.id
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_exact_existing_job_entry_links_without_using_host_only(session: Session) -> None:
    company = Company(canonical_name="Entry Owner", normalized_name=normalize_name("Entry Owner"))
    other = Company(canonical_name="Other Tenant", normalized_name=normalize_name("Other Tenant"))
    session.add_all([company, other])
    session.flush()
    session.add_all(
        [
            JobEntry(
                company_id=company.id,
                url="https://jobs.lever.co/entry-owner/jobs",
                normalized_url="https://jobs.lever.co/entry-owner/jobs",
                provider="official",
                platform="lever",
            ),
            JobEntry(
                company_id=other.id,
                url="https://jobs.lever.co/other/jobs",
                normalized_url="https://jobs.lever.co/other/jobs",
                provider="official",
                platform="lever",
            ),
        ]
    )
    fact = candidate(
        "existing-entry",
        "Owner Former Name",
        recruitment_url="https://jobs.lever.co/entry-owner/opening/123",
    )
    session.add(fact)
    session.commit()

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=1, review_required=0)
    assert fact.company_id == company.id
    assert session.scalar(select(func.count()).select_from(Company)) == 2


def test_review_export_has_only_sanitized_public_evidence(session: Session) -> None:
    fact = candidate(
        "export",
        "Export Example",
        aliases=("Public Alias",),
        official_website="https://example.com/?token=official-secret",
        recruitment_url="https://jobs.example/jobs?access_token=recruiting-secret",
        source_url="https://registry.example/list?api_key=source-secret",
        evidence_summary="Public registry identifies Export Example.",
    )
    persist_candidates(session, fact)

    items = export_review_queue(session)

    assert len(items) == 1
    exported = asdict(items[0])
    assert exported == {
        "stable_evidence_id": fact.stable_evidence_id,
        "canonical_name": "Export Example",
        "normalized_name": normalize_name("Export Example"),
        "aliases": ("Public Alias",),
        "primary_category": AiCategory.FOUNDATION_MODELS,
        "official_website": "https://example.com/",
        "recruitment_url": "https://jobs.example/jobs",
        "source_id": "public_registry",
        "source_url": "https://registry.example/list",
        "retrieved_at": datetime(2026, 8, 6, tzinfo=UTC),
        "evidence_summary": "Public registry identifies Export Example.",
        "confidence_tier": ConfidenceTier.HIGH,
        "confidence_reason": "Government registry with public identity evidence.",
    }
    assert "secret" not in repr(exported)


def test_review_replay_is_exact_and_does_not_append_audit_rows(session: Session) -> None:
    fact = candidate("review-replay", "Manual Example")
    persist_candidates(session, fact)
    plus_eight = timezone(timedelta(hours=8))
    first = decision(fact, decided_at=datetime(2026, 8, 6, 20, tzinfo=plus_eight))
    equivalent_utc = decision(fact, decided_at=datetime(2026, 8, 6, 12, tzinfo=UTC))

    assert apply_review_decisions(session, [first]) == ReviewSummary(applied=1, replayed=0)
    audit_id = session.scalar(select(CandidateReview.id))
    assert apply_review_decisions(session, [equivalent_utc]) == ReviewSummary(
        applied=0, replayed=1
    )

    assert session.scalar(select(func.count()).select_from(CandidateReview)) == 1
    assert session.scalar(select(CandidateReview.id)) == audit_id

    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(session, [decision(fact, reason="A different decision payload.")])

    assert session.scalar(select(func.count()).select_from(CandidateReview)) == 1


def test_reject_decision_is_append_only_and_never_creates_company(session: Session) -> None:
    fact = candidate("review-reject", "Rejected Example")
    persist_candidates(session, fact)

    summary = apply_review_decisions(
        session,
        [
            decision(
                fact,
                action=ReviewAction.REJECT,
                resulting_status=CandidateDecisionStatus.REJECTED,
                reason="Evidence cannot establish an independent recruiting identity.",
            )
        ],
    )

    assert summary == ReviewSummary(applied=1, replayed=0)
    assert fact.decision_status is CandidateDecisionStatus.REJECTED
    assert fact.company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 0
    review = session.scalar(select(CandidateReview))
    assert review is not None
    assert review.prior_status is CandidateDecisionStatus.REVIEW_REQUIRED


def test_manual_merge_reuses_alias_owned_by_target_company(session: Session) -> None:
    company = Company(canonical_name="Target", normalized_name=normalize_name("Target"))
    session.add(company)
    session.flush()
    session.add(
        CompanyAlias(
            company_id=company.id,
            alias="Former Target",
            normalized_alias=normalize_name("Former Target"),
        )
    )
    fact = candidate("manual-merge", "Former Target")
    session.add(fact)
    session.commit()

    summary = apply_review_decisions(
        session,
        [decision(fact, resolved_company_id=company.id)],
    )

    assert summary == ReviewSummary(applied=1, replayed=0)
    assert fact.company_id == company.id
    assert session.scalar(select(func.count()).select_from(CompanyAlias)) == 1


def test_manual_merge_rejects_company_id_reserved_for_accept_as_new(
    session: Session,
) -> None:
    fact = candidate("reserved-manual-id", "Reserved Manual Identity")
    reserved_id = UUID(fact.stable_evidence_id[:32])
    company = Company(
        id=reserved_id,
        canonical_name="Preexisting Reserved Id",
        normalized_name=normalize_name("Preexisting Reserved Id"),
    )
    session.add_all([company, fact])
    session.commit()

    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(
            session,
            [decision(fact, resolved_company_id=reserved_id)],
        )

    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert session.scalar(select(func.count()).select_from(CandidateReview)) == 0


def test_conflicting_alias_ownership_rolls_back_entire_decision_batch(session: Session) -> None:
    owner = Company(canonical_name="Alias Owner", normalized_name=normalize_name("Alias Owner"))
    session.add(owner)
    session.flush()
    session.add(
        CompanyAlias(
            company_id=owner.id,
            alias="Claimed Alias",
            normalized_alias=normalize_name("Claimed Alias"),
        )
    )
    clean = candidate("batch-clean", "Clean New Company")
    conflicting = candidate("batch-conflict", "Conflicting Company", aliases=("Claimed Alias",))
    session.add_all([clean, conflicting])
    session.commit()

    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(session, [decision(clean), decision(conflicting)])

    session.expire_all()
    assert session.scalar(select(func.count()).select_from(CandidateReview)) == 0
    assert session.scalar(select(func.count()).select_from(Company)) == 1
    assert clean.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert conflicting.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED


def test_non_reviewable_or_incoherent_decision_is_rejected_transactionally(
    session: Session,
) -> None:
    accepted = candidate("already-accepted", "Already Accepted")
    accepted.decision_status = CandidateDecisionStatus.ACCEPTED
    pending = candidate("invalid-pair", "Invalid Pair")
    persist_candidates(session, accepted, pending)

    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(session, [decision(accepted)])
    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(
            session,
            [
                decision(
                    pending,
                    action=ReviewAction.REJECT,
                    resulting_status=CandidateDecisionStatus.ACCEPTED,
                )
            ],
        )

    assert session.scalar(select(func.count()).select_from(CandidateReview)) == 0

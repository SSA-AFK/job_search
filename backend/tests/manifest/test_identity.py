import os
import re
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.company_identity import service as company_identity_service
from app.company_identity.contracts import CompanyIdentityCandidateMatch
from app.core.normalization import normalize_name
from app.ingestion.extraction.schemas import CompanyCandidate
from app.ingestion.normalization.company import normalize_company
from app.ingestion.persistence.contracts import NormalizedCompanyRecord
from app.ingestion.persistence.service import PersistenceService
from app.manifest import identity as identity_module
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


def _quoted_manifest_identity_race_schema(schema_name: str) -> str:
    if re.fullmatch(r"manifest_identity_race_[0-9a-f]{32}", schema_name) is None:
        raise ValueError("invalid isolated manifest identity schema")
    return f'"{schema_name}"'


def _drop_manifest_identity_race_schema(
    connection: Connection,
    schema_name: str,
) -> None:
    quoted_schema = _quoted_manifest_identity_race_schema(schema_name)
    statements = (
        f"DROP TABLE IF EXISTS {quoted_schema}.candidate_reviews",
        f"DROP TABLE IF EXISTS {quoted_schema}.candidate_facts",
        f"DROP TABLE IF EXISTS {quoted_schema}.company_aliases",
        f"DROP TABLE IF EXISTS {quoted_schema}.companies",
        f"DROP SCHEMA {quoted_schema}",
    )
    for statement in statements:
        connection.execute(text(statement))


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)  # type: ignore[attr-defined]
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


def test_manifest_identity_race_cleanup_is_schema_scoped_without_cascade() -> None:
    class RecordingConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: object) -> None:
            self.statements.append(str(statement))

    with pytest.raises(ValueError):
        _drop_manifest_identity_race_schema(  # type: ignore[arg-type]
            RecordingConnection(),
            "public",
        )

    connection = RecordingConnection()
    schema_name = f"manifest_identity_race_{'a' * 32}"
    _drop_manifest_identity_race_schema(  # type: ignore[arg-type]
        connection,
        schema_name,
    )

    assert connection.statements == [
        f'DROP TABLE IF EXISTS "{schema_name}".candidate_reviews',
        f'DROP TABLE IF EXISTS "{schema_name}".candidate_facts',
        f'DROP TABLE IF EXISTS "{schema_name}".company_aliases',
        f'DROP TABLE IF EXISTS "{schema_name}".companies',
        f'DROP SCHEMA "{schema_name}"',
    ]
    assert all("CASCADE" not in statement for statement in connection.statements)


def decision(
    fact: CandidateFact,
    *,
    action: ReviewAction = ReviewAction.ACCEPT,
    resulting_status: CandidateDecisionStatus = CandidateDecisionStatus.ACCEPTED,
    resolved_company_id: UUID | None = None,
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


class _FixedSimilarity:
    available = True

    def __init__(
        self,
        *,
        candidate_review_names: frozenset[str] = frozenset(),
        existing_owners_by_query_name: dict[str, set[UUID]] | None = None,
    ) -> None:
        self.candidate_review_names = candidate_review_names
        self.existing_owners_by_query_name = existing_owners_by_query_name or {}

    def candidate_review_indexes(
        self,
        facts: tuple[CandidateFact, ...],
        groups: tuple[tuple[int, ...], ...],
    ) -> frozenset[int]:
        review_indexes: set[int] = set()
        for group in groups:
            names = {
                normalize_name(display_name)
                for index in group
                for display_name in (facts[index].canonical_name, *facts[index].aliases)
            }
            if names & self.candidate_review_names:
                review_indexes.update(group)
        return frozenset(review_indexes)

    def existing_owner_ids(self, names: frozenset[str]) -> set[UUID]:
        return {
            owner_id
            for name in names
            for owner_id in self.existing_owners_by_query_name.get(name, ())
        }


def resolve_candidates(
    session: Session,
    *,
    candidate_review_names: frozenset[str] = frozenset(),
    existing_owners_by_query_name: dict[str, set[UUID]] | None = None,
) -> IdentityResolutionSummary:
    return auto_resolve_candidates(
        session,
        similarity=_FixedSimilarity(
            candidate_review_names=candidate_review_names,
            existing_owners_by_query_name=existing_owners_by_query_name,
        ),
    )


def test_exact_alias_group_auto_merges_and_owns_aliases_globally(session: Session) -> None:
    persist_candidates(
        session,
        candidate("alias-a", "Acme Research", aliases=("Acme AI",)),
        candidate("alias-b", "Acme Labs", aliases=("Acme Research",)),
    )

    summary = resolve_candidates(session)

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


def test_exact_recruitment_entry_without_exact_name_requires_review(
    session: Session,
) -> None:
    shared_entry = "https://careers.example/jobs"
    persist_candidates(
        session,
        candidate("entry-a", "Example Group", recruitment_url=shared_entry),
        candidate("entry-b", "Example Product", recruitment_url=shared_entry),
    )

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_exact_ats_tenant_without_exact_name_requires_review(session: Session) -> None:
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

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


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

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 2


@pytest.mark.parametrize(
    "shared_root_url",
    ["https://jobs.lever.co/", "https://jobs.lever.co./"],
)
def test_shared_ats_root_url_requires_review(
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

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


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

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 2


@pytest.mark.parametrize(
    "generic_url",
    [
        "https://boards.greenhouse.io/embed/job_board",
        "https://apply.workable.com/j/",
    ],
)
def test_generic_shared_ats_path_requires_review_without_identity_evidence(
    session: Session,
    generic_url: str,
) -> None:
    persist_candidates(
        session,
        candidate("generic-one", "Generic One Robotics", recruitment_url=generic_url),
        candidate("generic-two", "Generic Two Vision", recruitment_url=generic_url),
    )

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_workday_shard_path_tenants_do_not_merge(session: Session) -> None:
    persist_candidates(
        session,
        candidate(
            "workday-one",
            "Workday One Robotics",
            recruitment_url=(
                "https://wd1.myworkdaysite.com/recruiting/company-a/jobs/engineering"
            ),
        ),
        candidate(
            "workday-two",
            "Workday Two Vision",
            recruitment_url=(
                "https://wd1.myworkdaysite.com/recruiting/company-b/jobs/research"
            ),
        ),
    )

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=2, review_required=0)
    assert session.scalar(select(func.count()).select_from(Company)) == 2


def test_exact_workday_tenant_without_exact_name_requires_review(session: Session) -> None:
    persist_candidates(
        session,
        candidate(
            "workday-page-one",
            "Workday Parent",
            recruitment_url=(
                "https://wd1.myworkdaysite.com/recruiting/company-a/jobs/engineering"
            ),
        ),
        candidate(
            "workday-page-two",
            "Workday Product",
            recruitment_url=(
                "https://wd1.myworkdaysite.com/recruiting/company-a/jobs/research"
            ),
        ),
    )

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_unknown_workday_shard_path_requires_review(session: Session) -> None:
    fact = candidate(
        "workday-unknown",
        "Unknown Workday Tenant",
        recruitment_url="https://wd1.myworkdaysite.com/jobs/company-a",
    )
    persist_candidates(session, fact)

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert fact.company_id is None


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

    summary = resolve_candidates(session)

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

    summary = resolve_candidates(session)

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

    summary = resolve_candidates(session)

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

    summary = resolve_candidates(session)

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

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert pending.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert pending.company_id is None


def test_new_identity_cannot_claim_an_existing_normalized_website(
    session: Session,
) -> None:
    website_owner = Company(
        canonical_name="Website Owner",
        normalized_name=normalize_name("Website Owner"),
        website="https://example.com/about",
    )
    fact = candidate(
        "new-website-conflict",
        "New Candidate",
        official_website="HTTPS://EXAMPLE.COM:443/about?utm_source=manifest",
    )
    session.add_all((website_owner, fact))
    session.commit()

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert fact.company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_exact_owner_cannot_claim_another_company_website(session: Session) -> None:
    exact_owner = Company(
        canonical_name="Exact Website Candidate",
        normalized_name=normalize_name("Exact Website Candidate"),
    )
    website_owner = Company(
        canonical_name="Other Website Owner",
        normalized_name=normalize_name("Other Website Owner"),
        website="https://other.example/company",
    )
    fact = candidate(
        "exact-website-conflict",
        "Exact Website Candidate",
        official_website="https://other.example/company?source=manifest",
    )
    session.add_all((exact_owner, website_owner, fact))
    session.commit()

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert fact.company_id is None


def test_exact_owner_may_reuse_its_normalized_website(session: Session) -> None:
    exact_owner = Company(
        canonical_name="Exact Website Owner",
        normalized_name=normalize_name("Exact Website Owner"),
        website="https://owner.example/about",
    )
    fact = candidate(
        "exact-owned-website",
        "Exact Website Owner",
        official_website="HTTPS://OWNER.EXAMPLE:443/about?source=manifest",
    )
    session.add_all((exact_owner, fact))
    session.commit()

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=1, review_required=0)
    assert fact.company_id == exact_owner.id


def test_exact_group_with_multiple_normalized_websites_requires_review(
    session: Session,
) -> None:
    first = candidate(
        "multiple-website-a",
        "Multiple Website Candidate",
        official_website="https://first.example/",
    )
    second = candidate(
        "multiple-website-b",
        "Multiple Website Candidate",
        official_website="https://second.example/",
    )
    persist_candidates(session, first, second)

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert first.company_id is None
    assert second.company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_auto_resolution_locks_identity_material_before_owner_rechecks(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = candidate(
        "auto-lock-order",
        "Lock Order Candidate",
        aliases=("Lock Order Alias",),
        official_website="HTTPS://LOCK.EXAMPLE:443/about?source=manifest",
    )
    persist_candidates(session, fact)
    captured: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    events: list[str] = []
    inside_lock = False
    exact_name_owners = identity_module._exact_name_owners  # type: ignore[attr-defined]
    website_owner_ids = identity_module._targeted_website_owner_ids  # type: ignore[attr-defined]

    @contextmanager
    def observe_identity_lock(
        _session: Session,
        names: Sequence[str],
        *,
        official_websites: Sequence[str] = (),
    ) -> Iterator[None]:
        nonlocal inside_lock
        captured.append((tuple(names), tuple(official_websites)))
        events.append("lock")
        inside_lock = True
        try:
            yield
        finally:
            inside_lock = False
            events.append("unlock")

    def checked_exact_name_owners(
        repository: object,
        names: frozenset[str],
    ) -> dict[str, set[UUID]]:
        assert inside_lock, "exact owner query ran outside the shared identity lock"
        events.append("exact")
        return exact_name_owners(repository, names)  # type: ignore[arg-type]

    def checked_website_owner_ids(
        database_session: Session,
        websites: frozenset[str],
    ) -> set[UUID] | None:
        assert inside_lock, "website owner query ran outside the shared identity lock"
        events.append("website")
        return website_owner_ids(database_session, websites)

    monkeypatch.setattr(
        identity_module,
        "serialized_company_identities",
        observe_identity_lock,
        raising=False,
    )
    monkeypatch.setattr(identity_module, "_exact_name_owners", checked_exact_name_owners)
    monkeypatch.setattr(
        identity_module,
        "_targeted_website_owner_ids",
        checked_website_owner_ids,
    )

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=1, review_required=0)
    assert captured == [
        (
            (normalize_name("Lock Order Alias"), normalize_name("Lock Order Candidate")),
            ("https://lock.example/about",),
        )
    ]
    assert events[0] == "lock"
    assert events[-1] == "unlock"
    assert events.index("exact") < events.index("website") < events.index("unlock")


def test_auto_resolution_refreshes_pending_facts_after_identity_lock(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = candidate("auto-stale-pending", "Stale Pending Candidate")
    persist_candidates(session, fact)

    @contextmanager
    def complete_candidate_before_lock_body(
        database_session: Session,
        _names: Sequence[str],
        *,
        official_websites: Sequence[str] = (),
    ) -> Iterator[None]:
        assert official_websites == ()
        company = Company(
            canonical_name=fact.canonical_name,
            normalized_name=fact.normalized_name,
        )
        database_session.add(company)
        database_session.flush()
        fact.company_id = company.id
        fact.decision_status = CandidateDecisionStatus.ACCEPTED
        database_session.flush()
        yield

    monkeypatch.setattr(
        identity_module,
        "serialized_company_identities",
        complete_candidate_before_lock_body,
    )

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=0)
    assert fact.decision_status is CandidateDecisionStatus.ACCEPTED
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def _assert_sqlite_manifest_lock_waits_for_caller_root_transaction(
    tmp_path: Path,
    *,
    writer_kind: Literal["auto", "reject"],
    outer_outcome: Literal["commit", "rollback"],
) -> None:
    label = f"sqlite-{writer_kind}-outer-{outer_outcome}"
    canonical_name = (
        "Outer Auto Candidate" if writer_kind == "auto" else "Outer Reject Candidate"
    )
    alias = "Outer Auto Alias" if writer_kind == "auto" else "Outer Reject Alias"
    website = f"https://outer-{writer_kind}.example/"
    lock_names = (
        ("outerautoalias", "outerautocandidate")
        if writer_kind == "auto"
        else ("outerrejectalias", "outerrejectcandidate")
    )
    lock_websites = (website,)
    engine = create_engine(
        f"sqlite:///{tmp_path / f'{label}.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)  # type: ignore[attr-defined]
    fact = candidate(
        label,
        canonical_name,
        aliases=(alias,),
        official_website=website,
    )
    observer_attempting = Event()
    observer_acquired = Event()
    lock_keys = company_identity_service._identity_lock_keys(  # type: ignore[attr-defined]
        company_identity_service._company_identities_key_material(  # type: ignore[attr-defined]
            names=lock_names,
            official_websites=lock_websites,
        )
    )

    def lock_states() -> tuple[bool, ...]:
        with company_identity_service._LOCAL_LOCKS_GUARD:  # type: ignore[attr-defined]
            locks_by_key = company_identity_service._LOCAL_LOCKS.get(  # type: ignore[attr-defined]
                engine,
                {},
            )
            return tuple(
                lock is not None and lock.locked()
                for key in lock_keys
                if (lock := locks_by_key.get(key)) is not None
            )

    def observe_after_identity_lock() -> tuple[CandidateDecisionStatus, int, int]:
        with Session(engine, expire_on_commit=False) as observer:
            observer_attempting.set()
            with company_identity_service.serialized_company_identities(
                observer,
                lock_names,
                official_websites=lock_websites,
            ):
                observer_acquired.set()
                status = observer.scalar(
                    select(CandidateFact.decision_status).where(
                        CandidateFact.stable_evidence_id == fact.stable_evidence_id
                    )
                )
                assert status is not None
                return (
                    status,
                    observer.scalar(select(func.count()).select_from(Company)) or 0,
                    observer.scalar(select(func.count()).select_from(CandidateReview))
                    or 0,
                )

    try:
        with Session(engine, expire_on_commit=False) as setup:
            persist_candidates(setup, fact)

        with (
            Session(engine, expire_on_commit=False) as writer,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            outer_transaction = writer.begin()
            try:
                writer.connection().exec_driver_sql("BEGIN")
                if writer_kind == "auto":
                    assert resolve_candidates(writer) == IdentityResolutionSummary(
                        auto_accepted=1,
                        review_required=0,
                    )
                else:
                    stored_fact = writer.scalar(
                        select(CandidateFact).where(
                            CandidateFact.stable_evidence_id == fact.stable_evidence_id
                        )
                    )
                    assert stored_fact is not None
                    assert apply_review_decisions(
                        writer,
                        (
                            decision(
                                stored_fact,
                                action=ReviewAction.REJECT,
                                resulting_status=CandidateDecisionStatus.REJECTED,
                            ),
                        ),
                    ) == ReviewSummary(applied=1, replayed=0)

                held_before_root_end = lock_states()
                observer_future = pool.submit(observe_after_identity_lock)
                assert observer_attempting.wait(timeout=5)
                acquired_before_root_end = observer_acquired.wait(timeout=0.2)
                if outer_outcome == "commit":
                    outer_transaction.commit()
                else:
                    outer_transaction.rollback()
                observed = observer_future.result(timeout=15)
            finally:
                if writer.in_transaction():
                    writer.rollback()

        expected = (
            (
                CandidateDecisionStatus.ACCEPTED,
                1,
                0,
            )
            if writer_kind == "auto" and outer_outcome == "commit"
            else (
                CandidateDecisionStatus.REJECTED,
                0,
                1,
            )
            if writer_kind == "reject" and outer_outcome == "commit"
            else (
                CandidateDecisionStatus.REVIEW_REQUIRED,
                0,
                0,
            )
        )
        assert held_before_root_end == (True,) * len(lock_keys)
        assert not acquired_before_root_end
        assert observed == expected
        assert lock_states() == (False,) * len(lock_keys)
    finally:
        Base.metadata.drop_all(engine)  # type: ignore[attr-defined]
        engine.dispose()


@pytest.mark.parametrize("outer_outcome", ["commit", "rollback"])
def test_sqlite_auto_identity_lock_waits_for_caller_root_transaction(
    tmp_path: Path,
    outer_outcome: Literal["commit", "rollback"],
) -> None:
    _assert_sqlite_manifest_lock_waits_for_caller_root_transaction(
        tmp_path,
        writer_kind="auto",
        outer_outcome=outer_outcome,
    )


@pytest.mark.parametrize("outer_outcome", ["commit", "rollback"])
def test_sqlite_manual_reject_identity_lock_waits_for_caller_root_transaction(
    tmp_path: Path,
    outer_outcome: Literal["commit", "rollback"],
) -> None:
    _assert_sqlite_manifest_lock_waits_for_caller_root_transaction(
        tmp_path,
        writer_kind="reject",
        outer_outcome=outer_outcome,
    )


def test_fuzzy_name_match_requires_review(session: Session) -> None:
    persist_candidates(
        session,
        candidate("fuzzy-a", "Example Artificial Intelligence"),
        candidate("fuzzy-b", "Example Artificial Intelligenc"),
    )

    summary = resolve_candidates(
        session,
        candidate_review_names=frozenset(
            {
                normalize_name("Example Artificial Intelligence"),
                normalize_name("Example Artificial Intelligenc"),
            }
        ),
    )

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=2)
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_sqlite_missing_similarity_fails_closed_without_python_fuzzy_loop(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def unexpected_ratio(left: str, right: str) -> float:
        calls.append((left, right))
        raise AssertionError("SQLite fail-closed path must not run Python fuzzy recall")

    monkeypatch.setattr(identity_module.fuzz, "ratio", unexpected_ratio)
    persist_candidates(
        session,
        *(candidate(f"bounded-{index}", f"Bounded Candidate {index}") for index in range(40)),
    )

    summary = auto_resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=40)
    assert calls == []
    assert session.scalar(select(func.count()).select_from(Company)) == 0


def test_auto_resolution_never_materializes_all_owner_tables(session: Session) -> None:
    owner = Company(
        canonical_name="Bounded Exact Owner",
        normalized_name=normalize_name("Bounded Exact Owner"),
    )
    unrelated = Company(
        canonical_name="Unrelated Owner",
        normalized_name=normalize_name("Unrelated Owner"),
    )
    session.add_all((owner, unrelated))
    session.flush()
    session.add_all(
        (
            CompanyAlias(
                company_id=unrelated.id,
                alias="Unrelated Alias",
                normalized_alias=normalize_name("Unrelated Alias"),
            ),
            JobEntry(
                company_id=unrelated.id,
                url="https://jobs.example/unrelated",
                normalized_url="https://jobs.example/unrelated",
                provider="official",
                platform="unknown",
            ),
            candidate("bounded-owner", "Bounded Exact Owner"),
        )
    )
    session.commit()
    statements: list[str] = []
    bind = session.get_bind()

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _many: bool,
    ) -> None:
        statements.append(" ".join(statement.upper().split()))

    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        summary = auto_resolve_candidates(session)
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert summary == IdentityResolutionSummary(auto_accepted=1, review_required=0)
    for table in ("COMPANIES", "COMPANY_ALIASES", "JOB_ENTRIES"):
        table_selects = [
            statement
            for statement in statements
            if statement.startswith("SELECT") and f"FROM {table}" in statement
        ]
        assert all(" WHERE " in statement for statement in table_selects)
    job_entry_selects = [
        statement
        for statement in statements
        if statement.startswith("SELECT") and "FROM JOB_ENTRIES" in statement
    ]
    assert all(" LIMIT " in statement for statement in job_entry_selects)


class _EmptyRows:
    def __iter__(self):
        return iter(())


class _PostgreSQLSimilarityRecordingSession:
    def __init__(self) -> None:
        self.bind = SimpleNamespace(dialect=postgresql.dialect())
        self.executions: list[tuple[object, object | None]] = []
        self.capability_checks = 0

    def get_bind(self) -> object:
        return self.bind

    def scalar(self, _statement: object) -> bool:
        self.capability_checks += 1
        return True

    def execute(
        self,
        statement: object,
        parameters: object | None = None,
    ) -> _EmptyRows:
        self.executions.append((statement, parameters))
        return _EmptyRows()


@pytest.mark.parametrize(
    ("score", "expected_owner"),
    [
        ("89.999", False),
        ("90", True),
        ("90.001", True),
    ],
)
def test_postgresql_existing_similarity_owners_respect_review_threshold(
    monkeypatch: pytest.MonkeyPatch,
    score: str,
    expected_owner: bool,
) -> None:
    session = _PostgreSQLSimilarityRecordingSession()
    similarity = identity_module._PostgreSQLManifestIdentitySimilarity(  # type: ignore[attr-defined]
        session  # type: ignore[arg-type]
    )
    company_id = UUID("11111111-1111-1111-1111-111111111111")
    match = CompanyIdentityCandidateMatch(
        company_id=company_id,
        canonical_name="Existing Company",
        normalized_name="existingcompany",
        match_kind="fuzzy_canonical",
        score=Decimal(score),
    )
    monkeypatch.setattr(
        similarity._repository,  # type: ignore[attr-defined]
        "find_similar_names_sync",
        lambda _names, *, limit: (match,),
    )

    owner_ids = similarity.existing_owner_ids(frozenset({"candidatecompany"}))

    assert (company_id in owner_ids) is expected_owner


def test_postgresql_manifest_similarity_is_topk_bounded_per_candidate_name() -> None:
    session = _PostgreSQLSimilarityRecordingSession()
    similarity = identity_module._PostgreSQLManifestIdentitySimilarity(  # type: ignore[attr-defined]
        session  # type: ignore[arg-type]
    )
    facts = (
        candidate("pg-similarity-a", "Candidate Alpha", aliases=("Alpha AI",)),
        candidate("pg-similarity-b", "Candidate Beta", aliases=("Beta AI",)),
    )

    assert similarity.candidate_review_indexes(facts, ((0,), (1,))) == frozenset()
    assert similarity.existing_owner_ids(frozenset({"candidatealpha"})) == set()

    statements = tuple(
        " ".join(
            str(
                statement.compile(  # type: ignore[attr-defined]
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            ).upper().split()
        )
        for statement, _parameters in session.executions
    )
    temp_tables = tuple(
        statement
        for statement in statements
        if statement.startswith("CREATE TEMPORARY TABLE")
    )
    assert len(temp_tables) == 1
    assert "NORMALIZED_NAME TEXT NOT NULL" in temp_tables[0]
    candidate_topk = tuple(
        statement
        for statement in statements
        if "JOIN LATERAL" in statement and "PG_TEMP" in statement
    )
    assert len(candidate_topk) == 1
    assert "<->" in candidate_topk[0]
    assert "ORDER BY" in candidate_topk[0]
    assert "LIMIT 20" in candidate_topk[0]
    assert any("USING GIST" in statement and "PUBLIC.GIST_TRGM_OPS" in statement for statement in statements)

    existing_topk = tuple(
        statement
        for statement in statements
        if statement.startswith("SELECT")
        and ("FROM COMPANIES" in statement or "JOIN COMPANY_ALIASES" in statement)
    )
    assert len(existing_topk) == 2
    assert all("<->" in statement for statement in existing_topk)
    assert all("ORDER BY" in statement for statement in existing_topk)
    assert all("LIMIT 20" in statement for statement in existing_topk)


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

    summary = resolve_candidates(
        session,
        existing_owners_by_query_name={
            normalize_name(fact.canonical_name): {company.id}
        },
    )

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert fact.company_id is None
    assert session.scalar(select(func.count()).select_from(Company)) == 1


@pytest.mark.parametrize(
    ("score", "expected_summary", "expected_status"),
    [
        (
            "89.999",
            IdentityResolutionSummary(auto_accepted=1, review_required=0),
            CandidateDecisionStatus.ACCEPTED,
        ),
        (
            "90",
            IdentityResolutionSummary(auto_accepted=0, review_required=1),
            CandidateDecisionStatus.REVIEW_REQUIRED,
        ),
    ],
)
def test_exact_owner_checks_threshold_qualified_fuzzy_conflicts(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    score: str,
    expected_summary: IdentityResolutionSummary,
    expected_status: CandidateDecisionStatus,
) -> None:
    exact_owner = Company(
        canonical_name="Exact Candidate",
        normalized_name=normalize_name("Exact Candidate"),
    )
    fuzzy_owner = Company(
        canonical_name="Fuzzy Conflict",
        normalized_name=normalize_name("Fuzzy Conflict"),
    )
    fact = candidate("exact-with-fuzzy-conflict", "Exact Candidate")
    session.add_all((exact_owner, fuzzy_owner, fact))
    session.commit()
    similarity = identity_module._PostgreSQLManifestIdentitySimilarity(  # type: ignore[attr-defined]
        session
    )
    match = CompanyIdentityCandidateMatch(
        company_id=fuzzy_owner.id,
        canonical_name=fuzzy_owner.canonical_name,
        normalized_name=fuzzy_owner.normalized_name,
        match_kind="fuzzy_canonical",
        score=Decimal(score),
    )
    monkeypatch.setattr(
        similarity._repository,  # type: ignore[attr-defined]
        "similarity_search_available",
        lambda: True,
    )
    monkeypatch.setattr(
        similarity._repository,  # type: ignore[attr-defined]
        "find_similar_names_sync",
        lambda _names, *, limit: (match,),
    )

    summary = auto_resolve_candidates(session, similarity=similarity)

    assert summary == expected_summary
    assert fact.decision_status is expected_status
    assert fact.company_id == (
        exact_owner.id if expected_status is CandidateDecisionStatus.ACCEPTED else None
    )


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

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=1, review_required=0)
    assert fact.company_id == company.id
    assert session.scalar(select(func.count()).select_from(Company)) == 1


def test_exact_existing_job_entry_without_exact_name_requires_review(
    session: Session,
) -> None:
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

    summary = resolve_candidates(session)

    assert summary == IdentityResolutionSummary(auto_accepted=0, review_required=1)
    assert fact.company_id is None
    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
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


def test_review_decision_locks_candidate_in_postgresql(session: Session) -> None:
    fact = candidate("review-lock", "Locked Review Example")
    persist_candidates(session, fact)
    captured_statements: list[object] = []
    bind = session.get_bind()

    def capture_statement(
        _connection: object,
        clauseelement: object,
        _multiparams: object,
        _params: object,
        _execution_options: object,
    ) -> None:
        captured_statements.append(clauseelement)

    event.listen(bind, "before_execute", capture_statement)
    try:
        apply_review_decisions(
            session,
            [
                decision(
                    fact,
                    action=ReviewAction.REJECT,
                    resulting_status=CandidateDecisionStatus.REJECTED,
                )
            ],
        )
    finally:
        event.remove(bind, "before_execute", capture_statement)

    compiled = tuple(
        str(statement.compile(dialect=postgresql.dialect()))
        for statement in captured_statements
        if isinstance(statement, Select)
    )
    candidate_lock_index = next(
        index
        for index, statement in enumerate(compiled)
        if "FROM candidate_facts" in statement and "FOR UPDATE" in statement
    )
    review_read_index = next(
        index
        for index, statement in enumerate(compiled)
        if "FROM candidate_reviews" in statement
    )
    assert candidate_lock_index < review_read_index


def test_stale_reviewer_conflicts_after_another_decision_commits(session: Session) -> None:
    fact = candidate("stale-reviewer", "Stale Reviewer Example")
    persist_candidates(session, fact)
    bind = session.get_bind()

    with Session(bind, expire_on_commit=False) as stale_session:
        stale_fact = stale_session.get(CandidateFact, fact.id)
        assert stale_fact is not None
        assert stale_fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
        stale_session.commit()

        apply_review_decisions(
            session,
            [
                decision(
                    fact,
                    action=ReviewAction.REJECT,
                    resulting_status=CandidateDecisionStatus.REJECTED,
                )
            ],
        )

        with pytest.raises(ReviewDecisionConflict):
            apply_review_decisions(stale_session, [decision(fact)])

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


def test_manual_accept_as_new_rejects_existing_normalized_website_owner(
    session: Session,
) -> None:
    website_owner = Company(
        canonical_name="Manual Website Owner",
        normalized_name=normalize_name("Manual Website Owner"),
        website="https://manual.example/about",
    )
    fact = candidate(
        "manual-new-website-conflict",
        "Manual New Candidate",
        official_website="HTTPS://MANUAL.EXAMPLE:443/about?source=review",
    )
    session.add_all((website_owner, fact))
    session.commit()

    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(session, [decision(fact)])

    session.expire_all()
    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert session.scalar(select(func.count()).select_from(Company)) == 1
    assert session.scalar(select(func.count()).select_from(CandidateReview)) == 0


def test_manual_exact_link_rejects_website_owned_by_another_company(
    session: Session,
) -> None:
    exact_owner = Company(
        canonical_name="Manual Exact Candidate",
        normalized_name=normalize_name("Manual Exact Candidate"),
    )
    website_owner = Company(
        canonical_name="Manual Other Website Owner",
        normalized_name=normalize_name("Manual Other Website Owner"),
        website="https://manual-other.example/",
    )
    fact = candidate(
        "manual-link-website-conflict",
        "Manual Exact Candidate",
        official_website="https://manual-other.example/?source=review",
    )
    session.add_all((exact_owner, website_owner, fact))
    session.commit()

    with pytest.raises(ReviewDecisionConflict):
        apply_review_decisions(
            session,
            [decision(fact, resolved_company_id=exact_owner.id)],
        )

    session.expire_all()
    assert fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
    assert session.scalar(select(func.count()).select_from(CandidateReview)) == 0


def test_manual_exact_link_allows_website_owned_by_target_company(
    session: Session,
) -> None:
    exact_owner = Company(
        canonical_name="Manual Website Target",
        normalized_name=normalize_name("Manual Website Target"),
        website="https://manual-target.example/",
    )
    fact = candidate(
        "manual-link-owned-website",
        "Manual Website Target",
        official_website="HTTPS://MANUAL-TARGET.EXAMPLE:443/?source=review",
    )
    session.add_all((exact_owner, fact))
    session.commit()

    summary = apply_review_decisions(
        session,
        [decision(fact, resolved_company_id=exact_owner.id)],
    )

    assert summary == ReviewSummary(applied=1, replayed=0)
    assert fact.company_id == exact_owner.id


def test_manual_batch_locks_all_identities_before_owner_rechecks(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = candidate(
        "manual-batch-lock-a",
        "Manual Lock One",
        aliases=("Manual One Alias",),
        official_website="HTTPS://MANUAL-ONE.EXAMPLE:443/about?source=review",
    )
    second = candidate(
        "manual-batch-lock-b",
        "Manual Lock Two",
        aliases=("Manual Two Alias",),
        official_website="https://manual-two.example/?source=review",
    )
    rejected = candidate(
        "manual-batch-lock-reject",
        "Manual Lock Rejected",
        aliases=("Manual Rejected Alias",),
        official_website="https://manual-rejected.example/?source=review",
    )
    persist_candidates(session, first, second, rejected)
    captured: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    events: list[str] = []
    inside_lock = False
    exact_name_owners = identity_module._exact_name_owners  # type: ignore[attr-defined]
    website_owner_ids = identity_module._targeted_website_owner_ids  # type: ignore[attr-defined]

    @contextmanager
    def observe_identity_lock(
        _session: Session,
        names: Sequence[str],
        *,
        official_websites: Sequence[str] = (),
    ) -> Iterator[None]:
        nonlocal inside_lock
        captured.append((tuple(names), tuple(official_websites)))
        events.append("lock")
        inside_lock = True
        try:
            yield
        finally:
            inside_lock = False
            events.append("unlock")

    def checked_exact_name_owners(
        repository: object,
        names: frozenset[str],
    ) -> dict[str, set[UUID]]:
        assert inside_lock, "exact owner query ran outside the shared identity lock"
        events.append("exact")
        return exact_name_owners(repository, names)  # type: ignore[arg-type]

    def checked_website_owner_ids(
        database_session: Session,
        websites: frozenset[str],
    ) -> set[UUID] | None:
        assert inside_lock, "website owner query ran outside the shared identity lock"
        events.append("website")
        return website_owner_ids(database_session, websites)

    monkeypatch.setattr(
        identity_module,
        "serialized_company_identities",
        observe_identity_lock,
    )
    monkeypatch.setattr(identity_module, "_exact_name_owners", checked_exact_name_owners)
    monkeypatch.setattr(
        identity_module,
        "_targeted_website_owner_ids",
        checked_website_owner_ids,
    )

    summary = apply_review_decisions(
        session,
        [
            decision(first),
            decision(second),
            decision(
                rejected,
                action=ReviewAction.REJECT,
                resulting_status=CandidateDecisionStatus.REJECTED,
            ),
        ],
    )

    assert summary == ReviewSummary(applied=3, replayed=0)
    assert captured == [
        (
            tuple(
                sorted(
                    {
                        normalize_name("Manual Lock One"),
                        normalize_name("Manual Lock Two"),
                        normalize_name("Manual Lock Rejected"),
                        normalize_name("Manual One Alias"),
                        normalize_name("Manual Two Alias"),
                        normalize_name("Manual Rejected Alias"),
                    }
                )
            ),
            (
                "https://manual-one.example/about",
                "https://manual-rejected.example/",
                "https://manual-two.example/",
            ),
        )
    ]
    assert events[0] == "lock"
    assert events[-1] == "unlock"
    assert events.count("exact") == 2
    assert events.count("website") == 2


@pytest.mark.postgresql
@pytest.mark.parametrize("writer_kind", ["auto", "manual"])
def test_manifest_writer_shares_website_lock_with_persistence(
    writer_kind: str,
) -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"manifest_identity_race_{uuid4().hex}"
    quoted_schema = _quoted_manifest_identity_race_schema(schema_name)
    admin_engine = create_engine(database_url)
    schema_engine = None
    schema_created = False
    shared_website = "https://manifest-shared.example/"
    first_ready = Event()
    release_first = Event()
    manifest_lock_attempted = Event()
    manifest_finished = Event()

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
            schema_created = True

        schema_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema_name}"}
        )
        schema_engine = create_engine(schema_url)
        Base.metadata.create_all(
            schema_engine,
            tables=(
                Company.__table__,
                CompanyAlias.__table__,
                CandidateFact.__table__,
                CandidateReview.__table__,
            ),
        )
        with Session(schema_engine, expire_on_commit=False) as setup:
            fact = candidate(
                f"postgresql-{writer_kind}-website-race",
                "Manifest Website Candidate",
                official_website=shared_website,
            )
            setup.add(fact)
            setup.commit()
            review_decision = decision(fact)

        persistence_record = NormalizedCompanyRecord(
            candidate=normalize_company(
                CompanyCandidate(
                    name="Persistence Website Owner",
                    aliases=(),
                    website=shared_website,
                    description="Public company description",
                    evidence_ids=["public-document"],
                    confidence=0.9,
                )
            ),
            company_id=None,
            field_evidence=(),
        )

        @event.listens_for(schema_engine, "before_cursor_execute")
        def observe_manifest_advisory_lock(
            connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            info = connection.info  # type: ignore[attr-defined]
            if (
                info.get("manifest_identity_race_role") == "manifest"
                and "PG_ADVISORY_XACT_LOCK" in statement.upper()
            ):
                manifest_lock_attempted.set()

        def write_persistence_company() -> UUID:
            with Session(schema_engine) as database_session, database_session.begin():
                company = PersistenceService(database_session)._upsert_company(
                    persistence_record,
                    uuid4(),
                )
                database_session.flush()
                company_id = company.id
                first_ready.set()
                if not release_first.wait(timeout=15):
                    raise TimeoutError("manifest writer did not reach identity lock")
                return company_id

        def write_manifest_identity() -> IdentityResolutionSummary | str:
            try:
                with Session(
                    schema_engine
                ) as database_session, database_session.begin():
                    database_session.connection().info[
                        "manifest_identity_race_role"
                    ] = "manifest"
                    if writer_kind == "auto":
                        return auto_resolve_candidates(
                            database_session,
                            similarity=_FixedSimilarity(),
                        )
                    apply_review_decisions(database_session, (review_decision,))
                    return "applied"
            except ReviewDecisionConflict:
                return "review_conflict"
            finally:
                manifest_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            persistence_future = executor.submit(write_persistence_company)
            if not first_ready.wait(timeout=15):
                persistence_future.result(timeout=15)
                raise TimeoutError("persistence writer did not reach commit hold")
            manifest_future = executor.submit(write_manifest_identity)
            try:
                if not manifest_lock_attempted.wait(timeout=15):
                    manifest_future.result(timeout=15)
                    raise TimeoutError("manifest writer did not reach identity lock")
                assert not manifest_finished.wait(timeout=0.2)
            finally:
                release_first.set()
            persistence_company_id = persistence_future.result(timeout=15)
            manifest_result = manifest_future.result(timeout=15)

        if writer_kind == "auto":
            assert manifest_result == IdentityResolutionSummary(
                auto_accepted=0,
                review_required=1,
            )
        else:
            assert manifest_result == "review_conflict"

        with Session(schema_engine) as verification:
            companies = tuple(verification.scalars(select(Company)))
            stored_fact = verification.scalar(
                select(CandidateFact).where(
                    CandidateFact.stable_evidence_id == fact.stable_evidence_id
                )
            )
            assert tuple(company.id for company in companies) == (
                persistence_company_id,
            )
            assert companies[0].normalized_website == shared_website
            assert stored_fact is not None
            assert stored_fact.decision_status is CandidateDecisionStatus.REVIEW_REQUIRED
            assert stored_fact.company_id is None
            assert verification.scalar(
                select(func.count()).select_from(CandidateReview)
            ) == 0
    finally:
        release_first.set()
        if schema_engine is not None:
            schema_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                _drop_manifest_identity_race_schema(connection, schema_name)
                assert connection.scalar(
                    text("SELECT to_regnamespace(:schema_name) IS NULL"),
                    {"schema_name": schema_name},
                )
        admin_engine.dispose()


@pytest.mark.postgresql
def test_manifest_auto_and_manual_reject_share_identity_lock() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if database_url is None:
        pytest.skip("TEST_POSTGRES_URL is not configured")

    schema_name = f"manifest_identity_race_{uuid4().hex}"
    quoted_schema = _quoted_manifest_identity_race_schema(schema_name)
    admin_engine = create_engine(database_url)
    schema_engine = None
    schema_created = False
    started = (Event(), Event())
    finished = (Event(), Event())

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
            schema_created = True

        schema_url = make_url(database_url).update_query_dict(
            {"options": f"-csearch_path={schema_name}"}
        )
        schema_engine = create_engine(schema_url)
        Base.metadata.create_all(
            schema_engine,
            tables=(
                Company.__table__,
                CompanyAlias.__table__,
                CandidateFact.__table__,
                CandidateReview.__table__,
            ),
        )
        with Session(schema_engine, expire_on_commit=False) as setup:
            fact = candidate(
                "postgresql-auto-reject-race",
                "Manifest Reject Race",
                aliases=("Manifest Reject Alias",),
            )
            setup.add(fact)
            setup.commit()
            stable_evidence_id = fact.stable_evidence_id
            identity_names = tuple(sorted(identity_module._identity_names(fact)))  # type: ignore[attr-defined]
            reject_decision = decision(
                fact,
                action=ReviewAction.REJECT,
                resulting_status=CandidateDecisionStatus.REJECTED,
            )

        def run_auto() -> IdentityResolutionSummary:
            started[0].set()
            try:
                with Session(schema_engine) as database_session:
                    return auto_resolve_candidates(
                        database_session,
                        similarity=_FixedSimilarity(),
                    )
            finally:
                finished[0].set()

        def run_reject() -> str:
            started[1].set()
            try:
                with Session(schema_engine) as database_session:
                    apply_review_decisions(database_session, (reject_decision,))
                return "rejected"
            except ReviewDecisionConflict:
                return "review_conflict"
            finally:
                finished[1].set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            with Session(schema_engine) as locker:
                transaction = locker.begin()
                with identity_module.serialized_company_identities(
                    locker,
                    identity_names,
                ):
                    auto_future = pool.submit(run_auto)
                    reject_future = pool.submit(run_reject)
                    all_started = all(marker.wait(timeout=15) for marker in started)
                    both_waiting = not any(
                        marker.wait(timeout=0.2) for marker in finished
                    )
                transaction.commit()
            auto_result = auto_future.result(timeout=15)
            reject_result = reject_future.result(timeout=15)
        assert all_started
        assert both_waiting

        with Session(schema_engine) as verification:
            stored_fact = verification.scalar(
                select(CandidateFact).where(
                    CandidateFact.stable_evidence_id == stable_evidence_id
                )
            )
            assert stored_fact is not None
            company_count = verification.scalar(
                select(func.count()).select_from(Company)
            )
            review_count = verification.scalar(
                select(func.count()).select_from(CandidateReview)
            )
            if reject_result == "rejected":
                assert auto_result == IdentityResolutionSummary(
                    auto_accepted=0,
                    review_required=0,
                )
                assert stored_fact.decision_status is CandidateDecisionStatus.REJECTED
                assert stored_fact.company_id is None
                assert company_count == 0
                assert review_count == 1
            else:
                assert reject_result == "review_conflict"
                assert auto_result == IdentityResolutionSummary(
                    auto_accepted=1,
                    review_required=0,
                )
                assert stored_fact.decision_status is CandidateDecisionStatus.ACCEPTED
                assert stored_fact.company_id is not None
                assert company_count == 1
                assert review_count == 0
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                _drop_manifest_identity_race_schema(connection, schema_name)
                assert connection.scalar(
                    text("SELECT to_regnamespace(:schema_name) IS NULL"),
                    {"schema_name": schema_name},
                )
        admin_engine.dispose()


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

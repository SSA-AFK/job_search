"""Conservative recruiting-identity resolution and append-only manual reviews."""

import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import UUID

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name, normalize_url
from app.manifest.contracts import (
    AiCategory,
    CandidateDecisionStatus,
    ConfidenceTier,
    ReviewAction,
    ReviewDecisionInput,
)
from app.manifest.models import CandidateFact, CandidateReview
from app.models import Company, CompanyAlias, JobEntry


class ReviewDecisionConflict(ValueError):
    """Raised when a review command cannot be applied without changing prior decisions."""


@dataclass(frozen=True)
class IdentityResolutionSummary:
    auto_accepted: int
    review_required: int


@dataclass(frozen=True)
class CandidateReviewItem:
    stable_evidence_id: str
    canonical_name: str
    normalized_name: str
    aliases: tuple[str, ...]
    primary_category: AiCategory
    official_website: str | None
    recruitment_url: str | None
    source_id: str
    source_url: str | None
    retrieved_at: datetime
    evidence_summary: str
    confidence_tier: ConfidenceTier
    confidence_reason: str


@dataclass(frozen=True)
class ReviewSummary:
    applied: int
    replayed: int


_FUZZY_REVIEW_THRESHOLD = 90
_SHARED_ATS_TENANT_PATH_HOSTS = frozenset(
    {
        "apply.workable.com",
        "boards.greenhouse.io",
        "careers.smartrecruiters.com",
        "job-boards.greenhouse.io",
        "jobs.ashbyhq.com",
        "jobs.lever.co",
    }
)
_GREENHOUSE_HOSTS = frozenset(
    {"boards.greenhouse.io", "job-boards.greenhouse.io"}
)
_IDENTITY_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


@dataclass(frozen=True)
class _RecruitmentEvidence:
    identity: str | None
    ambiguous: bool


_NO_RECRUITMENT_EVIDENCE = _RecruitmentEvidence(identity=None, ambiguous=False)
_AMBIGUOUS_RECRUITMENT_EVIDENCE = _RecruitmentEvidence(identity=None, ambiguous=True)


@contextmanager
def _atomic(session: Session) -> Iterator[None]:
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        yield


def _normalized_url(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_url(value)
    except (TypeError, UnicodeError, ValueError):
        return None


def _sanitized_public_url(value: str | None) -> str | None:
    normalized = _normalized_url(value)
    if normalized is None:
        return None
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _identity_segment(value: str) -> str | None:
    return value if _IDENTITY_SEGMENT_PATTERN.fullmatch(value) else None


def _recruitment_evidence(value: str | None) -> _RecruitmentEvidence:
    if value is None:
        return _NO_RECRUITMENT_EVIDENCE
    normalized = _normalized_url(value)
    if normalized is None:
        return _AMBIGUOUS_RECRUITMENT_EVIDENCE
    parts = urlsplit(normalized)
    host = None if parts.hostname is None else parts.hostname.removesuffix(".")
    path_parts = tuple(part for part in parts.path.split("/") if part)
    if host in _SHARED_ATS_TENANT_PATH_HOSTS:
        if not path_parts:
            return _AMBIGUOUS_RECRUITMENT_EVIDENCE
        if host in _GREENHOUSE_HOSTS and path_parts[0] == "embed":
            tenants = tuple(
                tenant
                for tenant in parse_qs(parts.query).get("for", ())
                if _identity_segment(tenant) is not None
            )
            if len(tenants) == 1:
                return _RecruitmentEvidence(
                    identity=f"tenant:{host}:{tenants[0]}", ambiguous=False
                )
            return _AMBIGUOUS_RECRUITMENT_EVIDENCE
        if host == "apply.workable.com" and path_parts[0] == "j":
            if len(path_parts) > 1 and _identity_segment(path_parts[1]) is not None:
                return _RecruitmentEvidence(identity=f"url:{normalized}", ambiguous=False)
            return _AMBIGUOUS_RECRUITMENT_EVIDENCE
        tenant = _identity_segment(path_parts[0])
        if tenant is None:
            return _AMBIGUOUS_RECRUITMENT_EVIDENCE
        return _RecruitmentEvidence(
            identity=f"tenant:{host}:{tenant}", ambiguous=False
        )
    if host is not None and host.endswith(".myworkdaysite.com"):
        if (
            len(path_parts) >= 2
            and path_parts[0] == "recruiting"
            and (tenant := _identity_segment(path_parts[1])) is not None
        ):
            return _RecruitmentEvidence(
                identity=f"tenant:{host}:{tenant}", ambiguous=False
            )
        return _AMBIGUOUS_RECRUITMENT_EVIDENCE
    if host is not None and host.endswith(".myworkdayjobs.com"):
        host_prefix = host.removesuffix(".myworkdayjobs.com")
        prefix_parts = tuple(part for part in host_prefix.split(".") if part)
        if (
            len(prefix_parts) >= 2
            and _identity_segment(prefix_parts[0]) is not None
            and re.fullmatch(r"wd[0-9]+", prefix_parts[-1]) is not None
        ):
            return _RecruitmentEvidence(identity=f"tenant:{host}", ambiguous=False)
        return _AMBIGUOUS_RECRUITMENT_EVIDENCE
    return _RecruitmentEvidence(identity=f"url:{normalized}", ambiguous=False)


def _recruitment_identity(value: str | None) -> str | None:
    return _recruitment_evidence(value).identity


def _identity_names(fact: CandidateFact) -> frozenset[str]:
    names = {fact.normalized_name}
    names.update(normalize_name(alias) for alias in fact.aliases)
    return frozenset(name for name in names if name)


def _identity_keys(fact: CandidateFact) -> frozenset[str]:
    keys = {f"name:{name}" for name in _identity_names(fact)}
    recruitment_identity = _recruitment_identity(fact.recruitment_url)
    if recruitment_identity is not None:
        keys.add(f"recruitment:{recruitment_identity}")
    return frozenset(keys)


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))

    def find(self, index: int) -> int:
        parent = self.parents[index]
        if parent != index:
            self.parents[index] = self.find(parent)
        return self.parents[index]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root


def _exact_groups(facts: tuple[CandidateFact, ...]) -> tuple[tuple[int, ...], ...]:
    disjoint_set = _DisjointSet(len(facts))
    first_index_by_key: dict[str, int] = {}
    for index, fact in enumerate(facts):
        for key in _identity_keys(fact):
            first_index = first_index_by_key.setdefault(key, index)
            disjoint_set.union(first_index, index)

    indexes_by_root: dict[int, list[int]] = {}
    for index in range(len(facts)):
        indexes_by_root.setdefault(disjoint_set.find(index), []).append(index)
    return tuple(
        tuple(indexes)
        for indexes in sorted(
            indexes_by_root.values(), key=lambda values: facts[values[0]].stable_evidence_id
        )
    )


def _fuzzy_review_indexes(
    facts: tuple[CandidateFact, ...], groups: tuple[tuple[int, ...], ...]
) -> frozenset[int]:
    names_by_group = [
        frozenset(name for index in group for name in _identity_names(facts[index]))
        for group in groups
    ]
    review_indexes: set[int] = set()
    for left_position, left_group in enumerate(groups):
        for right_position in range(left_position + 1, len(groups)):
            if any(
                fuzz.ratio(left_name, right_name) >= _FUZZY_REVIEW_THRESHOLD
                for left_name in names_by_group[left_position]
                for right_name in names_by_group[right_position]
            ):
                review_indexes.update(left_group)
                review_indexes.update(groups[right_position])
    return frozenset(review_indexes)


def _has_separable_recruiting_inventories(
    facts: Sequence[CandidateFact],
    entries: Sequence[JobEntry] = (),
) -> bool:
    identities = {
        identity
        for fact in facts
        if (identity := _recruitment_identity(fact.recruitment_url)) is not None
    }
    identities.update(
        identity
        for entry in entries
        if (identity := _recruitment_identity(entry.normalized_url)) is not None
    )
    return len(identities) > 1


def _has_ambiguous_recruitment_evidence(
    facts: Sequence[CandidateFact],
    entries: Sequence[JobEntry] = (),
) -> bool:
    return any(
        _recruitment_evidence(value).ambiguous
        for value in (
            *(fact.recruitment_url for fact in facts),
            *(entry.normalized_url for entry in entries),
        )
    )


def _name_owners(
    companies: Sequence[Company], aliases: Sequence[CompanyAlias]
) -> dict[str, set[UUID]]:
    owners: dict[str, set[UUID]] = {}
    for company in companies:
        owners.setdefault(company.normalized_name, set()).add(company.id)
    for alias in aliases:
        owners.setdefault(alias.normalized_alias, set()).add(alias.company_id)
    return owners


def _recruitment_owners(entries: Sequence[JobEntry]) -> dict[str, set[UUID]]:
    owners: dict[str, set[UUID]] = {}
    for entry in entries:
        identity = _recruitment_identity(entry.normalized_url)
        if identity is not None:
            owners.setdefault(identity, set()).add(entry.company_id)
    return owners


def _fact_owner_ids(
    fact: CandidateFact,
    name_owners: dict[str, set[UUID]],
    recruitment_owners: dict[str, set[UUID]],
) -> set[UUID]:
    owners = {
        company_id
        for name in _identity_names(fact)
        for company_id in name_owners.get(name, ())
    }
    recruitment_identity = _recruitment_identity(fact.recruitment_url)
    if recruitment_identity is not None:
        owners.update(recruitment_owners.get(recruitment_identity, ()))
    return owners


def _group_owner_ids(
    facts: Sequence[CandidateFact],
    name_owners: dict[str, set[UUID]],
    recruitment_owners: dict[str, set[UUID]],
) -> set[UUID]:
    return {
        owner_id
        for fact in facts
        for owner_id in _fact_owner_ids(fact, name_owners, recruitment_owners)
    }


def _fuzzy_existing_owner_ids(
    facts: Sequence[CandidateFact],
    name_owners: dict[str, set[UUID]],
) -> set[UUID]:
    candidate_names = {
        name for fact in facts for name in _identity_names(fact)
    }
    return {
        owner_id
        for candidate_name in candidate_names
        for existing_name, owner_ids in name_owners.items()
        if candidate_name != existing_name
        and fuzz.ratio(candidate_name, existing_name) >= _FUZZY_REVIEW_THRESHOLD
        for owner_id in owner_ids
    }


def _canonical_fact(facts: Sequence[CandidateFact]) -> CandidateFact:
    return min(
        facts,
        key=lambda fact: (
            fact.normalized_name,
            fact.canonical_name,
            fact.stable_evidence_id,
        ),
    )


def _display_names(facts: Sequence[CandidateFact]) -> dict[str, str]:
    display_by_normalized: dict[str, str] = {}
    for fact in facts:
        for display_name in (fact.canonical_name, *fact.aliases):
            normalized_name = normalize_name(display_name)
            if not normalized_name:
                continue
            display_by_normalized[normalized_name] = min(
                display_name,
                display_by_normalized.get(normalized_name, display_name),
            )
    return display_by_normalized


def _aliases_fit_storage(facts: Sequence[CandidateFact]) -> bool:
    return all(
        len(display_name) <= 255 and len(normalized_name) <= 255
        for normalized_name, display_name in _display_names(facts).items()
    )


def _create_company(
    session: Session,
    facts: Sequence[CandidateFact],
    *,
    company_id: UUID | None = None,
) -> Company:
    canonical = _canonical_fact(facts)
    website = _sanitized_public_url(canonical.official_website)
    company = Company(
        id=company_id,
        canonical_name=canonical.canonical_name,
        normalized_name=canonical.normalized_name,
        website=website if website is None or len(website) <= 1000 else None,
    )
    session.add(company)
    session.flush()
    return company


def _upsert_aliases(session: Session, company: Company, facts: Sequence[CandidateFact]) -> None:
    for normalized_alias, alias_value in sorted(_display_names(facts).items()):
        if normalized_alias == company.normalized_name:
            continue
        canonical_owner = session.scalar(
            select(Company.id).where(Company.normalized_name == normalized_alias)
        )
        if canonical_owner is not None and canonical_owner != company.id:
            raise ReviewDecisionConflict("candidate name is owned by another company")
        alias = session.scalar(
            select(CompanyAlias).where(CompanyAlias.normalized_alias == normalized_alias)
        )
        if alias is None:
            session.add(
                CompanyAlias(
                    company_id=company.id,
                    alias=alias_value,
                    normalized_alias=normalized_alias,
                )
            )
        elif alias.company_id != company.id:
            raise ReviewDecisionConflict("candidate alias is owned by another company")
    session.flush()


def auto_resolve_candidates(session: Session) -> IdentityResolutionSummary:
    """Accept only unambiguous exact identities; leave every weak match for review."""

    with _atomic(session):
        facts = tuple(
            session.scalars(
                select(CandidateFact)
                .where(
                    CandidateFact.decision_status
                    == CandidateDecisionStatus.REVIEW_REQUIRED
                )
                .order_by(CandidateFact.stable_evidence_id)
            )
        )
        accepted_facts = tuple(
            session.scalars(
                select(CandidateFact).where(
                    CandidateFact.decision_status
                    == CandidateDecisionStatus.ACCEPTED,
                    CandidateFact.company_id.is_not(None),
                )
            )
        )
        groups = _exact_groups(facts)
        fuzzy_review_indexes = _fuzzy_review_indexes(facts, groups)
        companies = tuple(session.scalars(select(Company)))
        aliases = tuple(session.scalars(select(CompanyAlias)))
        entries = tuple(session.scalars(select(JobEntry)))
        companies_by_id = {company.id: company for company in companies}
        name_owners = _name_owners(companies, aliases)
        recruitment_owners = _recruitment_owners(entries)
        accepted_facts_by_company: dict[UUID, list[CandidateFact]] = {}
        for accepted_fact in accepted_facts:
            company_id = accepted_fact.company_id
            if company_id is None:
                continue
            accepted_facts_by_company.setdefault(company_id, []).append(accepted_fact)
            for name in _identity_names(accepted_fact):
                name_owners.setdefault(name, set()).add(company_id)
            recruitment_identity = _recruitment_identity(
                accepted_fact.recruitment_url
            )
            if recruitment_identity is not None:
                recruitment_owners.setdefault(recruitment_identity, set()).add(
                    company_id
                )
        entries_by_company: dict[UUID, list[JobEntry]] = {}
        for entry in entries:
            entries_by_company.setdefault(entry.company_id, []).append(entry)
        auto_accepted = 0

        for group in groups:
            group_facts = tuple(facts[index] for index in group)
            owner_ids = _group_owner_ids(group_facts, name_owners, recruitment_owners)
            context_facts: Sequence[CandidateFact] = group_facts
            context_entries: Sequence[JobEntry] = ()
            if len(owner_ids) == 1:
                company_id = next(iter(owner_ids))
                context_facts = (
                    *group_facts,
                    *accepted_facts_by_company.get(company_id, ()),
                )
                context_entries = entries_by_company.get(company_id, ())
            categories = {fact.primary_category for fact in context_facts}
            fuzzy_existing_owner_ids = _fuzzy_existing_owner_ids(
                group_facts, name_owners
            )
            if (
                any(index in fuzzy_review_indexes for index in group)
                or len(categories) != 1
                or _has_separable_recruiting_inventories(
                    context_facts, context_entries
                )
                or _has_ambiguous_recruitment_evidence(
                    context_facts, context_entries
                )
                or len(owner_ids) > 1
                or not fuzzy_existing_owner_ids.issubset(owner_ids)
                or not _aliases_fit_storage(group_facts)
            ):
                continue

            if owner_ids:
                company = companies_by_id[next(iter(owner_ids))]
            else:
                company = _create_company(session, group_facts)
                companies_by_id[company.id] = company
            _upsert_aliases(session, company, group_facts)
            for fact in group_facts:
                fact.company_id = company.id
                fact.decision_status = CandidateDecisionStatus.ACCEPTED
            auto_accepted += len(group_facts)

        session.flush()
        return IdentityResolutionSummary(
            auto_accepted=auto_accepted,
            review_required=len(facts) - auto_accepted,
        )


def export_review_queue(session: Session) -> tuple[CandidateReviewItem, ...]:
    """Return stable public evidence only, with URL query credentials removed."""

    facts = session.scalars(
        select(CandidateFact)
        .where(
            CandidateFact.decision_status == CandidateDecisionStatus.REVIEW_REQUIRED
        )
        .order_by(CandidateFact.stable_evidence_id)
    )
    return tuple(
        CandidateReviewItem(
            stable_evidence_id=fact.stable_evidence_id,
            canonical_name=fact.canonical_name,
            normalized_name=fact.normalized_name,
            aliases=tuple(fact.aliases),
            primary_category=fact.primary_category,
            official_website=_sanitized_public_url(fact.official_website),
            recruitment_url=_sanitized_public_url(fact.recruitment_url),
            source_id=fact.source_id,
            source_url=_sanitized_public_url(fact.source_url),
            retrieved_at=fact.retrieved_at,
            evidence_summary=fact.evidence_summary,
            confidence_tier=fact.confidence_tier,
            confidence_reason=fact.confidence_reason,
        )
        for fact in facts
    )


def _validated_decision(value: ReviewDecisionInput) -> ReviewDecisionInput:
    return ReviewDecisionInput.model_validate(value.model_dump(mode="json"))


def _canonical_decision_bytes(
    *,
    stable_evidence_id: str,
    action: ReviewAction,
    resulting_status: CandidateDecisionStatus,
    resolved_company_id: UUID | None,
    reason: str,
    decided_at: datetime,
) -> bytes:
    return json.dumps(
        {
            "action": action.value,
            "decided_at": decided_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "reason": reason,
            "resolved_company_id": (
                None if resolved_company_id is None else str(resolved_company_id)
            ),
            "resulting_status": resulting_status.value,
            "stable_evidence_id": stable_evidence_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _decision_hash(decision: ReviewDecisionInput) -> str:
    return sha256(
        _canonical_decision_bytes(
            stable_evidence_id=decision.stable_evidence_id,
            action=decision.action,
            resulting_status=decision.resulting_status,
            resolved_company_id=decision.resolved_company_id,
            reason=decision.reason,
            decided_at=decision.decided_at,
        )
    ).hexdigest()


def _manual_new_company_id(stable_evidence_id: str) -> UUID:
    return UUID(stable_evidence_id[:32])


def _stored_review_hash(review: CandidateReview, stable_evidence_id: str) -> str:
    resolved_company_id = review.resulting_company_id
    if resolved_company_id == _manual_new_company_id(stable_evidence_id):
        resolved_company_id = None
    return sha256(
        _canonical_decision_bytes(
            stable_evidence_id=stable_evidence_id,
            action=review.action,
            resulting_status=review.resulting_status,
            resolved_company_id=resolved_company_id,
            reason=review.reason,
            decided_at=review.decided_at,
        )
    ).hexdigest()


def _validate_decision_transition(decision: ReviewDecisionInput) -> None:
    if decision.action is ReviewAction.ACCEPT:
        valid = decision.resulting_status is CandidateDecisionStatus.ACCEPTED
    else:
        valid = (
            decision.resulting_status is CandidateDecisionStatus.REJECTED
            and decision.resolved_company_id is None
        )
    if not valid:
        raise ReviewDecisionConflict("review action and result are inconsistent")


def _resolve_manual_company(
    session: Session,
    fact: CandidateFact,
    decision: ReviewDecisionInput,
) -> Company:
    companies = tuple(session.scalars(select(Company)))
    aliases = tuple(session.scalars(select(CompanyAlias)))
    entries = tuple(session.scalars(select(JobEntry)))
    owner_ids = _fact_owner_ids(
        fact,
        _name_owners(companies, aliases),
        _recruitment_owners(entries),
    )

    if decision.resolved_company_id is None:
        if owner_ids:
            raise ReviewDecisionConflict(
                "accepting a known identity requires its resolved company id"
            )
        company_id = _manual_new_company_id(fact.stable_evidence_id)
        if session.get(Company, company_id) is not None:
            raise ReviewDecisionConflict("stable manual identity is already occupied")
        company = _create_company(session, (fact,), company_id=company_id)
    else:
        if decision.resolved_company_id == _manual_new_company_id(
            fact.stable_evidence_id
        ):
            raise ReviewDecisionConflict(
                "resolved company id is reserved for accept-as-new replay"
            )
        resolved_company = session.get(Company, decision.resolved_company_id)
        if resolved_company is None:
            raise ReviewDecisionConflict("resolved company does not exist")
        if owner_ids - {resolved_company.id}:
            raise ReviewDecisionConflict("candidate identity is owned by another company")
        company = resolved_company

    if not _aliases_fit_storage((fact,)):
        raise ReviewDecisionConflict("candidate aliases exceed company storage limits")
    _upsert_aliases(session, company, (fact,))
    return company


def _apply_one_decision(
    session: Session,
    decision: ReviewDecisionInput,
) -> bool:
    fact = session.scalar(
        select(CandidateFact).where(
            CandidateFact.stable_evidence_id == decision.stable_evidence_id
        ).with_for_update().execution_options(populate_existing=True)
    )
    if fact is None:
        raise ReviewDecisionConflict("review decision references unknown evidence")

    reviews = tuple(
        session.scalars(
            select(CandidateReview).where(CandidateReview.candidate_fact_id == fact.id)
        )
    )
    if reviews:
        stored_hashes = {
            _stored_review_hash(review, fact.stable_evidence_id) for review in reviews
        }
        if stored_hashes == {_decision_hash(decision)}:
            return False
        raise ReviewDecisionConflict("review decision conflicts with append-only audit")

    if fact.decision_status is not CandidateDecisionStatus.REVIEW_REQUIRED:
        raise ReviewDecisionConflict("candidate is not reviewable")
    _validate_decision_transition(decision)

    company: Company | None = None
    if decision.action is ReviewAction.ACCEPT:
        company = _resolve_manual_company(session, fact, decision)

    session.add(
        CandidateReview(
            candidate_fact_id=fact.id,
            prior_status=fact.decision_status,
            action=decision.action,
            resulting_status=decision.resulting_status,
            resulting_company_id=None if company is None else company.id,
            reason=decision.reason,
            decided_at=decision.decided_at,
        )
    )
    fact.decision_status = decision.resulting_status
    fact.company_id = None if company is None else company.id
    session.flush()
    return True


def apply_review_decisions(
    session: Session,
    decisions: Sequence[ReviewDecisionInput],
) -> ReviewSummary:
    """Atomically apply exact, replay-idempotent review commands."""

    validated = tuple(_validated_decision(decision) for decision in decisions)
    applied = 0
    replayed = 0
    with _atomic(session):
        for decision in validated:
            if _apply_one_decision(session, decision):
                applied += 1
            else:
                replayed += 1
    return ReviewSummary(applied=applied, replayed=replayed)

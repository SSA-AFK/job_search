"""Conservative recruiting-identity resolution and append-only manual reviews."""

import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import UUID, uuid4

from rapidfuzz import fuzz
from sqlalchemy import ColumnElement, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute

from app.company_identity.repository import SqlAlchemyCompanyIdentityRepository
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
_MAX_SIMILARITY_CANDIDATES = 20
_MAX_CONTEXT_ROWS = 100
_EXACT_NAME_QUERY_CHUNK = 500
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


class _ManifestIdentitySimilarity(Protocol):
    available: bool

    def candidate_review_indexes(
        self,
        facts: tuple[CandidateFact, ...],
        groups: tuple[tuple[int, ...], ...],
    ) -> frozenset[int]: ...

    def existing_owner_ids(self, names: frozenset[str]) -> set[UUID]: ...


class _PostgreSQLManifestIdentitySimilarity:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = SqlAlchemyCompanyIdentityRepository(
            session,
            similarity_limit=_MAX_SIMILARITY_CANDIDATES,
        )

    @property
    def available(self) -> bool:
        return self._repository.similarity_search_available()

    def candidate_review_indexes(
        self,
        facts: tuple[CandidateFact, ...],
        groups: tuple[tuple[int, ...], ...],
    ) -> frozenset[int]:
        if not self.available or len(groups) < 2:
            return frozenset()

        rows = tuple(
            sorted(
                {
                    (group_index, name)
                    for group_index, group in enumerate(groups)
                    for fact_index in group
                    for name in _identity_names(facts[fact_index])
                }
            )
        )
        if not rows:
            return frozenset()

        table_name = f"_mic_{uuid4().hex}"
        index_name = f"ix_{table_name}_trgm"
        self._session.execute(
            text(
                f"CREATE TEMPORARY TABLE {table_name} ("
                "group_index integer NOT NULL, normalized_name text NOT NULL, "
                "PRIMARY KEY (group_index, normalized_name)) ON COMMIT DROP"
            )
        )
        self._session.execute(
            text(
                f"INSERT INTO pg_temp.{table_name} "
                "(group_index, normalized_name) "
                "VALUES (:group_index, :normalized_name)"
            ),
            [
                {"group_index": group_index, "normalized_name": name}
                for group_index, name in rows
            ],
        )
        self._session.execute(
            text(
                f"CREATE INDEX {index_name} ON pg_temp.{table_name} "
                "USING gist (normalized_name public.gist_trgm_ops)"
            )
        )
        self._session.execute(text(f"ANALYZE pg_temp.{table_name}"))
        recalled = self._session.execute(
            text(
                "SELECT source.group_index AS source_group_index, "
                "source.normalized_name AS source_name, "
                "neighbor.group_index AS neighbor_group_index, "
                "neighbor.normalized_name AS neighbor_name "
                f"FROM pg_temp.{table_name} AS source "
                "JOIN LATERAL ("
                "SELECT target.group_index, target.normalized_name "
                f"FROM pg_temp.{table_name} AS target "
                "WHERE target.group_index <> source.group_index "
                "ORDER BY target.normalized_name <-> source.normalized_name, "
                "target.normalized_name, target.group_index LIMIT 20"
                ") AS neighbor ON TRUE "
                "ORDER BY source.group_index, source.normalized_name, "
                "neighbor.normalized_name, neighbor.group_index"
            )
        )

        review_groups: set[int] = set()
        for row in recalled:
            if fuzz.ratio(row.source_name, row.neighbor_name) >= _FUZZY_REVIEW_THRESHOLD:
                review_groups.update((row.source_group_index, row.neighbor_group_index))
        return frozenset(
            fact_index
            for group_index in review_groups
            for fact_index in groups[group_index]
        )

    def existing_owner_ids(self, names: frozenset[str]) -> set[UUID]:
        if not self.available:
            return set()
        return {
            match.company_id
            for match in self._repository.find_similar_names_sync(
                names,
                limit=_MAX_SIMILARITY_CANDIDATES,
            )
        }


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
    return frozenset(f"name:{name}" for name in _identity_names(fact))


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


def _recruitment_review_indexes(
    facts: tuple[CandidateFact, ...], groups: tuple[tuple[int, ...], ...]
) -> frozenset[int]:
    group_indexes_by_identity: dict[str, set[int]] = {}
    for group_index, group in enumerate(groups):
        for fact_index in group:
            identity = _recruitment_identity(facts[fact_index].recruitment_url)
            if identity is not None:
                group_indexes_by_identity.setdefault(identity, set()).add(group_index)
    conflicting_groups = {
        group_index
        for group_indexes in group_indexes_by_identity.values()
        if len(group_indexes) > 1
        for group_index in group_indexes
    }
    return frozenset(
        fact_index
        for group_index in conflicting_groups
        for fact_index in groups[group_index]
    )


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


def _exact_name_owners(
    repository: SqlAlchemyCompanyIdentityRepository,
    names: frozenset[str],
) -> dict[str, set[UUID]]:
    owners: dict[str, set[UUID]] = {}
    ordered_names = sorted(names)
    for offset in range(0, len(ordered_names), _EXACT_NAME_QUERY_CHUNK):
        chunk = frozenset(ordered_names[offset : offset + _EXACT_NAME_QUERY_CHUNK])
        for owner in repository.find_exact_name_owners_sync(chunk):
            owners.setdefault(owner.normalized_name, set()).add(owner.company_id)
    return owners


def _recruitment_url_predicate(
    column: InstrumentedAttribute[str | None] | ColumnElement[str | None],
    value: str,
) -> tuple[str, ColumnElement[bool]] | None:
    evidence = _recruitment_evidence(value)
    normalized = _normalized_url(value)
    if evidence.identity is None or normalized is None:
        return None
    if evidence.identity.startswith("url:"):
        return evidence.identity, column == normalized

    parts = urlsplit(normalized)
    path_parts = tuple(part for part in parts.path.split("/") if part)
    if parts.hostname in _GREENHOUSE_HOSTS and path_parts[:1] == ("embed",):
        tenant = evidence.identity.rsplit(":", 1)[-1]
        prefix_path = f"/{tenant}"
    elif parts.hostname is not None and parts.hostname.endswith(".myworkdaysite.com"):
        prefix_path = "/" + "/".join(path_parts[:2])
    elif parts.hostname is not None and parts.hostname.endswith(".myworkdayjobs.com"):
        prefix_path = "/"
    else:
        prefix_path = "/" + "/".join(path_parts[:1])
    prefix = urlunsplit((parts.scheme, parts.netloc, prefix_path, "", ""))
    return evidence.identity, or_(column == normalized, column.like(f"{prefix}%"))


def _targeted_recruitment_owner_ids(
    session: Session,
    facts: Sequence[CandidateFact],
) -> set[UUID] | None:
    predicates_by_identity: dict[str, list[ColumnElement[bool]]] = {}
    accepted_predicates_by_identity: dict[str, list[ColumnElement[bool]]] = {}
    for fact in facts:
        if fact.recruitment_url is None:
            continue
        job_predicate = _recruitment_url_predicate(
            JobEntry.normalized_url,
            fact.recruitment_url,
        )
        accepted_predicate = _recruitment_url_predicate(
            CandidateFact.recruitment_url,
            fact.recruitment_url,
        )
        if job_predicate is not None:
            identity, predicate = job_predicate
            predicates_by_identity.setdefault(identity, []).append(predicate)
        if accepted_predicate is not None:
            identity, predicate = accepted_predicate
            accepted_predicates_by_identity.setdefault(identity, []).append(predicate)

    owner_ids: set[UUID] = set()
    for identity in sorted(set(predicates_by_identity) | set(accepted_predicates_by_identity)):
        job_rows = tuple(
            session.execute(
                select(JobEntry.company_id, JobEntry.normalized_url)
                .where(or_(*predicates_by_identity.get(identity, ())))
                .order_by(JobEntry.company_id, JobEntry.id)
                .limit(_MAX_CONTEXT_ROWS + 1)
            )
        ) if identity in predicates_by_identity else ()
        accepted_rows = tuple(
            session.execute(
                select(CandidateFact.company_id, CandidateFact.recruitment_url)
                .where(
                    CandidateFact.decision_status == CandidateDecisionStatus.ACCEPTED,
                    CandidateFact.company_id.is_not(None),
                    or_(*accepted_predicates_by_identity.get(identity, ())),
                )
                .order_by(CandidateFact.company_id, CandidateFact.id)
                .limit(_MAX_CONTEXT_ROWS + 1)
            )
        ) if identity in accepted_predicates_by_identity else ()
        if len(job_rows) > _MAX_CONTEXT_ROWS or len(accepted_rows) > _MAX_CONTEXT_ROWS:
            return None
        owner_ids.update(
            row.company_id
            for row in job_rows
            if _recruitment_identity(row.normalized_url) == identity
        )
        owner_ids.update(
            row.company_id
            for row in accepted_rows
            if row.company_id is not None
            and _recruitment_identity(row.recruitment_url) == identity
        )
    return owner_ids


def _fact_owner_ids(
    fact: CandidateFact,
    name_owners: dict[str, set[UUID]],
) -> set[UUID]:
    return {
        company_id
        for name in _identity_names(fact)
        for company_id in name_owners.get(name, ())
    }


def _group_owner_ids(
    facts: Sequence[CandidateFact],
    name_owners: dict[str, set[UUID]],
) -> set[UUID]:
    return {
        owner_id
        for fact in facts
        for owner_id in _fact_owner_ids(fact, name_owners)
    }


def _bounded_company_context(
    session: Session,
    company_id: UUID,
) -> tuple[tuple[CandidateFact, ...], tuple[JobEntry, ...]] | None:
    accepted_facts = tuple(
        session.scalars(
            select(CandidateFact)
            .where(
                CandidateFact.decision_status == CandidateDecisionStatus.ACCEPTED,
                CandidateFact.company_id == company_id,
            )
            .order_by(CandidateFact.stable_evidence_id)
            .limit(_MAX_CONTEXT_ROWS + 1)
        )
    )
    entries = tuple(
        session.scalars(
            select(JobEntry)
            .where(JobEntry.company_id == company_id)
            .order_by(JobEntry.id)
            .limit(_MAX_CONTEXT_ROWS + 1)
        )
    )
    if len(accepted_facts) > _MAX_CONTEXT_ROWS or len(entries) > _MAX_CONTEXT_ROWS:
        return None
    return accepted_facts, entries


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


def auto_resolve_candidates(
    session: Session,
    *,
    similarity: _ManifestIdentitySimilarity | None = None,
) -> IdentityResolutionSummary:
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
        groups = _exact_groups(facts)
        repository = SqlAlchemyCompanyIdentityRepository(session)
        similarity_backend = similarity or _PostgreSQLManifestIdentitySimilarity(session)
        similarity_available = similarity_backend.available
        candidate_names = frozenset(
            name for fact in facts for name in _identity_names(fact)
        )
        name_owners = _exact_name_owners(repository, candidate_names)
        fuzzy_review_indexes = (
            similarity_backend.candidate_review_indexes(facts, groups)
            if similarity_available
            else frozenset()
        )
        recruitment_review_indexes = _recruitment_review_indexes(facts, groups)
        auto_accepted = 0

        for group in groups:
            group_facts = tuple(facts[index] for index in group)
            group_names = frozenset(
                name for fact in group_facts for name in _identity_names(fact)
            )
            owner_ids = _group_owner_ids(group_facts, name_owners)
            recruitment_owner_ids = _targeted_recruitment_owner_ids(session, group_facts)
            context_facts: Sequence[CandidateFact] = group_facts
            context_entries: Sequence[JobEntry] = ()
            context_available = True
            if len(owner_ids) == 1:
                company_id = next(iter(owner_ids))
                stored_context = _bounded_company_context(session, company_id)
                if stored_context is None:
                    context_available = False
                else:
                    accepted_facts, context_entries = stored_context
                    context_facts = (*group_facts, *accepted_facts)
            categories = {fact.primary_category for fact in context_facts}
            fuzzy_existing_owner_ids = (
                similarity_backend.existing_owner_ids(group_names)
                if not owner_ids and similarity_available
                else set()
            )
            if (
                (not similarity_available and not owner_ids)
                or any(index in fuzzy_review_indexes for index in group)
                or any(index in recruitment_review_indexes for index in group)
                or not context_available
                or len(categories) != 1
                or _has_separable_recruiting_inventories(
                    context_facts, context_entries
                )
                or _has_ambiguous_recruitment_evidence(
                    context_facts, context_entries
                )
                or len(owner_ids) > 1
                or recruitment_owner_ids is None
                or not recruitment_owner_ids.issubset(owner_ids)
                or not fuzzy_existing_owner_ids.issubset(owner_ids)
                or not _aliases_fit_storage(group_facts)
            ):
                continue

            if owner_ids:
                company = session.get(Company, next(iter(owner_ids)))
                if company is None:
                    continue
            else:
                company = _create_company(session, group_facts)
            _upsert_aliases(session, company, group_facts)
            for fact in group_facts:
                fact.company_id = company.id
                fact.decision_status = CandidateDecisionStatus.ACCEPTED
            for name in group_names:
                name_owners.setdefault(name, set()).add(company.id)
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
    repository = SqlAlchemyCompanyIdentityRepository(session)
    names = _identity_names(fact)
    owner_ids = _fact_owner_ids(fact, _exact_name_owners(repository, names))
    recruitment_owner_ids = _targeted_recruitment_owner_ids(session, (fact,))
    if recruitment_owner_ids is None:
        raise ReviewDecisionConflict("candidate recruitment evidence exceeds review bounds")
    owner_ids.update(recruitment_owner_ids)

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

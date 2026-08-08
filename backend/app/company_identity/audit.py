"""Deterministic, read-only audit of persisted company identity history."""

import asyncio
import json
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.company_identity.contracts import (
    IdentityAuditFinding,
    IdentityAuditReport,
    IdentityAuditSeverity,
    IdentityReviewReason,
    IdentityReviewStatus,
)
from app.company_identity.models import CompanyIdentityReviewItem
from app.company_identity.repository import CompanyIdentityRepository
from app.core.normalization import normalize_name, normalize_public_identity_url
from app.manifest.contracts import CandidateDecisionStatus
from app.manifest.identity import _recruitment_identity
from app.manifest.models import CandidateFact
from app.models import Company, CompanyAlias, JobEntry, RegulatoryFiling

_SIMILARITY_THRESHOLD = Decimal(90)
_SIMILARITY_LIMIT = 20
_MAX_FINDING_COMPANIES = 100
_MAX_FINDINGS = 10_000
_REASONS_PROVING_NO_EXACT_OWNER = frozenset(
    {
        IdentityReviewReason.FUZZY_NAME_NEIGHBOR.value,
        IdentityReviewReason.SIMILARITY_SEARCH_UNAVAILABLE.value,
    }
)
_AMBIGUOUS_EXACT_OWNER_REASON = IdentityReviewReason.AMBIGUOUS_EXACT_OWNER.value

_SEVERITY_BY_CODE = {
    "cross_table_name_owner": IdentityAuditSeverity.CRITICAL,
    "shared_website_identity": IdentityAuditSeverity.CRITICAL,
    "incompatible_recruitment_identities": IdentityAuditSeverity.CRITICAL,
    "accepted_candidate_name_unrepresented": IdentityAuditSeverity.IMPORTANT,
    "fuzzy_name_cluster": IdentityAuditSeverity.IMPORTANT,
    "orphan_alias": IdentityAuditSeverity.IMPORTANT,
    "pending_review_owner_changed": IdentityAuditSeverity.IMPORTANT,
    "similarity_search_unavailable": IdentityAuditSeverity.IMPORTANT,
    "alias_normalized_drift": IdentityAuditSeverity.MINOR,
    "canonical_name_normalized_drift": IdentityAuditSeverity.MINOR,
    "filing_number_normalized_drift": IdentityAuditSeverity.MINOR,
    "website_normalized_drift": IdentityAuditSeverity.MINOR,
    "audit_findings_truncated": IdentityAuditSeverity.CRITICAL,
}

_ACTION_BY_CODE = {
    "cross_table_name_owner": "review_name_ownership",
    "shared_website_identity": "review_company_identity",
    "incompatible_recruitment_identities": "review_recruitment_identity",
    "accepted_candidate_name_unrepresented": "review_candidate_history",
    "fuzzy_name_cluster": "review_name_similarity",
    "orphan_alias": "review_alias_owner",
    "pending_review_owner_changed": "rerun_pending_review",
    "similarity_search_unavailable": "enable_similarity_search",
    "alias_normalized_drift": "review_alias_normalization",
    "canonical_name_normalized_drift": "review_canonical_normalization",
    "filing_number_normalized_drift": "review_filing_normalization",
    "website_normalized_drift": "review_website_normalization",
    "audit_findings_truncated": "review_complete_audit_export",
}

_SEVERITY_ORDER = {severity: index for index, severity in enumerate(IdentityAuditSeverity)}


@dataclass(frozen=True)
class _FindingSpec:
    code: str
    company_ids: tuple[UUID, ...]
    identity_key: str
    display_names: tuple[str, ...]
    evidence_codes: tuple[str, ...]


@dataclass(frozen=True)
class _RepositorySnapshot:
    exact_owners: dict[str, frozenset[UUID]]
    fuzzy_edges: frozenset[tuple[UUID, UUID]]
    fuzzy_display_names: dict[UUID, tuple[str, ...]]


@dataclass(frozen=True)
class _CompanyRow:
    id: UUID
    canonical_name: str
    normalized_name: str
    website: str | None
    normalized_website: str


@dataclass(frozen=True)
class _AliasRow:
    id: UUID
    company_id: UUID
    alias: str
    normalized_alias: str


@dataclass(frozen=True)
class _PendingReviewRow:
    id: UUID
    candidate_name: str
    normalized_name: str
    aliases: list[str]
    review_reasons: list[str]


@dataclass(frozen=True)
class _JobEntryRow:
    company_id: UUID
    normalized_url: str


@dataclass(frozen=True)
class _AcceptedFactRow:
    stable_evidence_id: str
    company_id: UUID | None
    canonical_name: str
    normalized_name: str
    aliases: list[str]
    recruitment_url: str | None


@dataclass(frozen=True)
class _FilingRow:
    id: UUID
    company_id: UUID
    filing_number: str
    normalized_filing_number: str


class CompanyIdentityAuditService:
    """Build a stable public report without changing database or ORM state."""

    def __init__(self, session: Session, repository: CompanyIdentityRepository) -> None:
        self._session = session
        self._repository = repository

    def build(self) -> IdentityAuditReport:
        with self._session.no_autoflush:
            company_rows = tuple(
                _CompanyRow(*row)
                for row in self._session.execute(
                    select(
                        Company.id,
                        Company.canonical_name,
                        Company.normalized_name,
                        Company.website,
                        Company.normalized_website,
                    ).order_by(Company.id)
                )
            )
            alias_rows = tuple(
                _AliasRow(*row)
                for row in self._session.execute(
                    select(
                        CompanyAlias.id,
                        CompanyAlias.company_id,
                        CompanyAlias.alias,
                        CompanyAlias.normalized_alias,
                    ).order_by(CompanyAlias.id)
                )
            )
            pending_rows = tuple(
                _PendingReviewRow(*row)
                for row in self._session.execute(
                    select(
                        CompanyIdentityReviewItem.id,
                        CompanyIdentityReviewItem.candidate_name,
                        CompanyIdentityReviewItem.normalized_name,
                        CompanyIdentityReviewItem.aliases,
                        CompanyIdentityReviewItem.review_reasons,
                    )
                    .where(CompanyIdentityReviewItem.status == IdentityReviewStatus.PENDING)
                    .order_by(CompanyIdentityReviewItem.id)
                )
            )
            job_entry_rows = tuple(
                _JobEntryRow(*row)
                for row in self._session.execute(
                    select(JobEntry.company_id, JobEntry.normalized_url).order_by(
                        JobEntry.company_id, JobEntry.id
                    )
                )
            )
            accepted_fact_rows = tuple(
                _AcceptedFactRow(*row)
                for row in self._session.execute(
                    select(
                        CandidateFact.stable_evidence_id,
                        CandidateFact.company_id,
                        CandidateFact.canonical_name,
                        CandidateFact.normalized_name,
                        CandidateFact.aliases,
                        CandidateFact.recruitment_url,
                    )
                    .where(
                        CandidateFact.decision_status == CandidateDecisionStatus.ACCEPTED,
                        CandidateFact.company_id.is_not(None),
                    )
                    .order_by(CandidateFact.stable_evidence_id)
                )
            )
            filing_rows = tuple(
                _FilingRow(*row)
                for row in self._session.execute(
                    select(
                        RegulatoryFiling.id,
                        RegulatoryFiling.company_id,
                        RegulatoryFiling.filing_number,
                        RegulatoryFiling.normalized_filing_number,
                    ).order_by(RegulatoryFiling.id)
                )
            )
            cross_table_names = tuple(
                self._session.scalars(
                    select(Company.normalized_name)
                    .join(
                        CompanyAlias,
                        CompanyAlias.normalized_alias == Company.normalized_name,
                    )
                    .where(CompanyAlias.company_id != Company.id)
                    .distinct()
                    .order_by(Company.normalized_name)
                )
            )
            shared_websites = tuple(
                self._session.scalars(
                    select(Company.normalized_website)
                    .where(Company.normalized_website != "")
                    .group_by(Company.normalized_website)
                    .having(func.count(func.distinct(Company.id)) > 1)
                    .order_by(Company.normalized_website)
                )
            )

            name_owners = _name_owners(company_rows, alias_rows)
            pending_names = {
                name
                for pending_row in pending_rows
                for name in _review_names(pending_row.normalized_name, pending_row.aliases)
            }
            similarity_available = self._repository.similarity_search_available()
            repository_snapshot = _read_repository_snapshot_sync(
                self._repository,
                name_owners=name_owners,
                pending_names=frozenset(pending_names),
                similarity_available=similarity_available,
            )

        specs: list[_FindingSpec] = []
        display_by_company = {
            company_row.id: _safe_display_values((company_row.canonical_name,))
            for company_row in company_rows
        }
        company_ids = frozenset(display_by_company)

        for normalized_name in cross_table_names:
            cross_table_owners = name_owners.get(normalized_name, frozenset())
            _append_specs(
                specs,
                code="cross_table_name_owner",
                company_ids=cross_table_owners,
                identity_key=normalized_name,
                display_names=(),
                evidence_codes=("canonical_name", "company_alias"),
                display_names_by_company=display_by_company,
            )

        companies_by_website: dict[str, set[UUID]] = {}
        for company_row in company_rows:
            if company_row.normalized_website:
                companies_by_website.setdefault(company_row.normalized_website, set()).add(
                    company_row.id
                )
        for website in shared_websites:
            website_owners = companies_by_website.get(website, set())
            sanitized_website = _sanitized_url(website)
            _append_specs(
                specs,
                code="shared_website_identity",
                company_ids=website_owners,
                identity_key=website,
                display_names=((sanitized_website,) if sanitized_website is not None else ()),
                evidence_codes=("normalized_website",),
                display_names_by_company=display_by_company,
            )

        for cluster in _connected_components(repository_snapshot.fuzzy_edges):
            cluster_display_by_company = {
                company_id: (
                    *display_by_company.get(company_id, ()),
                    *repository_snapshot.fuzzy_display_names.get(company_id, ()),
                )
                for company_id in cluster
            }
            _append_specs(
                specs,
                code="fuzzy_name_cluster",
                company_ids=cluster,
                identity_key="|".join(str(company_id) for company_id in cluster),
                display_names=(),
                evidence_codes=("bounded_similarity",),
                display_names_by_company=cluster_display_by_company,
            )

        if company_rows and not similarity_available:
            _append_specs(
                specs,
                code="similarity_search_unavailable",
                company_ids=company_ids,
                identity_key="repository_capability",
                display_names=(),
                evidence_codes=("similarity_capability",),
            )

        recruitment_by_company: dict[UUID, dict[str, str | None]] = {}
        recruitment_evidence_by_company: dict[UUID, set[str]] = {}
        for job_entry_row in job_entry_rows:
            _add_recruitment_identity(
                recruitment_by_company,
                recruitment_evidence_by_company,
                job_entry_row.company_id,
                job_entry_row.normalized_url,
                evidence_code="job_entry",
            )
        for accepted_fact_row in accepted_fact_rows:
            if accepted_fact_row.company_id is not None:
                _add_recruitment_identity(
                    recruitment_by_company,
                    recruitment_evidence_by_company,
                    accepted_fact_row.company_id,
                    accepted_fact_row.recruitment_url,
                    evidence_code="accepted_candidate_fact",
                )
        for company_id, identities in sorted(
            recruitment_by_company.items(), key=lambda item: str(item[0])
        ):
            if len(identities) <= 1:
                continue
            urls = tuple(url for url in identities.values() if url is not None)
            _append_specs(
                specs,
                code="incompatible_recruitment_identities",
                company_ids=(company_id,),
                identity_key="|".join(sorted(identities)),
                display_names=(*display_by_company.get(company_id, ()), *urls),
                evidence_codes=tuple(sorted(recruitment_evidence_by_company[company_id])),
            )

        for company_row in company_rows:
            canonical_normalized = normalize_name(company_row.canonical_name)
            if canonical_normalized != company_row.normalized_name:
                _append_specs(
                    specs,
                    code="canonical_name_normalized_drift",
                    company_ids=(company_row.id,),
                    identity_key=(f"{canonical_normalized}|{company_row.normalized_name}"),
                    display_names=(company_row.canonical_name,),
                    evidence_codes=("canonical_name", "normalized_name"),
                )
            expected_website = _normalized_public_url(company_row.website)
            if expected_website != company_row.normalized_website:
                _append_specs(
                    specs,
                    code="website_normalized_drift",
                    company_ids=(company_row.id,),
                    identity_key=(
                        f"{expected_website or '<invalid>'}|{company_row.normalized_website}"
                    ),
                    display_names=(
                        *display_by_company.get(company_row.id, ()),
                        *tuple(
                            value
                            for value in (
                                _sanitized_url(company_row.website),
                                _sanitized_url(company_row.normalized_website),
                            )
                            if value is not None
                        ),
                    ),
                    evidence_codes=("website", "normalized_website"),
                )

        for alias_row in alias_rows:
            expected_alias = normalize_name(alias_row.alias)
            if expected_alias != alias_row.normalized_alias:
                _append_specs(
                    specs,
                    code="alias_normalized_drift",
                    company_ids=(alias_row.company_id,),
                    identity_key=(f"{alias_row.id}|{expected_alias}|{alias_row.normalized_alias}"),
                    display_names=(alias_row.alias,),
                    evidence_codes=("company_alias", "normalized_alias"),
                )
            if alias_row.company_id not in company_ids:
                _append_specs(
                    specs,
                    code="orphan_alias",
                    company_ids=(alias_row.company_id,),
                    identity_key=str(alias_row.id),
                    display_names=(alias_row.alias,),
                    evidence_codes=("company_alias", "missing_company"),
                )

        represented_names_by_company = {
            company_id: {name for name, owners in name_owners.items() if company_id in owners}
            for company_id in company_ids
        }
        for accepted_fact_row in accepted_fact_rows:
            if accepted_fact_row.company_id is None:
                continue
            fact_names = _review_names(accepted_fact_row.normalized_name, accepted_fact_row.aliases)
            missing_names = fact_names - represented_names_by_company.get(
                accepted_fact_row.company_id, set()
            )
            if missing_names:
                _append_specs(
                    specs,
                    code="accepted_candidate_name_unrepresented",
                    company_ids=(accepted_fact_row.company_id,),
                    identity_key=(
                        f"{accepted_fact_row.stable_evidence_id}|{'|'.join(sorted(missing_names))}"
                    ),
                    display_names=(
                        *display_by_company.get(accepted_fact_row.company_id, ()),
                        accepted_fact_row.canonical_name,
                    ),
                    evidence_codes=("accepted_candidate_fact", "current_name_ownership"),
                )

        for filing_row in filing_rows:
            expected_number = normalize_name(filing_row.filing_number)
            if expected_number != filing_row.normalized_filing_number:
                _append_specs(
                    specs,
                    code="filing_number_normalized_drift",
                    company_ids=(filing_row.company_id,),
                    identity_key=(
                        f"{filing_row.id}|{expected_number}|{filing_row.normalized_filing_number}"
                    ),
                    display_names=display_by_company.get(filing_row.company_id, ()),
                    evidence_codes=("filing_number", "normalized_filing_number"),
                )

        for pending_row in pending_rows:
            review_names = _review_names(pending_row.normalized_name, pending_row.aliases)
            current_owners = frozenset(
                owner
                for name in review_names
                for owner in repository_snapshot.exact_owners.get(name, ())
            )
            review_reasons = frozenset(pending_row.review_reasons)
            proves_no_prior_exact_owner = bool(
                _REASONS_PROVING_NO_EXACT_OWNER.intersection(review_reasons)
            )
            was_ambiguous = _AMBIGUOUS_EXACT_OWNER_REASON in review_reasons
            owner_cardinality_changed = (
                (proves_no_prior_exact_owner and bool(current_owners))
                or (was_ambiguous and len(current_owners) == 1)
                or (not was_ambiguous and len(current_owners) > 1)
            )
            if owner_cardinality_changed:
                _append_specs(
                    specs,
                    code="pending_review_owner_changed",
                    company_ids=current_owners,
                    identity_key=(f"{pending_row.id}|{'|'.join(sorted(review_names))}"),
                    display_names=(pending_row.candidate_name,),
                    evidence_codes=("pending_review", "current_name_ownership"),
                    display_names_by_company=display_by_company,
                )

        findings = [_finding(spec) for spec in specs]
        findings.sort(key=_finding_order)
        if len(findings) > _MAX_FINDINGS:
            retained = findings[: _MAX_FINDINGS - 1]
            overflow_ids = tuple(sorted(company_ids, key=str)[:_MAX_FINDING_COMPANIES])
            if not overflow_ids:
                overflow_ids = findings[_MAX_FINDINGS - 1].company_ids[:1]
            retained.append(
                _finding(
                    _FindingSpec(
                        code="audit_findings_truncated",
                        company_ids=overflow_ids,
                        identity_key=str(len(findings)),
                        display_names=(),
                        evidence_codes=("report_bound",),
                    )
                )
            )
            findings = sorted(retained, key=_finding_order)

        counts = {
            severity: sum(finding.severity is severity for finding in findings)
            for severity in IdentityAuditSeverity
        }
        return IdentityAuditReport(
            findings=tuple(findings),
            scanned_companies=len(company_rows),
            scanned_aliases=len(alias_rows),
            scanned_review_items=len(pending_rows),
            finding_counts=counts,
        )


async def _read_repository_snapshot(
    repository: CompanyIdentityRepository,
    *,
    name_owners: dict[str, frozenset[UUID]],
    pending_names: frozenset[str],
    similarity_available: bool,
) -> _RepositorySnapshot:
    exact_rows = await repository.find_exact_name_owners(pending_names)
    exact_owners: dict[str, set[UUID]] = {}
    for owner in exact_rows:
        exact_owners.setdefault(owner.normalized_name, set()).add(owner.company_id)

    fuzzy_edges: set[tuple[UUID, UUID]] = set()
    fuzzy_display_names: dict[UUID, set[str]] = {}
    if similarity_available:
        for source_name in sorted(name_owners):
            matches = await repository.find_similar_names(
                frozenset({source_name}), limit=_SIMILARITY_LIMIT
            )
            source_owners = name_owners[source_name]
            for match in matches:
                if match.score < _SIMILARITY_THRESHOLD:
                    continue
                fuzzy_display_names.setdefault(match.company_id, set()).add(match.canonical_name)
                for source_owner in source_owners:
                    if source_owner == match.company_id:
                        continue
                    left, right = sorted((source_owner, match.company_id), key=str)
                    fuzzy_edges.add((left, right))
    return _RepositorySnapshot(
        exact_owners={name: frozenset(owners) for name, owners in exact_owners.items()},
        fuzzy_edges=frozenset(fuzzy_edges),
        fuzzy_display_names={
            company_id: _safe_display_values(names)
            for company_id, names in fuzzy_display_names.items()
        },
    )


def _name_owners(
    company_rows: Sequence[_CompanyRow], alias_rows: Sequence[_AliasRow]
) -> dict[str, frozenset[UUID]]:
    owners: dict[str, set[UUID]] = {}
    for company_row in company_rows:
        names = {
            company_row.normalized_name,
            normalize_name(company_row.canonical_name),
        }
        for name in names:
            if name:
                owners.setdefault(name, set()).add(company_row.id)
    for alias_row in alias_rows:
        names = {alias_row.normalized_alias, normalize_name(alias_row.alias)}
        for name in names:
            if name:
                owners.setdefault(name, set()).add(alias_row.company_id)
    return {name: frozenset(owner_ids) for name, owner_ids in owners.items()}


def _review_names(normalized_name: str, aliases: object) -> set[str]:
    names = {normalized_name}
    if isinstance(aliases, list):
        names.update(normalize_name(alias) for alias in aliases if isinstance(alias, str))
    return {name for name in names if name}


def _add_recruitment_identity(
    identities_by_company: dict[UUID, dict[str, str | None]],
    evidence_by_company: dict[UUID, set[str]],
    company_id: UUID,
    value: str | None,
    *,
    evidence_code: str,
) -> None:
    identity = _recruitment_identity(value)
    if identity is None:
        return
    identities_by_company.setdefault(company_id, {}).setdefault(identity, _sanitized_url(value))
    evidence_by_company.setdefault(company_id, set()).add(evidence_code)


def _connected_components(edges: Iterable[tuple[UUID, UUID]]) -> tuple[tuple[UUID, ...], ...]:
    neighbors: dict[UUID, set[UUID]] = {}
    for left, right in edges:
        neighbors.setdefault(left, set()).add(right)
        neighbors.setdefault(right, set()).add(left)
    components: list[tuple[UUID, ...]] = []
    unseen = set(neighbors)
    while unseen:
        start = min(unseen, key=str)
        pending = [start]
        component: set[UUID] = set()
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(sorted(neighbors.get(current, ()), key=str, reverse=True))
        unseen.difference_update(component)
        components.append(tuple(sorted(component, key=str)))
    return tuple(sorted(components, key=lambda component: tuple(map(str, component))))


def _append_specs(
    specs: list[_FindingSpec],
    *,
    code: str,
    company_ids: Iterable[UUID],
    identity_key: str,
    display_names: Iterable[str],
    evidence_codes: tuple[str, ...],
    display_names_by_company: dict[UUID, tuple[str, ...]] | None = None,
) -> None:
    ordered_ids = tuple(sorted(set(company_ids), key=str))
    if not ordered_ids:
        return
    common_displays = _safe_display_values(display_names)
    for offset in range(0, len(ordered_ids), _MAX_FINDING_COMPANIES):
        chunk_ids = ordered_ids[offset : offset + _MAX_FINDING_COMPANIES]
        company_displays: tuple[str, ...] = ()
        if display_names_by_company is not None:
            company_displays = tuple(
                display
                for company_id in chunk_ids
                for display in display_names_by_company.get(company_id, ())
            )
        available_company_displays = 100 - len(common_displays)
        chunk_displays = _safe_display_values(company_displays)[:available_company_displays]
        specs.append(
            _FindingSpec(
                code=code,
                company_ids=chunk_ids,
                identity_key=f"{identity_key}|chunk:{offset // _MAX_FINDING_COMPANIES}",
                display_names=_safe_display_values((*chunk_displays, *common_displays)),
                evidence_codes=evidence_codes,
            )
        )


def _finding(spec: _FindingSpec) -> IdentityAuditFinding:
    canonical_bytes = json.dumps(
        {
            "code": spec.code,
            "company_ids": [str(company_id) for company_id in spec.company_ids],
            "evidence_codes": sorted(set(spec.evidence_codes)),
            "identity_key": spec.identity_key,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return IdentityAuditFinding(
        finding_id=sha256(canonical_bytes).hexdigest(),
        code=spec.code,
        severity=_SEVERITY_BY_CODE[spec.code],
        company_ids=spec.company_ids,
        display_names=spec.display_names,
        evidence_codes=spec.evidence_codes,
        recommended_action=_ACTION_BY_CODE[spec.code],
    )


def _finding_order(finding: IdentityAuditFinding) -> tuple[object, ...]:
    return (
        _SEVERITY_ORDER[finding.severity],
        finding.code,
        tuple(str(company_id) for company_id in finding.company_ids),
        finding.finding_id,
    )


def _company_displays(
    company_ids: Iterable[UUID], display_by_company: dict[UUID, tuple[str, ...]]
) -> tuple[str, ...]:
    return tuple(
        display
        for company_id in sorted(set(company_ids), key=str)
        for display in display_by_company.get(company_id, ())
    )


def _safe_display_values(values: Iterable[str]) -> tuple[str, ...]:
    display_by_normalized: dict[str, str] = {}
    for value in values:
        cleaned = unicodedata.normalize("NFKC", value).strip()
        if cleaned.casefold().startswith(("http://", "https://")):
            sanitized_url = _sanitized_url(cleaned)
            if sanitized_url is None:
                continue
            cleaned = sanitized_url
        if (
            not cleaned
            or len(cleaned) > 200
            or "<" in cleaned
            or ">" in cleaned
            or any(unicodedata.category(character).startswith("C") for character in cleaned)
        ):
            continue
        normalized = normalize_name(cleaned)
        existing = display_by_normalized.get(normalized)
        if existing is None or cleaned < existing:
            display_by_normalized[normalized] = cleaned
    return tuple(
        display_by_normalized[normalized] for normalized in sorted(display_by_normalized)[:100]
    )


def _normalized_public_url(value: str | None) -> str | None:
    if value is None:
        return ""
    try:
        return normalize_public_identity_url(value)
    except (TypeError, UnicodeError, ValueError):
        return None


def _sanitized_url(value: str | None) -> str | None:
    normalized = _normalized_public_url(value)
    if not normalized or len(normalized) > 200:
        return None
    parts = urlsplit(normalized)
    if parts.username is not None or parts.password is not None:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _read_repository_snapshot_sync(
    repository: CompanyIdentityRepository,
    *,
    name_owners: dict[str, frozenset[UUID]],
    pending_names: frozenset[str],
    similarity_available: bool,
) -> _RepositorySnapshot:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _read_repository_snapshot(
                repository,
                name_owners=name_owners,
                pending_names=pending_names,
                similarity_available=similarity_available,
            )
        )
    raise RuntimeError("CompanyIdentityAuditService.build cannot run inside an event loop")

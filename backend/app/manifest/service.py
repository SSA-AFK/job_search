"""Transactional freeze service for the immutable Gate 1 company manifest."""

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_url
from app.manifest.allocation import (
    ManifestAllocationError,
    QuotaAllocation,
    ResolvedCandidate,
    allocate_quotas,
    canonical_manifest_bytes,
    select_manifest_members,
)
from app.manifest.contracts import (
    AiCategory,
    AtsClassification,
    CandidateDecisionStatus,
    ConfidenceTier,
    DiscoveryStatus,
    EntryDiscoveryResult,
    ManifestCompany,
    ManifestMemberData,
    RecordDiscoveryCommand,
)
from app.manifest.models import (
    CandidateFact,
    CompanyManifest,
    CompanyManifestMember,
    EntryDiscoveryObservation,
)
from app.models.company import Company
from app.models.enums import JobEntryStatus
from app.models.job_entry import JobEntry


class ManifestFreezeError(ValueError):
    """Raised when the current reviewed pool cannot be frozen."""


class ManifestFreezeConflict(ManifestFreezeError):
    """Raised when a freeze attempt differs from the already frozen manifest."""


class DiscoveryRecordConflict(ValueError):
    """Raised when a discovery command cannot be recorded idempotently."""


@dataclass(frozen=True)
class FrozenManifest:
    manifest_version: str
    config_fingerprint: str
    frozen_at: datetime
    allocation: QuotaAllocation
    members: tuple[ManifestMemberData, ...]
    canonical_bytes: bytes
    manifest_bytes: bytes
    quota_bytes: bytes


@dataclass(frozen=True)
class DiscoveryRecordSummary:
    observation_id: UUID
    job_entry_id: UUID | None
    observation_created: bool
    entry_created: bool


_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
_CONFIDENCE_ORDER = {
    ConfidenceTier.HIGH: 0,
    ConfidenceTier.MEDIUM: 1,
    ConfidenceTier.LOW: 2,
}


def _result_values(result: EntryDiscoveryResult) -> tuple[object, ...]:
    classification = result.classification
    return (
        result.method,
        result.status,
        None if result.candidate_url is None else str(result.candidate_url),
        None if result.normalized_url is None else str(result.normalized_url),
        result.source_id,
        result.ownership_evidence,
        None if classification is None else classification.platform,
        False if classification is None else classification.requires_rendering,
        result.error_code,
    )


def _observation_values(observation: EntryDiscoveryObservation) -> tuple[object, ...]:
    return (
        observation.method,
        observation.status,
        observation.candidate_url,
        observation.normalized_url,
        observation.source_id,
        observation.ownership_evidence,
        observation.platform,
        observation.requires_rendering,
        observation.error_code,
    )


def _accepted_classification(result: EntryDiscoveryResult) -> AtsClassification:
    if (
        result.normalized_url is None
        or result.candidate_url is None
        or result.ownership_evidence is None
        or result.classification is None
    ):
        raise DiscoveryRecordConflict(
            "accepted discovery requires an owned normalized entry classification"
        )
    if normalize_url(str(result.candidate_url)) != str(result.normalized_url):
        raise DiscoveryRecordConflict(
            "accepted discovery candidate and normalized URL identities differ"
        )
    if result.classification.platform == "unknown":
        raise DiscoveryRecordConflict("accepted discovery requires a known platform")
    return result.classification


def _matching_observation(
    observations: Sequence[EntryDiscoveryObservation],
    command: RecordDiscoveryCommand,
) -> EntryDiscoveryObservation | None:
    result = command.result
    normalized_url = None if result.normalized_url is None else str(result.normalized_url)
    same_identity = tuple(
        observation
        for observation in observations
        if (
            observation.normalized_url == normalized_url
            if normalized_url is not None
            else observation.normalized_url is None and observation.method == result.method
        )
    )
    if not same_identity:
        return None
    expected = _result_values(result)
    for observation in same_identity:
        if (
            _observation_values(observation) == expected
            and observation.observed_at == command.observed_at
        ):
            return observation
    raise DiscoveryRecordConflict("discovery observation conflicts with stored result")


def _upsert_discovered_entry(
    session: Session,
    *,
    command: RecordDiscoveryCommand,
    classification: AtsClassification,
) -> tuple[JobEntry, bool]:
    result = command.result
    assert result.normalized_url is not None
    assert result.candidate_url is not None
    normalized_url = str(result.normalized_url)
    entries = tuple(
        session.scalars(
            select(JobEntry)
            .where(JobEntry.normalized_url == normalized_url)
            .order_by(JobEntry.id)
            .with_for_update()
        )
    )
    if any(entry.company_id != command.company_id for entry in entries):
        raise DiscoveryRecordConflict("normalized discovery URL is owned by another company")
    if len(entries) > 1:
        raise DiscoveryRecordConflict("normalized discovery URL has conflicting entries")

    created = not entries
    if created:
        entry = JobEntry(
            company_id=command.company_id,
            url=str(result.candidate_url),
            normalized_url=normalized_url,
            provider="official_entry_discovery",
            platform=classification.platform,
            requires_rendering=classification.requires_rendering,
            status=JobEntryStatus.UNKNOWN,
        )
        session.add(entry)
    else:
        entry = entries[0]
        entry.url = str(result.candidate_url)
        entry.provider = "official_entry_discovery"
        entry.platform = classification.platform
        entry.requires_rendering = classification.requires_rendering
    session.flush()
    return entry, created


def record_discovery_result(
    session: Session,
    command: RecordDiscoveryCommand,
) -> DiscoveryRecordSummary:
    """Atomically persist one manifest discovery result and its owned entry."""

    if session.in_transaction():
        raise DiscoveryRecordConflict("discovery recording requires a clean session")
    classification = (
        _accepted_classification(command.result)
        if command.result.status is DiscoveryStatus.ACCEPTED
        else command.result.classification
    )

    with session.begin():
        manifest = session.scalar(
            select(CompanyManifest)
            .where(CompanyManifest.version == command.manifest_version)
            .with_for_update()
        )
        if manifest is None:
            raise DiscoveryRecordConflict("discovery manifest does not exist")
        member = session.scalar(
            select(CompanyManifestMember)
            .where(
                CompanyManifestMember.manifest_version == command.manifest_version,
                CompanyManifestMember.company_id == command.company_id,
            )
            .with_for_update()
        )
        if member is None:
            raise DiscoveryRecordConflict(
                "discovery company is not a member of the requested manifest"
            )

        observations = tuple(
            session.scalars(
                select(EntryDiscoveryObservation)
                .where(
                    EntryDiscoveryObservation.manifest_version
                    == command.manifest_version,
                    EntryDiscoveryObservation.company_id == command.company_id,
                )
                .order_by(EntryDiscoveryObservation.id)
                .with_for_update()
            )
        )
        replay = _matching_observation(observations, command)
        if replay is not None:
            return DiscoveryRecordSummary(
                observation_id=replay.id,
                job_entry_id=replay.job_entry_id,
                observation_created=False,
                entry_created=False,
            )

        entry: JobEntry | None = None
        entry_created = False
        if command.result.status is DiscoveryStatus.ACCEPTED:
            assert classification is not None
            entry, entry_created = _upsert_discovered_entry(
                session,
                command=command,
                classification=classification,
            )

        result = command.result
        observation = EntryDiscoveryObservation(
            manifest_version=command.manifest_version,
            company_id=command.company_id,
            method=result.method,
            status=result.status,
            candidate_url=(
                None if result.candidate_url is None else str(result.candidate_url)
            ),
            normalized_url=(
                None if result.normalized_url is None else str(result.normalized_url)
            ),
            source_id=result.source_id,
            ownership_evidence=result.ownership_evidence,
            platform=None if classification is None else classification.platform,
            requires_rendering=(
                False if classification is None else classification.requires_rendering
            ),
            error_code=result.error_code,
            job_entry_id=None if entry is None else entry.id,
            observed_at=command.observed_at,
        )
        session.add(observation)
        session.flush()
        return DiscoveryRecordSummary(
            observation_id=observation.id,
            job_entry_id=observation.job_entry_id,
            observation_created=True,
            entry_created=entry_created,
        )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _category_values(values: Mapping[AiCategory, int]) -> dict[str, int]:
    return {category.value: values[category] for category in AiCategory}


def _allocation_data(allocation: QuotaAllocation) -> dict[str, object]:
    return {
        "total": allocation.total,
        "counts": _category_values(allocation.counts),
        "floor": _category_values(allocation.floor),
        "proportional": _category_values(allocation.proportional),
        "final": _category_values(allocation.final),
    }


def _artifact_bytes(
    *,
    manifest_version: str,
    config_fingerprint: str,
    frozen_at: datetime,
    allocation: QuotaAllocation,
    members: Sequence[ManifestMemberData],
) -> tuple[bytes, bytes]:
    member_values = [member.model_dump(mode="json") for member in members]
    timestamp = _utc_z(frozen_at)
    manifest_document = {
        "config_fingerprint": config_fingerprint,
        "frozen_at": timestamp,
        "manifest_version": manifest_version,
        "member_count": len(members),
        "members": member_values,
    }
    quota_document = {
        **_allocation_data(allocation),
        "config_fingerprint": config_fingerprint,
        "frozen_at": timestamp,
        "manifest_version": manifest_version,
    }
    return _canonical_json_bytes(manifest_document), _canonical_json_bytes(quota_document)


def _conflict(message: str, *, cause: Exception | None = None) -> NoReturn:
    error = ManifestFreezeConflict(message)
    if cause is None:
        raise error
    raise error from cause


def _load_resolved_candidates(session: Session) -> tuple[ResolvedCandidate, ...]:
    accepted_facts = tuple(
        session.scalars(
            select(CandidateFact)
            .where(CandidateFact.decision_status == CandidateDecisionStatus.ACCEPTED)
            .order_by(CandidateFact.stable_evidence_id)
            .with_for_update()
        )
    )
    if any(fact.company_id is None for fact in accepted_facts):
        raise ManifestFreezeError("accepted candidate is unresolved")

    company_ids = {fact.company_id for fact in accepted_facts if fact.company_id is not None}
    companies = tuple(
        session.scalars(
            select(Company)
            .where(Company.id.in_(company_ids))
            .order_by(Company.normalized_name, Company.id)
            .with_for_update()
        )
    ) if company_ids else ()
    companies_by_id = {company.id: company for company in companies}
    if len(companies_by_id) != len(company_ids):
        raise ManifestFreezeError("accepted candidate references a missing company identity")

    facts_by_company: dict[UUID, list[CandidateFact]] = defaultdict(list)
    for fact in accepted_facts:
        assert fact.company_id is not None
        facts_by_company[fact.company_id].append(fact)
    if len(facts_by_company) < 1500:
        raise ManifestFreezeError("manifest freeze requires at least 1500 accepted identities")

    resolved: list[ResolvedCandidate] = []
    for company_id in sorted(facts_by_company, key=str):
        facts = facts_by_company[company_id]
        categories = {fact.primary_category for fact in facts}
        if len(categories) != 1:
            raise ManifestFreezeError("accepted company identity has conflicting primary categories")
        representative = min(
            facts,
            key=lambda fact: (
                _CONFIDENCE_ORDER[fact.confidence_tier],
                fact.stable_evidence_id,
            ),
        )
        company = companies_by_id[company_id]
        resolved.append(
            ResolvedCandidate(
                company_id=company_id,
                canonical_name=company.canonical_name,
                normalized_name=company.normalized_name,
                primary_category=representative.primary_category,
                official_website=representative.official_website,
                recruitment_url=representative.recruitment_url,
                confidence_tier=representative.confidence_tier,
                stable_evidence_id=representative.stable_evidence_id,
                scale=company.scale,
                city=company.city,
            )
        )
    return tuple(resolved)


def _build_current_freeze_data(
    session: Session,
) -> tuple[QuotaAllocation, tuple[ManifestMemberData, ...], bytes, str]:
    candidates = _load_resolved_candidates(session)
    counts = {
        category: sum(
            candidate.primary_category is category for candidate in candidates
        )
        for category in AiCategory
    }
    try:
        allocation = allocate_quotas(counts)
        members = select_manifest_members(candidates, allocation)
        canonical_bytes = canonical_manifest_bytes(members)
    except ManifestAllocationError as error:
        raise ManifestFreezeError(str(error)) from error
    if len(members) != 1000:
        raise ManifestFreezeError("manifest persistence requires exactly 1000 members")
    manifest_version = sha256(canonical_bytes).hexdigest()
    return allocation, members, canonical_bytes, manifest_version


def _persisted_members(
    session: Session, manifest_version: str
) -> tuple[ManifestMemberData, ...]:
    rows = tuple(
        session.scalars(
            select(CompanyManifestMember)
            .where(CompanyManifestMember.manifest_version == manifest_version)
            .order_by(CompanyManifestMember.position)
            .with_for_update()
        )
    )
    return tuple(
        ManifestMemberData(
            position=row.position,
            company=ManifestCompany.model_validate(
                {
                    "company_id": row.company_id,
                    "canonical_name": row.canonical_name,
                    "primary_category": row.primary_category,
                    "official_website": row.official_website,
                    "recruitment_url": row.recruitment_url,
                }
            ),
        )
        for row in rows
    )


def _existing_manifest(session: Session) -> CompanyManifest | None:
    manifests = tuple(
        session.scalars(
            select(CompanyManifest)
            .order_by(CompanyManifest.version)
            .with_for_update()
        )
    )
    if len(manifests) > 1:
        _conflict("multiple persisted manifests violate the singleton freeze contract")
    return manifests[0] if manifests else None


def _replay_existing(
    session: Session,
    *,
    existing: CompanyManifest,
    config_fingerprint: str,
    allocation: QuotaAllocation,
    members: tuple[ManifestMemberData, ...],
    canonical_bytes: bytes,
    manifest_version: str,
) -> FrozenManifest:
    persisted_members = _persisted_members(session, existing.version)
    try:
        persisted_bytes = canonical_manifest_bytes(persisted_members)
    except ManifestAllocationError as error:
        _conflict("persisted manifest membership is invalid", cause=error)
    matches = (
        existing.config_fingerprint == config_fingerprint
        and existing.version == manifest_version
        and existing.member_count == 1000
        and existing.canonical_quota == _allocation_data(allocation)
        and persisted_members == members
        and persisted_bytes == canonical_bytes
        and sha256(persisted_bytes).hexdigest() == existing.version
    )
    if not matches:
        _conflict("freeze conflicts with existing manifest")
    manifest_bytes, quota_bytes = _artifact_bytes(
        manifest_version=existing.version,
        config_fingerprint=existing.config_fingerprint,
        frozen_at=existing.frozen_at,
        allocation=allocation,
        members=persisted_members,
    )
    return FrozenManifest(
        manifest_version=existing.version,
        config_fingerprint=existing.config_fingerprint,
        frozen_at=existing.frozen_at,
        allocation=allocation,
        members=persisted_members,
        canonical_bytes=persisted_bytes,
        manifest_bytes=manifest_bytes,
        quota_bytes=quota_bytes,
    )


def freeze_manifest(session: Session, *, config_fingerprint: str) -> FrozenManifest:
    """Freeze exactly one immutable 1,000-company manifest in an owned transaction."""

    if _FINGERPRINT_PATTERN.fullmatch(config_fingerprint) is None:
        raise ManifestFreezeError("config fingerprint must be 64 lowercase hexadecimal characters")
    if session.in_transaction():
        raise ManifestFreezeError("manifest freeze requires a clean session")

    with session.begin():
        try:
            allocation, members, canonical_bytes, manifest_version = _build_current_freeze_data(
                session
            )
        except ManifestFreezeError as error:
            existing = _existing_manifest(session)
            if existing is not None:
                _conflict("current candidate pool conflicts with existing manifest", cause=error)
            raise

        existing = _existing_manifest(session)
        if existing is not None and existing.config_fingerprint != config_fingerprint:
            _conflict("freeze conflicts with existing manifest configuration")
        if existing is not None:
            return _replay_existing(
                session,
                existing=existing,
                config_fingerprint=config_fingerprint,
                allocation=allocation,
                members=members,
                canonical_bytes=canonical_bytes,
                manifest_version=manifest_version,
            )

        frozen_at = datetime.now(UTC)
        session.add(
            CompanyManifest(
                version=manifest_version,
                config_fingerprint=config_fingerprint,
                member_count=1000,
                canonical_quota=_allocation_data(allocation),
                frozen_at=frozen_at,
            )
        )
        session.flush()
        session.add_all(
            [
                CompanyManifestMember(
                    manifest_version=manifest_version,
                    company_id=member.company.company_id,
                    position=member.position,
                    canonical_name=member.company.canonical_name,
                    primary_category=member.company.primary_category,
                    official_website=(
                        None
                        if member.company.official_website is None
                        else str(member.company.official_website)
                    ),
                    recruitment_url=(
                        None
                        if member.company.recruitment_url is None
                        else str(member.company.recruitment_url)
                    ),
                )
                for member in members
            ]
        )
        session.flush()
        manifest_bytes, quota_bytes = _artifact_bytes(
            manifest_version=manifest_version,
            config_fingerprint=config_fingerprint,
            frozen_at=frozen_at,
            allocation=allocation,
            members=members,
        )
        return FrozenManifest(
            manifest_version=manifest_version,
            config_fingerprint=config_fingerprint,
            frozen_at=frozen_at,
            allocation=allocation,
            members=members,
            canonical_bytes=canonical_bytes,
            manifest_bytes=manifest_bytes,
            quota_bytes=quota_bytes,
        )

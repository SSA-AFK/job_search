"""Idempotent persistence for auditable candidate evidence."""

import json
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from hashlib import sha256
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.normalization import normalize_name, normalize_url
from app.manifest.contracts import (
    CandidateDecisionStatus,
    CandidateFactInput,
    ConfidenceTier,
    SourceClass,
    SourceRegistry,
    SourceRegistryEntry,
    SourceRole,
)
from app.manifest.models import CandidateFact


class UnregisteredSourceError(ValueError):
    """Raised when candidate evidence cites a source outside the reviewed registry."""


class CandidateEvidenceConflict(ValueError):
    """Raised when a stable evidence identity is already bound to different facts."""


@dataclass(frozen=True)
class CandidateImportSummary:
    created: int
    replayed: int


@dataclass
class _ImportCounts:
    created: int = 0
    replayed: int = 0


_HIGH_CONFIDENCE_SOURCE_CLASSES = frozenset(
    {
        SourceClass.GOVERNMENT,
        SourceClass.EXCHANGE,
        SourceClass.ASSOCIATION,
        SourceClass.INDUSTRIAL_PARK,
    }
)
_CANONICAL_NAME_STORAGE_LIMIT = 200
_NORMALIZED_NAME_STORAGE_LIMIT = 255


def _validated_fact(value: CandidateFactInput) -> CandidateFactInput:
    """Revalidate instances so model_construct cannot bypass input limits."""

    return CandidateFactInput.model_validate(value.model_dump(mode="json"))


def _display_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _storage_safe_names(value: CandidateFactInput) -> tuple[str, str]:
    canonical_name = _display_name(value.canonical_name)
    normalized_name = normalize_name(canonical_name)
    if len(canonical_name) > _CANONICAL_NAME_STORAGE_LIMIT:
        raise ValueError("canonical candidate name exceeds storage limit")
    if len(normalized_name) > _NORMALIZED_NAME_STORAGE_LIMIT:
        raise ValueError("normalized candidate name exceeds storage limit")
    return canonical_name, normalized_name


def _canonical_aliases(value: CandidateFactInput) -> tuple[str, ...]:
    aliases_by_normalized_name: dict[str, str] = {}
    for alias in value.aliases:
        display_alias = _display_name(alias)
        normalized_alias = normalize_name(display_alias)
        aliases_by_normalized_name[normalized_alias] = min(
            display_alias,
            aliases_by_normalized_name.get(normalized_alias, display_alias),
        )
    return tuple(
        aliases_by_normalized_name[key] for key in sorted(aliases_by_normalized_name)
    )


def _public_fact_values(value: CandidateFactInput) -> dict[str, object]:
    fact = _validated_fact(value)
    return {
        "source_id": fact.source_id,
        "source_url": normalize_url(str(fact.source_url)),
        "retrieved_at": fact.retrieved_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "normalized_name": normalize_name(fact.canonical_name),
        "aliases": sorted(normalize_name(alias) for alias in _canonical_aliases(fact)),
        "primary_category": fact.primary_category.value,
        "official_website": (
            None
            if fact.official_website is None
            else normalize_url(str(fact.official_website))
        ),
        "recruitment_url": (
            None
            if fact.recruitment_url is None
            else normalize_url(str(fact.recruitment_url))
        ),
        "evidence_summary": fact.evidence_summary,
    }


def _source_url_is_in_scope(source_url: str, source: SourceRegistryEntry) -> bool:
    evidence = urlsplit(source_url)
    registered = urlsplit(normalize_url(str(source.base_url)))
    if (evidence.scheme, evidence.netloc) != (registered.scheme, registered.netloc):
        return False
    base_path = registered.path.rstrip("/") or "/"
    if base_path == "/":
        return True
    return evidence.path == base_path or evidence.path.startswith(f"{base_path}/")


def canonical_candidate_fact(value: CandidateFactInput) -> bytes:
    """Serialize normalized, public evidence into a stable hash input."""

    return json.dumps(
        _public_fact_values(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def stable_evidence_id(value: CandidateFactInput) -> str:
    return sha256(canonical_candidate_fact(value)).hexdigest()


def classify_candidate_confidence(
    value: CandidateFactInput, source: SourceRegistryEntry
) -> tuple[ConfidenceTier, str]:
    """Derive confidence only from reviewed source provenance and evidence."""

    source_class = source.source_class
    if source_class is SourceClass.AUTHORIZED_API:
        return (
            ConfidenceTier.LOW,
            "authorized_api source is an authorized API fallback",
        )
    if source_class is SourceClass.OFFICIAL_COMPANY_SITE:
        return (
            ConfidenceTier.MEDIUM,
            "official_company_site source is an official company site",
        )
    if source_class in _HIGH_CONFIDENCE_SOURCE_CLASSES and value.official_website is not None:
        return (
            ConfidenceTier.HIGH,
            f"{source_class.value} source includes an official website",
        )
    return (
        ConfidenceTier.MEDIUM,
        f"{source_class.value} source does not include an official website",
    )


class CandidateImporter:
    def __init__(self, session: Session, registry: SourceRegistry) -> None:
        self.session = session
        self.registry = registry
        self.counts = _ImportCounts()

    def import_all(self, facts: tuple[CandidateFactInput, ...]) -> CandidateImportSummary:
        for supplied_fact in facts:
            self._import_one(_validated_fact(supplied_fact))
        return CandidateImportSummary(**vars(self.counts))

    def _import_one(self, fact: CandidateFactInput) -> None:
        try:
            source = self.registry.require(fact.source_id)
        except KeyError as error:
            raise UnregisteredSourceError("candidate source is not registered") from error
        if SourceRole.CANDIDATE_POOL not in source.roles:
            raise UnregisteredSourceError("candidate source is not authorized for candidate imports")

        source_url = normalize_url(str(fact.source_url))
        if not _source_url_is_in_scope(source_url, source):
            raise UnregisteredSourceError("candidate source URL is outside registered scope")

        public_values = _public_fact_values(fact)
        canonical_name, normalized_name = _storage_safe_names(fact)
        evidence_id = stable_evidence_id(fact)
        existing = self.session.scalar(
            select(CandidateFact).where(CandidateFact.stable_evidence_id == evidence_id)
        )
        if existing is not None:
            if not self._matches_public_fact(existing, public_values):
                raise CandidateEvidenceConflict("candidate evidence identity conflicts with stored facts")
            self.counts.replayed += 1
            return

        confidence_tier, confidence_reason = classify_candidate_confidence(fact, source)
        self.session.add(
            CandidateFact(
                stable_evidence_id=evidence_id,
                canonical_name=canonical_name,
                normalized_name=normalized_name,
                aliases=list(_canonical_aliases(fact)),
                primary_category=fact.primary_category,
                official_website=public_values["official_website"],
                recruitment_url=public_values["recruitment_url"],
                source_id=fact.source_id,
                source_url=str(public_values["source_url"]),
                retrieved_at=fact.retrieved_at,
                evidence_summary=fact.evidence_summary,
                confidence_tier=confidence_tier,
                confidence_reason=confidence_reason,
                decision_status=CandidateDecisionStatus.REVIEW_REQUIRED,
            )
        )
        self.session.flush()
        self.counts.created += 1

    @staticmethod
    def _matches_public_fact(existing: CandidateFact, public_values: dict[str, object]) -> bool:
        return (
            existing.source_id == public_values["source_id"]
            and existing.source_url == public_values["source_url"]
            and existing.retrieved_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            == public_values["retrieved_at"]
            and existing.normalized_name == public_values["normalized_name"]
            and sorted(normalize_name(alias) for alias in existing.aliases)
            == public_values["aliases"]
            and existing.primary_category.value == public_values["primary_category"]
            and existing.official_website == public_values["official_website"]
            and existing.recruitment_url == public_values["recruitment_url"]
            and existing.evidence_summary == public_values["evidence_summary"]
        )


def import_candidate_facts(
    session: Session,
    facts: Iterable[CandidateFactInput],
    registry: SourceRegistry,
) -> CandidateImportSummary:
    """Atomically import reviewed-source candidate evidence without auto-merging identities."""

    with session.begin():
        return CandidateImporter(session, registry).import_all(tuple(facts))

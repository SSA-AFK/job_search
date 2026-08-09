"""Compatibility adapter for exact-only company identity resolution."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.company_identity.contracts import CompanyIdentityInput
from app.company_identity.repository import CompanyIdentityRepository
from app.company_identity.resolver import CompanyIdentityResolver
from app.ingestion.extraction.schemas import CompanyCandidate


@dataclass(frozen=True)
class CompanyForComparison:
    """Legacy comparison row retained for import compatibility only."""

    company_id: UUID
    normalized_name: str


class CompanyMatchKind(StrEnum):
    EXISTING = "existing"
    NEW = "new"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True)
class CompanyMatch:
    kind: CompanyMatchKind
    company_id: UUID | None


class CompanyDeduplicationRepository(CompanyIdentityRepository, Protocol):
    pass


class CompanyDeduplicator:
    def __init__(self, repository: CompanyDeduplicationRepository) -> None:
        self._resolver = CompanyIdentityResolver(repository)

    async def resolve(self, candidate: CompanyCandidate) -> CompanyMatch:
        identity = CompanyIdentityInput(
            canonical_name=candidate.name,
            aliases=candidate.aliases,
            official_website=None if candidate.website is None else str(candidate.website),
        )
        resolution = await self._resolver.resolve(identity)
        return CompanyMatch(
            kind=CompanyMatchKind(resolution.kind.value),
            company_id=resolution.company_id,
        )

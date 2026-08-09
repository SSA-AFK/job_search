"""Exact-only company identity decisions with review-only fuzzy evidence."""

from hashlib import sha256
from uuid import UUID

from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityResolution,
    IdentityResolutionKind,
    IdentityReviewReason,
    _canonical_identity_bytes,
)
from app.company_identity.repository import CompanyIdentityRepository

_MAX_CANDIDATES = 20
_SHORT_NAME_LENGTH = 4


class CompanyIdentityResolver:
    def __init__(self, repository: CompanyIdentityRepository) -> None:
        self._repository = repository

    async def resolve(self, identity: CompanyIdentityInput) -> CompanyIdentityResolution:
        candidate = CompanyIdentityInput.model_validate(identity.model_dump())
        stable_hash = sha256(_canonical_identity_bytes(candidate)).hexdigest()
        names = frozenset((candidate.normalized_name, *candidate.normalized_aliases))
        exact_owners = await self._repository.find_exact_name_owners(names)
        exact_owner_ids = frozenset(owner.company_id for owner in exact_owners)
        reasons = await self._evidence_conflict_reasons(candidate, exact_owner_ids)

        if len(exact_owner_ids) > 1:
            reasons.add(IdentityReviewReason.AMBIGUOUS_EXACT_OWNER)
            return _review_resolution(stable_hash, reasons=reasons)

        if len(exact_owner_ids) == 1:
            if reasons:
                return _review_resolution(stable_hash, reasons=reasons)
            return CompanyIdentityResolution(
                kind=IdentityResolutionKind.EXISTING,
                company_id=next(iter(exact_owner_ids)),
                stable_identity_hash=stable_hash,
            )

        if not self._repository.similarity_search_available():
            reasons.add(IdentityReviewReason.SIMILARITY_SEARCH_UNAVAILABLE)
            return _review_resolution(stable_hash, reasons=reasons)

        similar = await self._repository.find_similar_names(names, limit=_MAX_CANDIDATES)
        candidates = tuple(sorted(similar, key=_candidate_order)[:_MAX_CANDIDATES])
        if candidates:
            reasons.add(IdentityReviewReason.FUZZY_NAME_NEIGHBOR)
            if any(len(name) <= _SHORT_NAME_LENGTH for name in names):
                reasons.add(IdentityReviewReason.SHORT_NAME_COLLISION)
        if reasons:
            return _review_resolution(stable_hash, reasons=reasons, candidates=candidates)
        return CompanyIdentityResolution(
            kind=IdentityResolutionKind.NEW,
            stable_identity_hash=stable_hash,
        )

    async def _evidence_conflict_reasons(
        self,
        identity: CompanyIdentityInput,
        exact_owner_ids: frozenset[UUID],
    ) -> set[IdentityReviewReason]:
        projections: tuple[tuple[CompanyIdentityInput, IdentityReviewReason], ...] = ()
        if identity.official_website is not None:
            projections += (
                (
                    CompanyIdentityInput(
                        canonical_name=identity.canonical_name,
                        official_website=identity.official_website,
                    ),
                    IdentityReviewReason.WEBSITE_IDENTITY_CONFLICT,
                ),
            )
        if identity.recruitment_identity is not None:
            projections += (
                (
                    CompanyIdentityInput(
                        canonical_name=identity.canonical_name,
                        recruitment_identity=identity.recruitment_identity,
                    ),
                    IdentityReviewReason.RECRUITMENT_IDENTITY_CONFLICT,
                ),
            )
        if identity.legal_identifiers:
            projections += (
                (
                    CompanyIdentityInput(
                        canonical_name=identity.canonical_name,
                        legal_identifiers=identity.legal_identifiers,
                    ),
                    IdentityReviewReason.LEGAL_IDENTITY_CONFLICT,
                ),
            )

        reasons: set[IdentityReviewReason] = set()
        sole_exact_owner = next(iter(exact_owner_ids)) if len(exact_owner_ids) == 1 else None
        for projection, reason in projections:
            evidence_owners = await self._repository.find_evidence_owner_ids(projection)
            if evidence_owners and (
                sole_exact_owner is None or evidence_owners != frozenset({sole_exact_owner})
            ):
                reasons.add(reason)
        return reasons


def _candidate_order(
    candidate: CompanyIdentityCandidateMatch,
) -> tuple[object, str, str]:
    return (-candidate.score, candidate.normalized_name, str(candidate.company_id))


def _review_resolution(
    stable_hash: str,
    *,
    reasons: set[IdentityReviewReason],
    candidates: tuple[CompanyIdentityCandidateMatch, ...] = (),
) -> CompanyIdentityResolution:
    return CompanyIdentityResolution(
        kind=IdentityResolutionKind.REVIEW_REQUIRED,
        stable_identity_hash=stable_hash,
        candidate_matches=candidates,
        review_reasons=tuple(reasons),
    )

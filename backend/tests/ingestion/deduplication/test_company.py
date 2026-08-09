import asyncio
from decimal import Decimal
from uuid import UUID

import pytest

from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityNameOwner,
)
from app.ingestion.deduplication.company import CompanyDeduplicator
from app.ingestion.extraction.schemas import CompanyCandidate

COMPANY_A = UUID("00000000-0000-0000-0000-000000000001")
COMPANY_B = UUID("00000000-0000-0000-0000-000000000002")


class FakeCompanyRepository:
    def __init__(
        self,
        *,
        exact: dict[str, UUID] | None = None,
        similar: tuple[CompanyIdentityCandidateMatch, ...] = (),
        similarity_available: bool = True,
    ) -> None:
        self.exact = exact or {}
        self.similar = similar
        self.similarity_available = similarity_available

    async def find_exact_name_owners(
        self, names: frozenset[str]
    ) -> tuple[CompanyIdentityNameOwner, ...]:
        return tuple(
            CompanyIdentityNameOwner(company_id=self.exact[name], normalized_name=name)
            for name in sorted(names)
            if name in self.exact
        )

    async def find_evidence_owner_ids(
        self, identity: CompanyIdentityInput
    ) -> frozenset[UUID]:
        return frozenset()

    async def find_similar_names(
        self, names: frozenset[str], *, limit: int
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        assert limit == 20
        return self.similar

    def similarity_search_available(self) -> bool:
        return self.similarity_available


def fuzzy_match(company_id: UUID, canonical_name: str) -> CompanyIdentityCandidateMatch:
    return CompanyIdentityCandidateMatch(
        company_id=company_id,
        canonical_name=canonical_name,
        normalized_name=canonical_name,
        match_kind="fuzzy_canonical",
        score=Decimal("80.0"),
    )


def company_candidate(name: str, *, aliases: tuple[str, ...] = ()) -> CompanyCandidate:
    return CompanyCandidate(
        name=name,
        aliases=aliases,
        evidence_ids=["doc-1"],
        confidence=0.9,
    )


def test_company_alias_exact_match_wins() -> None:
    repository = FakeCompanyRepository(exact={"openaichina": COMPANY_B})

    match = asyncio.run(
        CompanyDeduplicator(repository).resolve(
            company_candidate("OpenAI Group", aliases=("OpenAI China",))
        )
    )

    assert match.kind == "existing"
    assert match.company_id == COMPANY_B


@pytest.mark.parametrize("name", ["abcdefghxx", "prefixtide", "OpenAI Group"])
def test_company_fuzzy_match_never_becomes_existing(name: str) -> None:
    repository = FakeCompanyRepository(
        similar=(fuzzy_match(COMPANY_A, "abcdefghij"),)
    )

    match = asyncio.run(CompanyDeduplicator(repository).resolve(company_candidate(name)))

    assert match.kind == "review_required"
    assert match.company_id is None

def test_company_without_candidates_is_new_when_similarity_is_available() -> None:
    match = asyncio.run(
        CompanyDeduplicator(FakeCompanyRepository()).resolve(company_candidate("New Co"))
    )

    assert match.kind == "new"
    assert match.company_id is None


def test_company_similarity_unavailable_is_review_required() -> None:
    match = asyncio.run(
        CompanyDeduplicator(
            FakeCompanyRepository(similarity_available=False)
        ).resolve(company_candidate("Unknown Co"))
    )

    assert match.kind == "review_required"
    assert match.company_id is None

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityNameOwner,
    IdentityResolutionKind,
    IdentityReviewReason,
)
from app.company_identity.repository import (
    CompanyIdentityRepository,
    SqlAlchemyCompanyIdentityRepository,
)
from app.company_identity.resolver import CompanyIdentityResolver

COMPANY_A = UUID("00000000-0000-0000-0000-000000000001")
COMPANY_B = UUID("00000000-0000-0000-0000-000000000002")
COMPANY_C = UUID("00000000-0000-0000-0000-000000000003")


class FakeCompanyIdentityRepository:
    def __init__(
        self,
        *,
        exact_owners: dict[str, frozenset[UUID]] | None = None,
        website_owners: frozenset[UUID] = frozenset(),
        recruitment_owners: frozenset[UUID] = frozenset(),
        legal_owners: frozenset[UUID] = frozenset(),
        similar: tuple[CompanyIdentityCandidateMatch, ...] = (),
        similarity_available: bool = True,
    ) -> None:
        self.exact_owners = exact_owners or {}
        self.website_owners = website_owners
        self.recruitment_owners = recruitment_owners
        self.legal_owners = legal_owners
        self.similar = similar
        self.similarity_available = similarity_available
        self.evidence_projections: list[CompanyIdentityInput] = []
        self.similarity_calls: list[tuple[frozenset[str], int]] = []

    async def find_exact_name_owners(
        self, names: frozenset[str]
    ) -> tuple[CompanyIdentityNameOwner, ...]:
        return tuple(
            CompanyIdentityNameOwner(company_id=company_id, normalized_name=name)
            for name in sorted(names)
            for company_id in sorted(self.exact_owners.get(name, frozenset()), key=str)
        )

    async def find_evidence_owner_ids(
        self, identity: CompanyIdentityInput
    ) -> frozenset[UUID]:
        self.evidence_projections.append(identity)
        if identity.official_website is not None:
            return self.website_owners
        if identity.recruitment_identity is not None:
            return self.recruitment_owners
        if identity.legal_identifiers:
            return self.legal_owners
        raise AssertionError("resolver supplied an empty evidence projection")

    async def find_similar_names(
        self, names: frozenset[str], *, limit: int
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        self.similarity_calls.append((names, limit))
        return self.similar

    def similarity_search_available(self) -> bool:
        return self.similarity_available


def identity(
    name: str,
    *,
    aliases: tuple[str, ...] = (),
    official_website: str | None = None,
    recruitment_identity: str | None = None,
    legal_identifiers: tuple[str, ...] = (),
    city: str | None = None,
) -> CompanyIdentityInput:
    return CompanyIdentityInput(
        canonical_name=name,
        aliases=aliases,
        official_website=official_website,
        recruitment_identity=recruitment_identity,
        legal_identifiers=legal_identifiers,
        city=city,
    )


def match(
    company_id: UUID,
    canonical_name: str,
    *,
    score: str,
    match_kind: str = "fuzzy_canonical",
) -> CompanyIdentityCandidateMatch:
    return CompanyIdentityCandidateMatch(
        company_id=company_id,
        canonical_name=canonical_name,
        normalized_name=canonical_name,
        match_kind=match_kind,
        score=Decimal(score),
    )


def resolve(
    repository: CompanyIdentityRepository, candidate: CompanyIdentityInput
):
    return asyncio.run(CompanyIdentityResolver(repository).resolve(candidate))


def test_unique_exact_canonical_owner_auto_links() -> None:
    repository = FakeCompanyIdentityRepository(
        exact_owners={"openai": frozenset({COMPANY_A})}
    )

    result = resolve(repository, identity("OpenAI"))

    assert result.kind is IdentityResolutionKind.EXISTING
    assert result.company_id == COMPANY_A
    assert repository.similarity_calls == []


def test_unique_exact_alias_is_the_only_noncanonical_auto_link() -> None:
    repository = FakeCompanyIdentityRepository(
        exact_owners={"openaichina": frozenset({COMPANY_A})}
    )

    result = resolve(repository, identity("Unrelated", aliases=("OpenAI China",)))

    assert result.kind is IdentityResolutionKind.EXISTING
    assert result.company_id == COMPANY_A


def test_multiple_exact_owners_require_review_without_uuid_selection() -> None:
    repository = FakeCompanyIdentityRepository(
        exact_owners={"openai": frozenset({COMPANY_B, COMPANY_A})}
    )

    result = resolve(repository, identity("OpenAI"))

    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.company_id is None
    assert result.review_reasons == (IdentityReviewReason.AMBIGUOUS_EXACT_OWNER,)
    assert repository.similarity_calls == []


def test_evidence_owned_only_by_unique_exact_owner_does_not_defeat_exact_link() -> None:
    repository = FakeCompanyIdentityRepository(
        exact_owners={"openai": frozenset({COMPANY_A})},
        website_owners=frozenset({COMPANY_A}),
    )

    result = resolve(
        repository,
        identity("OpenAI", official_website="https://openai.com/"),
    )

    assert result.kind is IdentityResolutionKind.EXISTING
    assert result.company_id == COMPANY_A


def test_evidence_owned_by_different_exact_name_owner_requires_review() -> None:
    repository = FakeCompanyIdentityRepository(
        exact_owners={"openai": frozenset({COMPANY_A})},
        website_owners=frozenset({COMPANY_B}),
    )

    result = resolve(
        repository,
        identity("OpenAI", official_website="https://example.com/"),
    )

    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.company_id is None
    assert result.review_reasons == (IdentityReviewReason.WEBSITE_IDENTITY_CONFLICT,)


@pytest.mark.parametrize("candidate_name", ["Open Al", "OpenAI China", "OpenAI Group"])
def test_fuzzy_names_require_review_without_auto_link(candidate_name: str) -> None:
    repository = FakeCompanyIdentityRepository(
        similar=(match(COMPANY_A, "OpenAI", score="88.0"),)
    )

    result = resolve(repository, identity(candidate_name))

    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.company_id is None
    assert result.review_reasons == (IdentityReviewReason.FUZZY_NAME_NEIGHBOR,)


def test_short_name_neighbor_adds_short_name_collision_reason() -> None:
    repository = FakeCompanyIdentityRepository(
        similar=(match(COMPANY_A, "AI Lab", score="57.1"),)
    )

    result = resolve(repository, identity("AI"))

    assert result.review_reasons == (
        IdentityReviewReason.FUZZY_NAME_NEIGHBOR,
        IdentityReviewReason.SHORT_NAME_COLLISION,
    )


def test_tied_fuzzy_candidates_stay_review_and_never_select_by_uuid() -> None:
    repository = FakeCompanyIdentityRepository(
        similar=(
            match(COMPANY_B, "Beta", score="90.0"),
            match(COMPANY_A, "Alpha", score="90.0"),
        )
    )

    result = resolve(repository, identity("Alphabeta"))

    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.company_id is None
    assert tuple(candidate.company_id for candidate in result.candidate_matches) == (
        COMPANY_A,
        COMPANY_B,
    )


@pytest.mark.parametrize(
    ("candidate", "owners_field", "expected_reason"),
    (
        (
            identity("Example", official_website="https://example.com/"),
            "website_owners",
            IdentityReviewReason.WEBSITE_IDENTITY_CONFLICT,
        ),
        (
            identity("Example", recruitment_identity="tenant:example"),
            "recruitment_owners",
            IdentityReviewReason.RECRUITMENT_IDENTITY_CONFLICT,
        ),
        (
            identity("Example", legal_identifiers=("CN-123",)),
            "legal_owners",
            IdentityReviewReason.LEGAL_IDENTITY_CONFLICT,
        ),
    ),
)
def test_evidence_owners_require_review_without_auto_link(
    candidate: CompanyIdentityInput,
    owners_field: str,
    expected_reason: IdentityReviewReason,
) -> None:
    repository = FakeCompanyIdentityRepository(**{owners_field: frozenset({COMPANY_A})})

    result = resolve(repository, candidate)

    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.company_id is None
    assert result.review_reasons == (expected_reason,)


def test_multiple_evidence_fields_are_projected_and_attributed_independently() -> None:
    repository = FakeCompanyIdentityRepository(
        website_owners=frozenset({COMPANY_A}),
        legal_owners=frozenset({COMPANY_B}),
    )
    candidate = identity(
        "Example",
        official_website="https://example.com/",
        recruitment_identity="tenant:unowned",
        legal_identifiers=("CN-123",),
        city="Shanghai",
    )

    result = resolve(repository, candidate)

    assert result.review_reasons == (
        IdentityReviewReason.LEGAL_IDENTITY_CONFLICT,
        IdentityReviewReason.WEBSITE_IDENTITY_CONFLICT,
    )
    assert len(repository.evidence_projections) == 3
    assert sum(item.official_website is not None for item in repository.evidence_projections) == 1
    assert sum(item.recruitment_identity is not None for item in repository.evidence_projections) == 1
    assert sum(bool(item.legal_identifiers) for item in repository.evidence_projections) == 1
    assert all(item.city is None for item in repository.evidence_projections)


def test_no_candidates_with_available_similarity_is_new() -> None:
    result = resolve(FakeCompanyIdentityRepository(), identity("Brand New Company"))

    assert result.kind is IdentityResolutionKind.NEW
    assert result.company_id is None


def test_unavailable_similarity_fails_closed_to_review() -> None:
    repository = FakeCompanyIdentityRepository(similarity_available=False)

    result = resolve(repository, identity("Unknown Company"))

    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.review_reasons == (IdentityReviewReason.SIMILARITY_SEARCH_UNAVAILABLE,)
    assert repository.similarity_calls == []


def test_missing_postgresql_trigram_capability_fails_closed_to_review() -> None:
    from sqlalchemy.dialects import postgresql

    class MissingTrigramSession:
        bind = SimpleNamespace(dialect=postgresql.dialect())

        def execute(self, _statement: object):
            return ()

        def scalar(self, _statement: object) -> bool:
            return False

    repository = SqlAlchemyCompanyIdentityRepository(MissingTrigramSession())  # type: ignore[arg-type]

    result = resolve(repository, identity("Unknown Company"))

    assert result.kind is IdentityResolutionKind.REVIEW_REQUIRED
    assert result.review_reasons == (IdentityReviewReason.SIMILARITY_SEARCH_UNAVAILABLE,)


def test_resolver_enforces_deterministic_top_twenty_order() -> None:
    candidates = tuple(
        match(
            UUID(f"00000000-0000-0000-0000-{number:012d}"),
            f"Company {number:02d}",
            score=str(100 - number),
        )
        for number in range(25, 0, -1)
    )

    result = resolve(
        FakeCompanyIdentityRepository(similar=candidates),
        identity("Company"),
    )

    assert len(result.candidate_matches) == 20
    assert tuple(candidate.score for candidate in result.candidate_matches) == tuple(
        Decimal(str(score)) for score in range(99, 79, -1)
    )

import asyncio
from uuid import UUID

import pytest

from app.ingestion.deduplication.company import CompanyDeduplicator, CompanyForComparison
from app.ingestion.extraction.schemas import CompanyCandidate


@pytest.fixture
def company_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def alias_company_id() -> UUID:
    return UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def repository(company_id: UUID, alias_company_id: UUID) -> "FakeCompanyRepository":
    return FakeCompanyRepository(
        exact={"openai": company_id, "openaichina": alias_company_id},
        comparisons=(CompanyForComparison(company_id, "abcdefghij"),),
    )


@pytest.fixture
def deduplicator(repository: "FakeCompanyRepository") -> CompanyDeduplicator:
    return CompanyDeduplicator(repository)


def test_company_alias_exact_match_wins(
    alias_company_id: UUID, deduplicator: CompanyDeduplicator
) -> None:
    match = asyncio.run(deduplicator.resolve(company_candidate("OpenAI China")))

    assert match.kind == "existing"
    assert match.company_id == alias_company_id


@pytest.mark.parametrize(
    ("name", "expected_kind"),
    [
        ("abcdefghxx", "existing"),  # 80.0% similarity: include the threshold.
        ("abcdefgxxx", "new"),  # 70.0% similarity: below the threshold.
    ],
)
def test_company_fuzzy_match_uses_an_inclusive_80_percent_threshold(
    name: str, expected_kind: str, deduplicator: CompanyDeduplicator
) -> None:
    match = asyncio.run(deduplicator.resolve(company_candidate(name)))

    assert match.kind == expected_kind


def company_candidate(name: str) -> CompanyCandidate:
    return CompanyCandidate(name=name, evidence_ids=["doc-1"], confidence=0.9)


class FakeCompanyRepository:
    def __init__(
        self, *, exact: dict[str, UUID], comparisons: tuple[CompanyForComparison, ...]
    ) -> None:
        self._exact = exact
        self._comparisons = comparisons

    async def find_by_normalized_name_or_alias(self, normalized_name: str) -> UUID | None:
        return self._exact.get(normalized_name)

    async def list_for_deduplication(self) -> tuple[CompanyForComparison, ...]:
        return self._comparisons

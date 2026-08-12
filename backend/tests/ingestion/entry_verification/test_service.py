from unittest.mock import AsyncMock

import pytest

from app.ingestion.entry_discovery.contracts import CompanyNamePool
from app.ingestion.entry_verification.contracts import (
    EntryVerificationBudget,
    EntryVerificationResult,
    EntryVerificationStatus,
)
from app.ingestion.entry_verification.service import (
    EntryCandidateInput,
    EntryVerificationService,
)
from app.ingestion.entry_verification.validator import EntryUrlValidator


def result(url: str, status: EntryVerificationStatus) -> EntryVerificationResult:
    return EntryVerificationResult(
        candidate_url=url,
        final_url=url,
        status=status,
        reason_code=None if status is EntryVerificationStatus.VERIFIED else "not_found",
    )


@pytest.mark.anyio
async def test_existing_success_short_circuits_without_search() -> None:
    validator = AsyncMock(spec=EntryUrlValidator)
    validator.verify.return_value = result(
        "https://acme.example/careers", EntryVerificationStatus.VERIFIED
    )
    search = AsyncMock(return_value=[EntryCandidateInput("https://search.example/jobs")])
    service = EntryVerificationService(validator=validator)

    found = await service.find_verified_entry(
        company=CompanyNamePool(canonical_name="Acme"),
        existing=[EntryCandidateInput("https://acme.example/careers")],
        search=search,
    )

    assert found is not None and found.status is EntryVerificationStatus.VERIFIED
    search.assert_not_awaited()
    assert validator.verify.await_count == 1


@pytest.mark.anyio
async def test_search_runs_once_and_duplicate_urls_are_not_rechecked() -> None:
    validator = AsyncMock(spec=EntryUrlValidator)
    validator.verify.return_value = result(
        "https://acme.example/careers", EntryVerificationStatus.UNAVAILABLE
    )
    search = AsyncMock(
        return_value=[
            EntryCandidateInput("https://acme.example/careers/"),
            EntryCandidateInput("https://jobs.example/acme"),
        ]
    )
    service = EntryVerificationService(validator=validator)

    await service.find_verified_entry(
        company=CompanyNamePool(canonical_name="Acme"),
        existing=[EntryCandidateInput("https://acme.example/careers")],
        search=search,
    )

    assert search.await_count == 1
    assert validator.verify.await_count == 2


@pytest.mark.anyio
async def test_candidate_budget_stops_before_fourth_candidate() -> None:
    validator = AsyncMock(spec=EntryUrlValidator)
    validator.verify.side_effect = [
        result(f"https://example.com/jobs/{index}", EntryVerificationStatus.UNAVAILABLE)
        for index in range(3)
    ]
    service = EntryVerificationService(
        validator=validator, budget=EntryVerificationBudget(max_candidates=3)
    )

    found = await service.find_verified_entry(
        company=CompanyNamePool(canonical_name="Acme"),
        existing=[
            EntryCandidateInput(f"https://example.com/jobs/{index}")
            for index in range(4)
        ],
    )

    assert found is not None and found.reason_code == "budget_exhausted"
    assert validator.verify.await_count == 3

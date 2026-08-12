import pytest
from pydantic import ValidationError

from app.ingestion.entry_verification.contracts import (
    EntryVerificationBudget,
    EntryVerificationResult,
    EntryVerificationStatus,
)


def test_default_budget_is_strictly_bounded() -> None:
    assert EntryVerificationBudget().model_dump() == {
        "max_search_calls": 1,
        "max_candidates": 3,
        "max_http_requests": 5,
    }


def test_verified_result_requires_final_url() -> None:
    with pytest.raises(ValidationError):
        EntryVerificationResult(
            candidate_url="https://example.com/careers",
            status=EntryVerificationStatus.VERIFIED,
        )


def test_failure_rejects_unstable_reason_code() -> None:
    with pytest.raises(ValidationError):
        EntryVerificationResult(
            candidate_url="https://example.com/careers",
            status=EntryVerificationStatus.UNAVAILABLE,
            reason_code="socket exploded with secret detail",
        )

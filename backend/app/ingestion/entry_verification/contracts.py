from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class EntryVerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


STABLE_ENTRY_FAILURE_CODES = frozenset(
    {
        "not_found",
        "unsafe_url",
        "robots_disallowed",
        "request_timeout",
        "access_blocked",
        "login_required",
        "not_recruiting_page",
        "company_ownership_unverified",
        "budget_exhausted",
        "provider_unavailable",
    }
)


class EntryVerificationBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_search_calls: int = Field(default=1, ge=0, le=1)
    max_candidates: int = Field(default=3, ge=1, le=3)
    max_http_requests: int = Field(default=5, ge=1, le=5)


class EntryVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_url: HttpUrl
    final_url: HttpUrl | None = None
    status: EntryVerificationStatus
    reason_code: str | None = Field(default=None, max_length=50)
    http_requests: int = Field(default=0, ge=0)
    ownership_evidence: str | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def validate_reason(self) -> "EntryVerificationResult":
        if self.status is EntryVerificationStatus.VERIFIED:
            if self.reason_code is not None or self.final_url is None:
                raise ValueError("verified result requires final_url and no reason_code")
        elif self.reason_code not in STABLE_ENTRY_FAILURE_CODES:
            raise ValueError("non-verified result requires a stable reason_code")
        return self

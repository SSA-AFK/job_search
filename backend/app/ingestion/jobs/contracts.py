# backend/app/ingestion/jobs/contracts.py
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class AtsParseStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"

class AtsJobCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    external_id: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    raw_attributes: dict[str, str] = Field(default_factory=dict)

class AtsListResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    candidates: tuple[AtsJobCandidate, ...]
    status: AtsParseStatus
    observed_count: int = Field(default=0, ge=0)
    reported_total: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default="parse_failed", max_length=100)
    content_fingerprint: str | None = Field(default=None, max_length=128)

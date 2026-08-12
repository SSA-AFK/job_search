from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class JobEnumerationStatus(StrEnum):
    FRESH_DATABASE_HIT = "fresh_database_hit"
    SOURCE_UNSUPPORTED = "source_unsupported"
    SOURCE_SUCCEEDED = "source_succeeded"
    SOURCE_PARTIAL = "source_partial"
    SOURCE_FAILED = "source_failed"


class ExternalJobCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_provider: str = Field(min_length=1, max_length=50)
    source_raw_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    apply_url: HttpUrl
    job_type: str | None = Field(default=None, max_length=50)
    city: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    requirements: str | None = Field(default=None, max_length=20_000)
    observed_at: datetime


class JobEnumerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: JobEnumerationStatus
    jobs: tuple[ExternalJobCandidate, ...] = ()
    source_key: str | None = Field(default=None, max_length=100)
    pagination_complete: bool = False
    empty_confirmed: bool = False
    error_code: str | None = Field(default=None, max_length=50)
    rejected_records: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_completion(self) -> "JobEnumerationResult":
        if self.status is JobEnumerationStatus.SOURCE_SUCCEEDED and (
            not self.pagination_complete or self.error_code is not None
        ):
            raise ValueError("successful enumeration must be complete without an error")
        if self.empty_confirmed and (self.jobs or not self.pagination_complete):
            raise ValueError("confirmed empty result must be complete and contain no jobs")
        return self

"""Public outcome returned by a completed ingestion run."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.models import CollectionStatus


class RunResultSource(Protocol):
    id: UUID
    status: CollectionStatus
    company_id: UUID | None
    providers_attempted: list[str]
    documents_found: int
    jobs_found: int
    jobs_written: int
    error_code: str | None


@dataclass(frozen=True)
class IngestionResult:
    run_id: UUID
    status: CollectionStatus
    company_id: UUID | None
    providers_attempted: tuple[str, ...]
    documents_found: int
    jobs_found: int
    jobs_written: int
    error_code: str | None

    @classmethod
    def from_run(cls, run: RunResultSource) -> "IngestionResult":
        return cls(
            run_id=run.id,
            status=run.status,
            company_id=run.company_id,
            providers_attempted=tuple(run.providers_attempted),
            documents_found=run.documents_found,
            jobs_found=run.jobs_found,
            jobs_written=run.jobs_written,
            error_code=run.error_code,
        )

    @classmethod
    def unknown_run(cls, run_id: UUID) -> "IngestionResult":
        return cls(
            run_id=run_id,
            status=CollectionStatus.FAILED,
            company_id=None,
            providers_attempted=(),
            documents_found=0,
            jobs_found=0,
            jobs_written=0,
            error_code="run_not_found",
        )

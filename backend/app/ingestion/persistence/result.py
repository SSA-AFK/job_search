"""Results returned after a normalized ingestion batch is persisted."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class PersistenceResult:
    company_id: UUID
    documents_written: int
    jobs_written: int
    warnings: tuple[str, ...]

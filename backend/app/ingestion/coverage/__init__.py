"""Contracts for recording job-list coverage observations."""

from app.ingestion.coverage.contracts import (
    CoverageReport,
    RecordJobSnapshot,
    SnapshotRecordResult,
)
from app.ingestion.coverage.service import CoverageConflict, JobCoverageService

__all__ = [
    "CoverageConflict",
    "CoverageReport",
    "JobCoverageService",
    "RecordJobSnapshot",
    "SnapshotRecordResult",
]

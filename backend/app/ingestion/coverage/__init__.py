"""Contracts for recording job-list coverage observations."""

from app.ingestion.coverage.contracts import (
    CoverageReport,
    RecordJobSnapshot,
    SnapshotRecordResult,
)

__all__ = ["CoverageReport", "RecordJobSnapshot", "SnapshotRecordResult"]

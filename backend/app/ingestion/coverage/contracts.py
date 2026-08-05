"""Immutable validated contracts for job-list coverage collection."""

import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import JobSnapshotStatus

_MAX_SQL_INTEGER = 2_147_483_647
_MAX_SQL_SMALLINT = 32_767
_MAX_SEEN_SOURCE_IDS = 20_000
_RATE_QUANTUM = Decimal("0.0001")


class _FrozenDTO(BaseModel):
    model_config = ConfigDict(frozen=True)


class RecordJobSnapshot(_FrozenDTO):
    entry_id: UUID
    crawl_run_id: UUID
    status: JobSnapshotStatus
    pagination_complete: bool = False
    empty_confirmed: bool = False
    reported_total: int | None = Field(default=None, ge=0, le=_MAX_SQL_INTEGER)
    pages_fetched: int = Field(ge=0, le=_MAX_SQL_SMALLINT)
    content_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, min_length=1, max_length=50)
    started_at: AwareDatetime
    completed_at: AwareDatetime
    seen_source_ids: frozenset[UUID] = Field(
        default_factory=frozenset, max_length=_MAX_SEEN_SOURCE_IDS
    )

    @model_validator(mode="after")
    def validate_snapshot_invariants(self) -> "RecordJobSnapshot":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")

        if self.status is JobSnapshotStatus.SUCCEEDED:
            if not self.pagination_complete:
                raise ValueError("successful snapshot must be complete")
            if self.error_code is not None:
                raise ValueError("successful snapshot cannot have an error_code")
        elif self.status is JobSnapshotStatus.PARTIAL:
            if self.error_code is None:
                raise ValueError("partial snapshot requires error_code")
        elif self.status is JobSnapshotStatus.FAILED:
            if self.error_code is None:
                raise ValueError("failed snapshot requires error_code")
            if self.pagination_complete:
                raise ValueError("failed snapshot cannot be complete")
            if self.seen_source_ids:
                raise ValueError("failed snapshot cannot contain sources")
            if self.pages_fetched != 0:
                raise ValueError("failed snapshot pages_fetched must be zero")

        if self.empty_confirmed:
            if self.status is not JobSnapshotStatus.SUCCEEDED or not self.pagination_complete:
                raise ValueError("empty confirmation requires successful complete snapshot")
            if self.seen_source_ids:
                raise ValueError("confirmed empty snapshot cannot contain sources")
            if self.reported_total != 0:
                raise ValueError("confirmed empty reported_total must be zero")
        return self

    def command_hash(self) -> str:
        """Return a deterministic replay signature for this validated command."""

        payload = {
            "completed_at": _utc_timestamp(self.completed_at),
            "content_fingerprint": self.content_fingerprint,
            "crawl_run_id": str(self.crawl_run_id),
            "empty_confirmed": self.empty_confirmed,
            "entry_id": str(self.entry_id),
            "error_code": self.error_code,
            "pages_fetched": self.pages_fetched,
            "pagination_complete": self.pagination_complete,
            "reported_total": self.reported_total,
            "seen_source_ids": sorted(str(source_id) for source_id in self.seen_source_ids),
            "started_at": _utc_timestamp(self.started_at),
            "status": self.status.value,
        }
        canonical_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return sha256(canonical_json.encode("utf-8")).hexdigest()


class SnapshotRecordResult(_FrozenDTO):
    snapshot_id: UUID
    created: bool
    sources_reactivated: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    sources_missing_incremented: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    sources_deactivated: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    jobs_recomputed: int = Field(ge=0, le=_MAX_SQL_INTEGER)


class CoverageReport(_FrozenDTO):
    as_of: AwareDatetime | None = None
    refresh_window_hours: int = Field(default=24, ge=1, le=_MAX_SQL_INTEGER)
    target_companies: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    active_entry_companies: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    recently_enumerated_companies: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    complete_list_companies: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    confirmed_empty_companies: int = Field(ge=0, le=_MAX_SQL_INTEGER)
    entry_coverage_rate: Decimal | None = None
    enumeration_rate: Decimal | None = None
    completeness_rate: Decimal | None = None
    refresh_slo_rate: Decimal | None = None

    @field_validator(
        "entry_coverage_rate",
        "enumeration_rate",
        "completeness_rate",
        "refresh_slo_rate",
    )
    @classmethod
    def quantize_rate(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if not Decimal(0) <= value <= Decimal(1):
            raise ValueError("coverage rate must be between zero and one")
        return value.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()

"""Minimal contracts for the four ranking enrichment categories."""

from dataclasses import dataclass
from datetime import date

from app.rankings.gap_plan import EnrichmentCategory

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True)
class ProjectedSignal:
    category: EnrichmentCategory
    signal_key: str
    value: dict[str, JsonValue]
    event_date: date | None
    source_fingerprint: str


@dataclass(frozen=True)
class CategoryCollectionResult:
    category: EnrichmentCategory
    response_sha256: str
    signals: tuple[ProjectedSignal, ...]

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import HttpUrl, ValidationError

from app.job_enumeration.contracts import ExternalJobCandidate

_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_RECORDS = 10_000


class BossImportError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class BossImportRecord:
    company_name: str
    brand_id: str | None
    job: ExternalJobCandidate


@dataclass(frozen=True, slots=True)
class BossImportFile:
    fingerprint: str
    records: tuple[BossImportRecord, ...]
    rejected_records: int


def load_boss_json(path: Path, *, observed_at: datetime | None = None) -> BossImportFile:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.suffix.lower() != ".json":
        raise BossImportError("manual_import_invalid")
    payload_bytes = resolved.read_bytes()
    if len(payload_bytes) > _MAX_FILE_BYTES:
        raise BossImportError("manual_import_invalid")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BossImportError("manual_import_invalid") from error
    raw_records = _records(payload)
    if len(raw_records) > _MAX_RECORDS:
        raise BossImportError("manual_import_invalid")
    timestamp = (observed_at or datetime.now(UTC)).astimezone(UTC)
    accepted: list[BossImportRecord] = []
    rejected = 0
    for record in raw_records:
        try:
            accepted.append(_record(record, timestamp))
        except (TypeError, ValueError, ValidationError):
            rejected += 1
    return BossImportFile(
        fingerprint=hashlib.sha256(payload_bytes).hexdigest(),
        records=tuple(accepted),
        rejected_records=rejected,
    )


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [value for value in payload if isinstance(value, dict)]
    if isinstance(payload, dict):
        values = payload.get("jobs", payload.get("data", []))
        if isinstance(values, list):
            return [value for value in values if isinstance(value, dict)]
    raise BossImportError("manual_import_invalid")


def _record(record: dict[str, Any], observed_at: datetime) -> BossImportRecord:
    company_name = _text(record, "company_name", "brand_name", "company")
    source_id = _text(record, "job_id", "encryptJobId", "id")
    title = _text(record, "job_name", "jobName", "title")
    url = _text(record, "job_url", "url")
    if not company_name or not source_id or not title or not url:
        raise ValueError("missing required BOSS fields")
    return BossImportRecord(
        company_name=company_name,
        brand_id=_text(record, "brand_id", "brandId"),
        job=ExternalJobCandidate(
            source_provider="boss_manual",
            source_raw_id=source_id,
            title=title,
            apply_url=HttpUrl(url),
            city=_text(record, "city", "city_name"),
            description=_text(record, "description", "job_desc"),
            observed_at=observed_at,
        ),
    )


def _text(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None

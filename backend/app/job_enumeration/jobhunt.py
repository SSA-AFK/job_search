import asyncio
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import HttpUrl, ValidationError

from app.job_enumeration.contracts import (
    ExternalJobCandidate,
    JobEnumerationResult,
    JobEnumerationStatus,
)

_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_RECORDS = 2_000
_MAX_REJECTED_RATIO = 0.20


class JobHuntError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class JobHuntCli:
    def __init__(
        self,
        *,
        executable: Path,
        expected_version: str,
        timeout_seconds: float = 60.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not executable.is_absolute():
            raise ValueError("JobHunt executable path must be absolute")
        if not expected_version:
            raise ValueError("JobHunt expected version is required")
        self._executable = executable
        self._expected_version = expected_version
        self._timeout_seconds = timeout_seconds
        self._now = now

    async def check_version(self) -> None:
        output = await self._run(("--version",))
        observed = output.strip().removeprefix("v")
        if observed != self._expected_version.removeprefix("v"):
            raise JobHuntError("jobhunt_version_mismatch")

    async def sites(self) -> Mapping[str, frozenset[str]]:
        payload = _json(await self._run(("sites", "--format", "json")))
        records = payload if isinstance(payload, list) else payload.get("sites", [])
        if not isinstance(records, list):
            raise JobHuntError("jobhunt_invalid_output")
        result: dict[str, frozenset[str]] = {}
        for item in records:
            if not isinstance(item, dict):
                continue
            key = _text(item, "key", "site", "code", "id", "command")
            natures = item.get("supported_natures", [])
            if key and isinstance(natures, list):
                result[key] = frozenset(str(value) for value in natures)
        if not result:
            raise JobHuntError("jobhunt_invalid_output")
        return result

    async def enumerate(self, *, site: str, natures: Sequence[str]) -> JobEnumerationResult:
        await self.check_version()
        jobs: list[ExternalJobCandidate] = []
        rejected = 0
        completed_natures = 0
        failed_natures = 0
        for nature in natures:
            try:
                payload = _json(
                    await self._run(
                        (site, "all", "--nature", nature, "--max", str(_MAX_RECORDS), "--format", "json")
                    )
                )
            except JobHuntError:
                failed_natures += 1
                continue
            records, complete = _records(payload, max_records=_MAX_RECORDS)
            if not complete:
                failed_natures += 1
                continue
            completed_natures += 1
            for record in records:
                try:
                    jobs.append(_candidate(record, site=site, observed_at=self._now()))
                except (TypeError, ValidationError, ValueError):
                    rejected += 1
        if failed_natures:
            return JobEnumerationResult(
                status=(
                    JobEnumerationStatus.SOURCE_PARTIAL
                    if completed_natures
                    else JobEnumerationStatus.SOURCE_FAILED
                ),
                jobs=tuple(jobs),
                source_key=site,
                error_code="jobhunt_source_unavailable",
                rejected_records=rejected,
            )
        total = len(jobs) + rejected
        if total and rejected / total > _MAX_REJECTED_RATIO:
            return JobEnumerationResult(
                status=JobEnumerationStatus.SOURCE_FAILED,
                source_key=site,
                error_code="jobhunt_invalid_output",
                rejected_records=rejected,
            )
        return JobEnumerationResult(
            status=JobEnumerationStatus.SOURCE_SUCCEEDED,
            jobs=tuple(jobs),
            source_key=site,
            pagination_complete=True,
            empty_confirmed=not jobs,
            rejected_records=rejected,
        )

    async def _run(self, arguments: tuple[str, ...]) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._executable),
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise JobHuntError("jobhunt_not_installed") from error
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError as error:
            if sys.platform == "win32":
                terminator = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await terminator.communicate()
            else:
                process.kill()
            await process.wait()
            raise JobHuntError("jobhunt_timeout") from error
        if process.returncode != 0:
            raise JobHuntError("jobhunt_source_unavailable")
        if len(stdout) > _MAX_OUTPUT_BYTES:
            raise JobHuntError("jobhunt_invalid_output")
        return stdout.decode("utf-8", errors="strict")


def _json(value: str) -> Any:
    try:
        return json.loads(value)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise JobHuntError("jobhunt_invalid_output") from error


def _records(payload: Any, *, max_records: int) -> tuple[list[dict[str, Any]], bool]:
    if isinstance(payload, list):
        records = [item for item in payload if isinstance(item, dict)]
        return records, len(payload) < max_records
    if not isinstance(payload, dict):
        raise JobHuntError("jobhunt_invalid_output")
    values = payload.get("jobs", payload.get("items", payload.get("data", [])))
    if not isinstance(values, list) or len(values) > _MAX_RECORDS:
        raise JobHuntError("jobhunt_invalid_output")
    complete = payload.get("pagination_complete") is True or payload.get("has_more") is False
    return [item for item in values if isinstance(item, dict)], complete


def _candidate(record: Mapping[str, Any], *, site: str, observed_at: datetime) -> ExternalJobCandidate:
    source_id = _text(record, "id", "code")
    title = _text(record, "name", "title")
    url = _text(record, "url")
    if not source_id or not title or not url:
        raise ValueError("missing required job fields")
    locations = record.get("location_names")
    city = (
        ", ".join(str(value) for value in locations)
        if isinstance(locations, list)
        else locations if isinstance(locations, str) else None
    )
    return ExternalJobCandidate(
        source_provider=f"jobhunt:{site}",
        source_raw_id=source_id,
        title=title,
        apply_url=HttpUrl(url),
        job_type=_text(record, "nature_code"),
        city=city,
        department=_text(record, "department_name"),
        description=_text(record, "description"),
        requirements=_text(record, "requirement", "requirements"),
        observed_at=observed_at,
    )


def _text(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None

"""Bounded Tianyancha CLI client that never persists or logs raw payloads."""

import asyncio
import json
from collections.abc import Sequence
from datetime import date
from typing import Any

from app.ingestion.errors import ProviderError
from app.rankings.gap_plan import EnrichmentCategory

_COMMANDS: dict[EnrichmentCategory, tuple[tuple[str, ...], ...]] = {
    EnrichmentCategory.GROWTH: (("operation", "financing-records"),),
    EnrichmentCategory.INTELLECTUAL_PROPERTY: (
        ("intellectual_property", "patent-info"),
        ("intellectual_property", "software-copyright-info"),
    ),
    EnrichmentCategory.MARKET_VALIDATION: (
        ("operation", "bidding-info"),
        ("operation", "qualifications"),
    ),
    EnrichmentCategory.MATERIAL_RISK: (("risk", "overview"),),
}


class TianyanchaRankingClient:
    def __init__(
        self,
        *,
        executable: str = "npx.cmd",
        prefix_args: tuple[str, ...] = ("-y", "tyc-cli"),
        timeout_seconds: float = 45.0,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        self._executable = executable
        self._prefix_args = prefix_args
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    async def fetch(
        self,
        category: EnrichmentCategory,
        search_key: str,
        *,
        window_start: date,
        window_end: date,
    ) -> dict[str, Any]:
        if category not in _COMMANDS:
            raise ValueError(f"unsupported enrichment category: {category}")
        if not search_key.strip() or any(char in search_key for char in "\r\n\0"):
            raise ValueError("search_key must be a non-empty single line")
        responses = []
        for command in _COMMANDS[category]:
            payload = await self._run(
                command,
                search_key,
                category=category,
                window_start=window_start,
                window_end=window_end,
            )
            responses.append({"tool": command[-1], "payload": payload})
        return {"responses": responses}

    async def _run(
        self,
        command: tuple[str, ...],
        search_key: str,
        *,
        category: EnrichmentCategory,
        window_start: date,
        window_end: date,
    ) -> dict[str, Any]:
        args = [*self._prefix_args, *command, search_key]
        args.extend(self._category_options(category, command[-1], window_start, window_end))
        args.append("--compact")
        try:
            process = await asyncio.create_subprocess_exec(
                self._executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError as error:
            if "process" in locals():
                process.kill()
                await process.wait()
            raise ProviderError(code="tianyancha_timeout", retryable=True) from error
        except OSError as error:
            raise ProviderError(code="tianyancha_cli_unavailable", retryable=False) from error
        if process.returncode != 0:
            raise ProviderError(
                code=_error_code(stderr.decode("utf-8", errors="replace")),
                retryable=False,
            )
        if len(stdout) > self._max_response_bytes:
            raise ProviderError(code="tianyancha_response_too_large", retryable=False)
        try:
            payload = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderError(code="tianyancha_invalid_response", retryable=False) from error
        if not isinstance(payload, dict):
            raise ProviderError(code="tianyancha_invalid_response", retryable=False)
        return payload

    @staticmethod
    def tool_call_count(category: EnrichmentCategory) -> int:
        return len(_COMMANDS[category])

    @staticmethod
    def _category_options(
        category: EnrichmentCategory,
        tool: str,
        window_start: date,
        window_end: date,
    ) -> Sequence[str]:
        if category == EnrichmentCategory.INTELLECTUAL_PROPERTY and tool == "patent-info":
            return (
                "--pageNum",
                "1",
                "--pageSize",
                "100",
                "--appDateBegin",
                window_start.isoformat(),
                "--appDateEnd",
                window_end.isoformat(),
                "--patentType",
                "1",
            )
        if category == EnrichmentCategory.MARKET_VALIDATION and tool == "bidding-info":
            return (
                "--pageNum",
                "1",
                "--pageSize",
                "100",
                "--publishStartTime",
                window_start.isoformat(),
                "--publishEndTime",
                window_end.isoformat(),
                "--bidType",
                "4",
            )
        if category in {
            EnrichmentCategory.GROWTH,
            EnrichmentCategory.INTELLECTUAL_PROPERTY,
            EnrichmentCategory.MARKET_VALIDATION,
        }:
            return ("--pageNum", "1", "--pageSize", "100")
        return ()


def _error_code(stderr: str) -> str:
    lowered = stderr.lower()
    if "300002" in lowered or "300003" in lowered or "认证" in stderr:
        return "tianyancha_auth_failed"
    if "300004" in lowered or "频率" in stderr:
        return "tianyancha_rate_limited"
    if (
        "300007" in lowered
        or "http 402" in lowered
        or "额度" in stderr
        or "调用次数已用完" in stderr
    ):
        return "tianyancha_quota_exhausted"
    return "tianyancha_cli_error"

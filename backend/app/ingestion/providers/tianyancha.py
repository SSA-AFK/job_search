"""Tianyancha CLI provider - calls tyc CLI for structured company data."""

import asyncio
import hashlib
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import HttpUrl, TypeAdapter, ValidationError

from app.ingestion.contracts import ProviderQuery, ProviderResult, RawDocument
from app.ingestion.errors import ProviderError

_URL_ADAPTER = TypeAdapter(HttpUrl)
_TIANYANCHA_BASE = "https://www.tianyancha.com/company"
_RETRY_DELAYS = (0.5, 1.0, 2.0)


class TianyanchaProvider:
    """Provider that fetches company data via the tyc CLI (天眼查).

    Calls one registration query per company and never performs a discovery
    fallback, so a bounded CLI quota cannot be silently consumed twice.
    """

    name = "tianyancha"

    def __init__(
        self,
        *,
        enabled: bool = True,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = lambda: 0.0,
        cli_executable: str = "npx",
        cli_args: tuple[str, ...] = ("tyc",),
        command_timeout_seconds: float = 30.0,
        call_budget: int = 0,
    ) -> None:
        self._enabled = enabled
        self._sleep = sleep
        self._jitter = jitter
        self._cli_executable = cli_executable
        self._cli_args = cli_args
        self._command_timeout_seconds = command_timeout_seconds
        self._call_budget = call_budget
        self._calls_made = 0
        self._budget_lock = asyncio.Lock()

    async def search(self, query: ProviderQuery) -> ProviderResult:
        if not self._enabled:
            return ProviderResult(documents=())

        company_name = query.query
        async with self._budget_lock:
            if self._calls_made >= self._call_budget:
                raise ProviderError(
                    code="tianyancha_budget_exhausted",
                    retryable=False,
                    detail="configured Tianyancha call budget has been exhausted",
                )
            self._calls_made += 1
        try:
            reg_info = await self._run_cli(
                "company", "registration-info", company_name
            )
            doc = self._build_document("registration_info", company_name, reg_info)
        except ProviderError as exc:
            raise ProviderError(
                code=exc.code,
                retryable=False,
                detail=exc.detail,
            ) from exc
        if doc is None:
            raise ProviderError(
                code="no_data", retryable=False, detail="tianyancha CLI returned no data"
            )

        return ProviderResult(
            documents=(doc,),
            truncated=False,
        )

    def _extract_company_id(self, raw_data: dict[str, Any]) -> str | None:
        """Extract the tianyancha company ID from various response formats."""
        # Format 1: registration-info: {"sources": {"base": {"id": ...}}}
        sources = raw_data.get("sources", {})
        base = sources.get("base", {})
        company_id = base.get("id")
        if company_id is not None:
            return str(company_id)

        # Format 2: company search: {"items": [{"id": ...}]}
        items = raw_data.get("items")
        if isinstance(items, list) and items:
            first = items[0]
            company_id = first.get("id")
            if company_id is not None:
                return str(company_id)

        return None

    def _build_document(
        self, doc_type: str, company_name: str, raw_data: dict[str, Any]
    ) -> RawDocument | None:
        """Build a RawDocument from the tyc CLI JSON response."""
        text = json.dumps(raw_data, ensure_ascii=False, indent=2)
        if not text.strip():
            return None

        company_id = self._extract_company_id(raw_data)
        url_str: str | None = None

        # Extract website from registration-info response
        sources = raw_data.get("sources", {})
        base = sources.get("base", {})
        website_list = base.get("websiteList")

        # Use the company's official website as the document URL, or
        # construct a tianyancha company page URL as fallback.
        if website_list:
            raw_url = website_list.split(",")[0].strip()
            if raw_url and not raw_url.startswith(("http://", "https://")):
                raw_url = f"https://{raw_url}"
            url_str = raw_url
        elif company_id:
            url_str = f"{_TIANYANCHA_BASE}/{company_id}"

        if url_str is None:
            url_str = f"{_TIANYANCHA_BASE}/{_hash_name(company_name)}"

        try:
            url = _URL_ADAPTER.validate_python(url_str)
        except ValidationError:
            url = _URL_ADAPTER.validate_python(f"{_TIANYANCHA_BASE}/{_hash_name(company_name)}")

        return RawDocument(
            provider=self.name,
            external_id=company_id,
            url=url,
            title=f"{company_name} - {doc_type}",
            text=text[:200_000],
            published_at=datetime.now(UTC),
            authority_level=3,  # High authority: structured business registry data
        )

    async def _run_cli(self, *args: str) -> dict[str, Any]:
        """Run a tyc CLI command and return the parsed JSON output."""
        cmd = [self._cli_executable, *self._cli_args, *args]

        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                # On Windows, .cmd/.bat wrappers need shell=True
                use_shell = (
                    sys.platform == "win32"
                    and self._cli_executable.lower().endswith((".cmd", ".bat"))
                )
                if use_shell:
                    proc = await asyncio.create_subprocess_shell(
                        " ".join(cmd),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=self._command_timeout_seconds
                    )
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                    raise ProviderError(
                        code="cli_timeout",
                        retryable=True,
                        detail=f"tyc command timed out: {' '.join(cmd)}",
                    ) from None

                if proc.returncode != 0:
                    stderr_text = stderr.decode("utf-8", errors="replace").strip()
                    raise ProviderError(
                        code="cli_error",
                        retryable=False,
                        detail=f"tyc CLI exited {proc.returncode}: {stderr_text or 'unknown error'}",
                    )

                raw = stdout.decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ProviderError(
                        code="unexpected_format",
                        retryable=False,
                        detail="tyc CLI returned non-dict JSON",
                    )
                return parsed

            except ProviderError:
                if attempt == len(_RETRY_DELAYS):
                    raise
                await self._sleep(_RETRY_DELAYS[attempt] + self._jitter())

        raise AssertionError("unreachable")


def _hash_name(name: str) -> str:
    """Simple name-based identifier for URL fallback."""
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]

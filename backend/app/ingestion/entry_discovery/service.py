"""Entry discovery service (Phase 1): concurrent search + probe + validate.

职责：
  Phase 1 - 高并发入口发现（只读搜索结果页 / HTTP HEAD，不需要 Playwright 渲染）
  Phase 2 - 低并发结构化抓取（由 Orchestrator / ATS Provider 负责，不在此文件）

纯规则实现，无 LLM 依赖。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.ingestion.contracts import ProviderQuery
from app.ingestion.entry_discovery.contracts import (
    CompanyNamePool,
    EntryCandidate,
    EntryPlatform,
    build_careers_probe_paths,
    build_site_queries,
    validate_entry_candidate,
)


@dataclass
class DiscoveryResult:
    """Output of Phase 1 entry discovery."""

    candidates: tuple[EntryCandidate, ...]
    high_confidence: tuple[EntryCandidate, ...]
    diagnostics: tuple[str, ...]

    @property
    def ats_entries(self) -> tuple[EntryCandidate, ...]:
        return tuple(
            c
            for c in self.high_confidence
            if c.platform in {EntryPlatform.ATS_FEISHU, EntryPlatform.ATS_MOKA, EntryPlatform.ATS_BYTEDANCE}
        )

    @property
    def company_site_entries(self) -> tuple[EntryCandidate, ...]:
        return tuple(
            c for c in self.high_confidence if c.platform == EntryPlatform.COMPANY_SITE_CAREERS
        )

    @property
    def job_board_entries(self) -> tuple[EntryCandidate, ...]:
        return tuple(
            c
            for c in self.high_confidence
            if c.platform
            in {EntryPlatform.LIEPIN, EntryPlatform.LAGOU}
        )


class EntryDiscoveryService:
    """Phase 1: 并发发现招聘入口并校验。

    参数：
      serper_provider: 可选的 SerperProvider 实例（用于 site: 搜索）。
                       传 None 时跳过 Serper 搜索，仅做官网 careers 探测。
      careers_prober: 可选的 HTTP 客户端（协程），用于 careers 页面探测。
                      传 None 时用默认 httpx.AsyncClient。
      semaphore_site: site: 查询层的并发数（搜索 API，HTML 小，可高并发）。
      semaphore_probe: 官网 careers 探测并发数（目标服务器压力敏感，较低）。
    """

    def __init__(
        self,
        *,
        serper_provider: Any | None = None,
        careers_prober: Any | None = None,
        semaphore_site: int = 10,
        semaphore_probe: int = 8,
        confidence_threshold: float = 0.6,
    ) -> None:
        self._serper = serper_provider
        self._careers_prober = careers_prober
        self._sem_site = asyncio.Semaphore(semaphore_site)
        self._sem_probe = asyncio.Semaphore(semaphore_probe)
        self._threshold = confidence_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def discover(self, name_pool: CompanyNamePool) -> DiscoveryResult:
        """Run Phase 1: concurrent entry discovery + validation."""
        tasks: list[asyncio.Task[list[EntryCandidate]]] = []

        # 1) Serper site: queries (ATS + 招聘平台)
        if self._serper is not None and getattr(self._serper, "name", None) == "serper":
            tasks.append(asyncio.create_task(self._run_site_queries(name_pool)))

        if name_pool.known_entry_urls:
            tasks.append(asyncio.create_task(self._validate_known_entry_urls(name_pool)))

        # 2) 官网 careers 路径探测
        if name_pool.domains:
            tasks.append(asyncio.create_task(self._probe_careers_pages(name_pool)))

        if not tasks:
            return DiscoveryResult((), (), ("no_discovery_channel_enabled",))

        done, _ = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)
        candidates: list[EntryCandidate] = []
        diagnostics: list[str] = []
        for fut in done:
            try:
                candidates.extend(fut.result())
            except Exception as exc:  # noqa: BLE001 - 保护：单个任务失败不影响整体
                diagnostics.append(f"discovery_task_error:{type(exc).__name__}")

        # 3) Validate + dedupe by URL
        validated: list[EntryCandidate] = []
        seen_url: set[str] = set()
        for raw in candidates:
            key = raw.url.rstrip("/").lower()
            if key in seen_url:
                continue
            seen_url.add(key)
            validated.append(validate_entry_candidate(raw, name_pool))

        validated.sort(key=lambda c: c.overall_confidence, reverse=True)
        high_conf = tuple(c for c in validated if c.is_high_confidence(self._threshold))
        return DiscoveryResult(
            candidates=tuple(validated),
            high_confidence=high_conf,
            diagnostics=tuple(diagnostics),
        )

    # ------------------------------------------------------------------
    # Serper site: search
    # ------------------------------------------------------------------

    async def _run_site_queries(self, name_pool: CompanyNamePool) -> list[EntryCandidate]:
        queries = build_site_queries(name_pool)
        if not queries:
            return []
        site_to_platform = {site: plat for plat, site in [
            ("ats_feishu", "jobs.feishu.cn"),
            ("ats_moka", "app.mokahr.com"),
            ("ats_bytedance", "jobs.bytedance.com"),
            ("liepin", "liepin.com"),
            ("lagou", "lagou.com"),
        ]}

        serper = self._serper
        if serper is None:
            return []

        async def one_query(platform: str, q: str) -> list[EntryCandidate]:
            async with self._sem_site:
                try:
                    result = await serper.search(ProviderQuery(query=q, max_results=10))
                except Exception:  # noqa: BLE001
                    return []
                out: list[EntryCandidate] = []
                for doc in result.documents:
                    host = (urlsplit(str(doc.url)).hostname or "").lower().rstrip(".")
                    # If the platform-specific site label doesn't match host, skip
                    matched = False
                    for site, plat in site_to_platform.items():
                        if host == site or host.endswith("." + site):
                            matched_plat = plat
                            matched = True
                            break
                    if not matched:
                        continue
                    out.append(
                        EntryCandidate(
                            url=str(doc.url),
                            platform=matched_plat,
                            title=doc.title,
                            snippet=doc.text[:500] if doc.text else None,
                            source_provider="serper",
                            source_url=str(doc.url),
                        )
                    )
                return out

        return _flatten(
            await asyncio.gather(
                *(one_query(plat, q) for plat, q in queries), return_exceptions=False
            )
        )

    async def _validate_known_entry_urls(self, name_pool: CompanyNamePool) -> list[EntryCandidate]:
        results: list[EntryCandidate] = []
        seen: set[str] = set()
        for url in name_pool.known_entry_urls:
            value = url.strip()
            if not value:
                continue
            if not value.startswith(("http://", "https://")):
                value = f"https://{value}"
            key = value.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(
                EntryCandidate(
                    url=value,
                    platform=_platform_from_url(value),
                    title=None,
                    snippet=None,
                    source_provider="known_entry_url",
                    source_url=value,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Careers page probe
    # ------------------------------------------------------------------

    async def _probe_careers_pages(self, name_pool: CompanyNamePool) -> list[EntryCandidate]:
        # 用每个已知域名的根域（去 www）作为探测锚点，避免重复探测相同域名
        anchors: list[str] = []
        seen: set[str] = set()
        for d in name_pool.domains:
            host = d.lower()
            host = host.removeprefix("*.")
            host = host.removeprefix("www.")
            if host and host not in seen:
                seen.add(host)
                anchors.append(host)

        probe_urls: list[str] = []
        for anchor in anchors:
            probe_urls.extend(build_careers_probe_paths(anchor))
        if not probe_urls:
            return []

        client = self._careers_prober or httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=5), trust_env=False, follow_redirects=True
        )
        owns_client = self._careers_prober is None

        async def one_probe(url: str) -> EntryCandidate | None:
            async with self._sem_probe:
                try:
                    resp = await client.get(url)
                except Exception:  # noqa: BLE001
                    return None
            # 只接受成功 / 被拦截（403），404 等明确"不存在"的路径直接丢弃
            if resp.status_code not in {200, 403}:
                return None
            # 提取标题（从 HTML <title> 粗提取）
            title = _extract_title(resp.text) if resp.text else None
            return EntryCandidate(
                url=str(resp.url),
                platform=EntryPlatform.COMPANY_SITE_CAREERS,
                title=title,
                snippet=resp.text[:300] if resp.text else None,
                source_provider="careers_probe",
                source_url=url,
            )

        try:
            results = await asyncio.gather(
                *(one_probe(u) for u in probe_urls), return_exceptions=True
            )
        finally:
            if owns_client:
                await client.aclose()

        return [c for c in results if isinstance(c, EntryCandidate)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten(nested: list[list[EntryCandidate]]) -> list[EntryCandidate]:
    result: list[EntryCandidate] = []
    for inner in nested:
        result.extend(inner)
    return result


def _platform_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "jobs.feishu.cn" or host.endswith(".jobs.feishu.cn"):
        return EntryPlatform.ATS_FEISHU
    if host == "app.mokahr.com" or host.endswith(".app.mokahr.com"):
        return EntryPlatform.ATS_MOKA
    if host == "jobs.bytedance.com" or host.endswith(".jobs.bytedance.com"):
        return EntryPlatform.ATS_BYTEDANCE
    if host == "liepin.com" or host.endswith(".liepin.com"):
        return EntryPlatform.LIEPIN
    if host == "lagou.com" or host.endswith(".lagou.com"):
        return EntryPlatform.LAGOU
    return EntryPlatform.COMPANY_SITE_CAREERS


_TITLE_RE = None


def _extract_title(html: str) -> str | None:
    global _TITLE_RE
    if _TITLE_RE is None:
        import re as _re
        _TITLE_RE = _re.compile(r"<title[^>]*>([^<]{1,200})</title>", _re.IGNORECASE)
    m = _TITLE_RE.search(html)
    if not m:
        return None
    raw = m.group(1).strip()
    return raw or None

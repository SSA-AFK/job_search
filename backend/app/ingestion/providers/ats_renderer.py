# backend/app/ingestion/providers/ats_renderer.py
import asyncio
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from app.ingestion.errors import ProviderError
from app.ingestion.providers.security import is_public_ip, resolve_host


@dataclass(frozen=True, slots=True)
class RenderedPage:
    url: str
    html: str
    status_code: int
    title: str | None = None


def _import_playwright():
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

    return async_playwright


class AtsRenderer:
    def __init__(self, *, pool_size: int, page_timeout_seconds: float) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be positive")
        self._pool_size = pool_size
        self._page_timeout_seconds = page_timeout_seconds
        self._semaphore = asyncio.Semaphore(pool_size)
        self._playwright: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            try:
                async_playwright = _import_playwright()
            except ImportError as error:
                raise ProviderError(
                    code="renderer_unavailable", retryable=False, detail=str(error)
                ) from error
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)

    async def render(
        self,
        url: str,
        *,
        allowed_hosts: set[str],
        wait_selector: str | None = None,
    ) -> RenderedPage:
        await self._validate_url(url, allowed_hosts)
        await self._ensure_browser()
        async with self._semaphore:
            assert self._browser is not None
            page = await self._browser.new_page()
            try:
                response = await page.goto(
                    url, wait_until="networkidle", timeout=int(self._page_timeout_seconds * 1000)
                )
                if response is None:
                    raise ProviderError(
                        code="renderer_no_response", retryable=False, detail="page.goto returned None"
                    )
                if wait_selector is not None:
                    await page.wait_for_selector(
                        wait_selector, timeout=int(self._page_timeout_seconds * 1000)
                    )
                html = await page.content()
                title = await page.title()
                return RenderedPage(
                    url=page.url, html=html[:200_000], status_code=response.status, title=title
                )
            except ProviderError:
                raise
            except Exception as error:
                raise ProviderError(
                    code="renderer_failed", retryable=True, detail=str(error)
                ) from error
            finally:
                await page.close()

    async def aclose(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

    @staticmethod
    async def _validate_url(url: str, allowed_hosts: set[str]) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username:
            raise ProviderError(code="unsafe_url", retryable=False, detail="invalid scheme or authority")
        if host not in allowed_hosts:
            raise ProviderError(code="unsafe_url", retryable=False, detail="host not allowlisted")
        try:
            address = ip_address(host)
        except ValueError:
            addresses = await resolve_host(host)
            if not addresses or any(not is_public_ip(a) for a in addresses):
                raise ProviderError(code="unsafe_url", retryable=False, detail="dns not public") from None
        else:
            if not is_public_ip(str(address)):
                raise ProviderError(code="unsafe_url", retryable=False, detail="ip not public")

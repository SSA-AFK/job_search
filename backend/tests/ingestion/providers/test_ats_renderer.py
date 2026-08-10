# backend/tests/ingestion/providers/test_ats_renderer.py
import pytest

from app.ingestion.errors import ProviderError
from app.ingestion.providers.ats_renderer import AtsRenderer, RenderedPage  # noqa: F401


@pytest.mark.asyncio
async def test_renderer_raises_unavailable_when_playwright_missing(monkeypatch) -> None:
    renderer = AtsRenderer(pool_size=1, page_timeout_seconds=5.0)
    monkeypatch.setattr(
        "app.ingestion.providers.ats_renderer._import_playwright",
        lambda: (_ for _ in ()).throw(ImportError("no playwright")),
    )

    async def _fake_resolve_host(host: str) -> tuple[str, ...]:
        return ("1.1.1.1",)  # public IP, passes is_public_ip check

    monkeypatch.setattr(
        "app.ingestion.providers.ats_renderer.resolve_host", _fake_resolve_host
    )
    with pytest.raises(ProviderError, match="renderer_unavailable"):
        await renderer.render("https://jobs.feishu.cn/x", allowed_hosts={"jobs.feishu.cn"})


@pytest.mark.asyncio
async def test_renderer_rejects_non_allowlisted_host() -> None:
    renderer = AtsRenderer(pool_size=1, page_timeout_seconds=5.0)
    with pytest.raises(ProviderError, match="unsafe_url"):
        await renderer.render("https://evil.example.com/x", allowed_hosts={"jobs.feishu.cn"})


@pytest.mark.asyncio
async def test_renderer_aclose_is_idempotent_when_not_started() -> None:
    renderer = AtsRenderer(pool_size=1, page_timeout_seconds=5.0)
    await renderer.aclose()
    await renderer.aclose()

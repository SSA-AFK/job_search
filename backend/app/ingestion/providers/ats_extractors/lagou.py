"""拉勾 (lagou.com) ATS 列表页提取器。"""
from pydantic import HttpUrl

from app.ingestion.contracts import RawDocument
from app.ingestion.jobs.contracts import AtsListResult, AtsParseStatus
from app.ingestion.jobs.parser import parse_html_job_list
from app.ingestion.providers.ats_renderer import AtsRenderer, RenderedPage
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy

_HOST = "lagou.com"


class LagouAtsExtractor:
    platform = "lagou"
    host = _HOST

    async def fetch_list(
        self,
        *,
        url: str,
        http_client: SafeHttpClient,
        robots_policy: RobotsPolicy,
        renderer: AtsRenderer | None,
    ) -> tuple[RawDocument, AtsListResult]:
        if not await robots_policy.can_fetch(url):
            return self._failed(url, "robots_disallowed")
        if renderer is None:
            return self._failed(url, "renderer_required")
        page = await renderer.render(
            url,
            allowed_hosts={_HOST, f"www.{_HOST}"},
            wait_selector="div.job-box, li.con_list_item, a.position_link, div.list_item_top",
        )
        challenge = self._access_challenge(page)
        if challenge is not None:
            return self._failed(url, challenge)
        result = parse_html_job_list(page.html, platform="lagou")
        document = RawDocument(
            provider="ats_lagou",
            external_id=None,
            url=HttpUrl(page.url),
            title=page.title,
            text=page.html[:200_000],
            published_at=None,
            authority_level=2,
        )
        return document, result

    @staticmethod
    def _access_challenge(page: RenderedPage) -> str | None:
        path = page.url.lower()
        content = f"{page.title or ''}\n{page.html}".lower()
        if "/login" in path or "sign in" in content or "拉勾登录" in content:
            return "login_required"
        if any(k in content for k in ("captcha", "verify you are human", "请完成验证", "安全验证", "图形验证")):
            return "captcha_required"
        if any(k in content for k in ("访问过于频繁", "403 forbidden", "too many requests", "被限制访问")):
            return "rate_limited"
        return None

    @staticmethod
    def _failed(url: str, error_code: str) -> tuple[RawDocument, AtsListResult]:
        document = RawDocument(
            provider="ats_lagou",
            external_id=None,
            url=HttpUrl(url),
            title=None,
            text="",
            published_at=None,
            authority_level=2,
        )
        return document, AtsListResult(
            candidates=(), status=AtsParseStatus.FAILED, error_code=error_code
        )

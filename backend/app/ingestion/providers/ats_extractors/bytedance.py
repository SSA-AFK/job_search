"""字节跳动 (jobs.bytedance.com) ATS 列表页提取器。

与飞书/Moka 范式一致：fetch_list → robots → render → challenge → parse → (RawDocument, AtsListResult)

字节跳动招聘页面是大型 SPA，有两个特殊问题：
1. 职位卡片 (.positionItem) 在完整 HTML 的 2.7MB+ 处，截断 HTML 会导致解析失败
2. 真实页面中 positionItem 不含 <a> 标签，HTML 解析器无法提取链接

解决方案：使用 page.evaluate 直接从 DOM 提取结构化职位数据（标题、URL、城市等），
绕过 HTML 截断和链接缺失问题。
"""
import json
from urllib.parse import urljoin

from pydantic import HttpUrl

from app.ingestion.contracts import RawDocument
from app.ingestion.jobs.contracts import AtsJobCandidate, AtsListResult, AtsParseStatus
from app.ingestion.jobs.parser import _guess_city, _guess_employment_type, _guess_salary_fields
from app.ingestion.providers.ats_renderer import AtsRenderer, RenderedPage
from app.ingestion.providers.http import SafeHttpClient
from app.ingestion.providers.robots import RobotsPolicy

_HOST = "jobs.bytedance.com"

# JavaScript: extract structured job data directly from the DOM.
_EXTRACT_POSITION_ITEMS = """
() => {
  const cards = document.querySelectorAll('.positionItem');
  if (cards.length === 0) return null;
  const results = [];
  for (const card of cards) {
    const titleEl = card.querySelector('.positionItem-title-text, [class*="title-text"], .title__37NOe span, [data-test="positionItem"] span');
    const title = titleEl ? titleEl.textContent.trim() : '';
    let href = '';
    const linkEl = card.querySelector('a[href]') || card.closest('a[href]');
    if (linkEl) href = linkEl.getAttribute('href') || '';
    const subEl = card.querySelector('.positionItem-subTitle, [class*="subTitle"], .subTitle__3sRa3');
    const subText = subEl ? subEl.textContent.trim() : '';
    const fullText = card.textContent.replace(/\\s+/g, ' ').trim().slice(0, 500);
    results.push({title, href, subText, fullText});
  }
  return JSON.stringify(results);
}
"""


class BytedanceAtsExtractor:
    platform = "bytedance"
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
            allowed_hosts={_HOST},
            wait_selector=".positionItem",
            scroll_steps=8,
            max_html_chars=200_000,
            extract_script=_EXTRACT_POSITION_ITEMS,
        )
        challenge = self._access_challenge(page)
        if challenge is not None:
            return self._failed(url, challenge)

        result = self._build_candidates(page.extracted_html, base_url=page.url)
        document = RawDocument(
            provider="ats_bytedance",
            external_id=None,
            url=HttpUrl(page.url),
            title=page.title,
            text=page.html[:200_000],
            published_at=None,
            authority_level=2,
        )
        return document, result

    @staticmethod
    def _build_candidates(extracted: str | None, *, base_url: str) -> AtsListResult:
        """Convert JSON data from page.evaluate into AtsJobCandidate objects."""
        if not extracted:
            return AtsListResult(
                candidates=(),
                status=AtsParseStatus.PARTIAL,
                error_code="no_candidates",
            )
        try:
            items = json.loads(extracted)
        except (json.JSONDecodeError, TypeError):
            return AtsListResult(
                candidates=(),
                status=AtsParseStatus.FAILED,
                error_code="parse_failed",
            )
        if not isinstance(items, list) or not items:
            return AtsListResult(
                candidates=(),
                status=AtsParseStatus.PARTIAL,
                error_code="no_candidates",
            )

        candidates: list[AtsJobCandidate] = []
        seen_hrefs: set[str] = set()
        for item in items:
            title = (item.get("title") or "").strip()
            href = (item.get("href") or "").strip()
            if not title:
                continue
            if href and href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            full_url = urljoin(base_url, href) if href else base_url
            sub_text = item.get("subText") or ""
            full_text = item.get("fullText") or ""
            blob = " | ".join(part for part in (title, sub_text, full_text) if part)

            city = _guess_city(blob)
            employment_type = _guess_employment_type(blob)
            sal_min_k, sal_max_k, sal_months = _guess_salary_fields(blob)

            raw_attributes: dict[str, str] = {}
            if sal_min_k is not None:
                raw_attributes["salary_min_k"] = str(sal_min_k)
            if sal_max_k is not None:
                raw_attributes["salary_max_k"] = str(sal_max_k)
            if sal_months is not None:
                raw_attributes["salary_months"] = str(sal_months)

            try:
                candidates.append(
                    AtsJobCandidate(
                        title=title[:500],
                        url=HttpUrl(full_url),
                        external_id=None,
                        city=city,
                        employment_type=employment_type,
                        raw_attributes=raw_attributes,
                    )
                )
            except Exception:  # noqa: BLE001, S112
                continue

        status = AtsParseStatus.SUCCEEDED if candidates else AtsParseStatus.PARTIAL
        return AtsListResult(
            candidates=tuple(candidates),
            status=status,
            observed_count=len(candidates),
            reported_total=None,
            error_code=None if candidates else "no_candidates",
        )

    @staticmethod
    def _access_challenge(page: RenderedPage) -> str | None:
        path = page.url.lower()
        content = f"{page.title or ''}\n{page.html}".lower()
        if path.endswith("/login") or "sign in to continue" in content or "登录" in content:
            return "login_required"
        if "captcha" in content or "verify you are human" in content or "验证" in content:
            return "captcha_required"
        return None

    @staticmethod
    def _failed(url: str, error_code: str) -> tuple[RawDocument, AtsListResult]:
        document = RawDocument(
            provider="ats_bytedance", external_id=None, url=HttpUrl(url), title=None,
            text="", published_at=None, authority_level=2,
        )
        return document, AtsListResult(
            candidates=(), status=AtsParseStatus.FAILED, error_code=error_code
        )

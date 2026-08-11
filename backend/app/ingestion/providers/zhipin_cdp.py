from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol
from urllib.parse import quote_plus

from pydantic import HttpUrl
from rapidfuzz.fuzz import ratio

from app.ingestion.contracts import (
    ParsedJob,
    ProviderFetchStats,
    ProviderQuery,
    ProviderResult,
    RawDocument,
)
from app.ingestion.entry_discovery.contracts import normalize_for_compare, strip_legal_suffixes
from app.ingestion.jobs.parser import _guess_employment_type, _guess_salary_fields


@dataclass(frozen=True, slots=True)
class ZhipinCompanyCandidate:
    brand_id: str
    name: str
    url: str | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ZhipinJobItem:
    title: str
    url: str
    job_id: str | None = None
    city: str | None = None
    salary: str | None = None
    experience: str | None = None
    education: str | None = None
    employment_type: str | None = None
    posted_at: str | None = None
    description: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ZhipinJobPage:
    jobs: tuple[ZhipinJobItem, ...]
    has_more: bool = False
    total: int | None = None


class ZhipinCdpClient(Protocol):
    async def search_companies(self, company_name: str) -> tuple[ZhipinCompanyCandidate, ...]: ...

    async def list_company_jobs(self, brand_id: str, *, page: int, page_size: int) -> ZhipinJobPage: ...


def _import_playwright():
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

    return async_playwright


class ZhipinCdpUnavailableClient:
    async def search_companies(self, company_name: str) -> tuple[ZhipinCompanyCandidate, ...]:
        raise ZhipinCdpClientError("client_unconfigured")

    async def list_company_jobs(self, brand_id: str, *, page: int, page_size: int) -> ZhipinJobPage:
        raise ZhipinCdpClientError("client_unconfigured")


class ZhipinCdpClientError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ACCESS_BLOCK_ERROR_CODES = frozenset(
    {
        "captcha_required",
        "login_required",
        "rate_limited",
        "render_failed",
        "client_unconfigured",
        "browser_unavailable",
        "api_changed",
        "platform_cooldown",
    }
)


_ZHIPIN_HOME_URL = "https://www.zhipin.com/web/geek/jobs"
_COMPANY_SEARCH_PATH = "/wapi/zpgeek/brand/search.json"
_COMPANY_JOBS_PATH = "/wapi/zpgeek/brand/joblist.json"


class PlaywrightZhipinCdpClient:
    def __init__(
        self,
        *,
        endpoint_url: str = "http://127.0.0.1:9222",
        page: Any | None = None,
        page_timeout_seconds: float = 30.0,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._provided_page = page
        self._page_timeout_ms = max(1_000, int(page_timeout_seconds * 1_000))
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any | None = page

    async def search_companies(self, company_name: str) -> tuple[ZhipinCompanyCandidate, ...]:
        page = await self._ensure_page()
        await self._ensure_zhipin_page(page)
        payload = await self._fetch_json(
            page,
            _COMPANY_SEARCH_PATH,
            {
                "query": company_name,
                "page": 1,
                "pageSize": 20,
            },
        )
        return _parse_company_candidates(payload)

    async def list_company_jobs(self, brand_id: str, *, page: int, page_size: int) -> ZhipinJobPage:
        browser_page = await self._ensure_page()
        await self._ensure_zhipin_page(browser_page)
        payload = await self._fetch_json(
            browser_page,
            _COMPANY_JOBS_PATH,
            {
                "brandId": brand_id,
                "page": page,
                "pageSize": page_size,
            },
        )
        return _parse_job_page(payload, page=page, page_size=page_size)

    async def close(self) -> None:
        if self._provided_page is not None:
            return
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._page = None

    async def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page
        try:
            async_playwright = _import_playwright()
        except ImportError as error:
            raise ZhipinCdpClientError("client_unconfigured") from error
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self._endpoint_url)
            context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            self._page = context.pages[0] if context.pages else await context.new_page()
            return self._page
        except Exception as error:
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            self._browser = None
            self._page = None
            raise ZhipinCdpClientError("browser_unavailable") from error

    async def _ensure_zhipin_page(self, page: Any) -> None:
        current_url = str(getattr(page, "url", ""))
        if "zhipin.com" not in current_url:
            try:
                await page.goto(_ZHIPIN_HOME_URL, wait_until="domcontentloaded", timeout=self._page_timeout_ms)
            except Exception as error:
                raise ZhipinCdpClientError("browser_unavailable") from error
        title = await page.title()
        if "登录" in title or "login" in str(getattr(page, "url", "")).lower():
            raise ZhipinCdpClientError("login_required")
        if "请稍候" in title or "安全" in title:
            raise ZhipinCdpClientError("captcha_required")

    async def _fetch_json(self, page: Any, path: str, data: Mapping[str, object]) -> Mapping[str, Any]:
        payload = await page.evaluate(
            """
            async ({ path, data }) => {
              const body = new URLSearchParams();
              for (const [key, value] of Object.entries(data)) {
                if (value !== undefined && value !== null) body.set(key, String(value));
              }
              const response = await fetch(path, {
                method: 'POST',
                credentials: 'include',
                headers: {
                  'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                  'x-requested-with': 'XMLHttpRequest',
                  'accept': 'application/json, text/plain, */*'
                },
                body
              });
              const text = await response.text();
              return { status: response.status, url: response.url, text };
            }
            """,
            {"path": path, "data": dict(data)},
        )
        if not isinstance(payload, Mapping):
            raise ZhipinCdpClientError("api_changed")
        status = int(payload.get("status") or 0)
        text = str(payload.get("text") or "")
        if status in {401, 403} or "请稍候" in text or "安全验证" in text:
            raise ZhipinCdpClientError("captcha_required")
        if status == 429:
            raise ZhipinCdpClientError("rate_limited")
        if status >= 500 or status == 0:
            raise ZhipinCdpClientError("browser_unavailable")
        try:
            import json

            parsed = json.loads(text)
        except ValueError as error:
            raise ZhipinCdpClientError("api_changed") from error
        if not isinstance(parsed, Mapping):
            raise ZhipinCdpClientError("api_changed")
        if _looks_like_login_response(parsed):
            raise ZhipinCdpClientError("login_required")
        if _looks_like_block_response(parsed):
            raise ZhipinCdpClientError("captcha_required")
        return parsed


class ZhipinCdpCompanyProvider:
    name = "zhipin_cdp_company"
    requires_website = False

    def __init__(
        self,
        *,
        client: ZhipinCdpClient | None = None,
        enabled: bool = False,
        min_match_score: float = 80.0,
        page_size: int = 30,
        max_pages: int = 20,
        platform_block_threshold: int = 2,
    ) -> None:
        self._client = client or ZhipinCdpUnavailableClient()
        self._enabled = enabled
        self._min_match_score = min(100.0, max(0.0, min_match_score))
        self._page_size = max(1, page_size)
        self._max_pages = max(1, max_pages)
        self._platform_block_threshold = max(1, platform_block_threshold)
        self._block_count = 0

    async def search(self, query: ProviderQuery) -> ProviderResult:
        if not self._enabled:
            return ProviderResult(documents=())
        if self._block_count >= self._platform_block_threshold:
            return ProviderResult(
                documents=(),
                warnings=("platform_cooldown",),
                stats=(self._stats(error_code="platform_cooldown", blocked_pages=1),),
            )
        try:
            companies = await self._client.search_companies(query.query)
        except ZhipinCdpClientError as error:
            return self._blocked_result(error.code)
        best, score = _best_company_match(query.query, companies)
        if best is None or score < self._min_match_score:
            return ProviderResult(
                documents=(),
                warnings=("company_not_matched",),
                stats=(self._stats(entries_discovered=len(companies), error_code="company_not_matched"),),
            )
        pages_fetched = 0
        jobs: list[ZhipinJobItem] = []
        reported_total: int | None = None
        try:
            for page in range(1, self._max_pages + 1):
                result = await self._client.list_company_jobs(best.brand_id, page=page, page_size=self._page_size)
                pages_fetched += 1
                jobs.extend(result.jobs)
                if result.total is not None:
                    reported_total = result.total
                if not result.has_more or not result.jobs:
                    break
        except ZhipinCdpClientError as error:
            stats = self._stats(
                entries_discovered=len(companies),
                pages_fetched=pages_fetched,
                parsed_jobs=len(jobs),
                blocked_pages=1 if error.code in _ACCESS_BLOCK_ERROR_CODES else 0,
                error_code=error.code,
            )
            if error.code in _ACCESS_BLOCK_ERROR_CODES:
                self._block_count += 1
            return ProviderResult(
                documents=_documents(best, jobs, reported_total),
                warnings=(error.code,),
                parsed_jobs=tuple(_parsed_job(item) for item in jobs),
                stats=(stats,),
            )
        parsed_jobs = tuple(_parsed_job(item) for item in jobs)
        if parsed_jobs:
            self._block_count = 0
        return ProviderResult(
            documents=_documents(best, jobs, reported_total),
            parsed_jobs=parsed_jobs,
            stats=(
                self._stats(
                    entries_discovered=len(companies),
                    pages_fetched=pages_fetched,
                    parsed_jobs=len(parsed_jobs),
                ),
            ),
        )

    def _blocked_result(self, code: str) -> ProviderResult:
        if code in _ACCESS_BLOCK_ERROR_CODES:
            self._block_count += 1
        return ProviderResult(
            documents=(),
            warnings=(code,),
            stats=(self._stats(blocked_pages=1 if code in _ACCESS_BLOCK_ERROR_CODES else 0, error_code=code),),
        )

    @staticmethod
    def _stats(
        *,
        entries_discovered: int = 0,
        pages_fetched: int = 0,
        parsed_jobs: int = 0,
        blocked_pages: int = 0,
        error_code: str | None = None,
    ) -> ProviderFetchStats:
        return ProviderFetchStats(
            provider="zhipin_cdp_company",
            platform="zhipin",
            entries_discovered=entries_discovered,
            pages_fetched=pages_fetched,
            parsed_jobs=parsed_jobs,
            blocked_pages=blocked_pages,
            error_code=error_code,
        )


def _best_company_match(
    query: str, companies: tuple[ZhipinCompanyCandidate, ...]
) -> tuple[ZhipinCompanyCandidate | None, float]:
    if not companies:
        return None, 0.0
    query_variants = _name_variants(query)
    best: ZhipinCompanyCandidate | None = None
    best_score = 0.0
    for company in companies:
        company_variants = tuple(
            variant for value in _company_name_values(company) for variant in _name_variants(value)
        )
        score = max(
            (ratio(left, right) for left in query_variants for right in company_variants if left and right),
            default=0.0,
        )
        if score > best_score:
            best = company
            best_score = float(score)
    return best, best_score


def _company_name_values(company: ZhipinCompanyCandidate) -> tuple[str, ...]:
    values = [company.name]
    if company.extra:
        for key in ("brand_name", "short_name", "company_name", "legal_name"):
            value = company.extra.get(key)
            if isinstance(value, str):
                values.append(value)
    return tuple(values)


def _parse_company_candidates(payload: Mapping[str, Any]) -> tuple[ZhipinCompanyCandidate, ...]:
    records = _extract_records(payload, ("brandList", "companyList", "list", "data", "items"))
    candidates: list[ZhipinCompanyCandidate] = []
    for record in records:
        brand_id = _first_text(record, "encryptBrandId", "brandId", "brand_id", "id")
        name = _first_text(record, "brandName", "name", "companyName", "brand_name")
        if not brand_id or not name:
            continue
        url = _first_text(record, "url", "brandUrl", "companyUrl")
        if url and url.startswith("/"):
            url = f"https://www.zhipin.com{url}"
        candidates.append(
            ZhipinCompanyCandidate(
                brand_id=brand_id,
                name=name,
                url=url,
                extra={
                    "brand_name": _first_text(record, "brandName", "brand_name"),
                    "short_name": _first_text(record, "shortName", "short_name"),
                    "company_name": _first_text(record, "companyName", "company_name"),
                    "legal_name": _first_text(record, "legalName", "legal_name"),
                },
            )
        )
    return tuple(candidates)


def _parse_job_page(payload: Mapping[str, Any], *, page: int, page_size: int) -> ZhipinJobPage:
    records = _extract_records(payload, ("jobList", "brandJobList", "list", "data", "items"))
    total = _first_int(payload, "totalCount", "total", "count")
    jobs = tuple(item for record in records if (item := _parse_job_item(record)) is not None)
    has_more = bool(jobs) and (total is None or page * page_size < total)
    return ZhipinJobPage(jobs=jobs, has_more=has_more, total=total)


def _parse_job_item(record: Mapping[str, Any]) -> ZhipinJobItem | None:
    title = _first_text(record, "jobName", "jobTitle", "title", "name")
    job_id = _first_text(record, "encryptJobId", "jobId", "job_id", "id")
    url = _first_text(record, "url", "jobUrl", "job_url", "href")
    if not url and job_id:
        url = f"https://www.zhipin.com/job_detail/{quote_plus(job_id)}.html"
    if url and url.startswith("/"):
        url = f"https://www.zhipin.com{url}"
    if not title or not url:
        return None
    return ZhipinJobItem(
        title=title,
        url=url,
        job_id=job_id,
        city=_first_text(record, "cityName", "city", "city_name"),
        salary=_first_text(record, "salaryDesc", "salary", "salary_desc"),
        experience=_first_text(record, "jobExperience", "experience", "experienceName"),
        education=_first_text(record, "jobDegree", "degree", "education"),
        employment_type=_first_text(record, "employmentType", "jobType"),
        posted_at=_first_text(record, "publishTime", "updateTime", "postedAt", "posted_at"),
        description=_first_text(record, "postDescription", "description", "jobDesc"),
        raw=dict(record),
    )


def _extract_records(payload: Mapping[str, Any], names: tuple[str, ...]) -> tuple[Mapping[str, Any], ...]:
    values = [payload]
    for key in ("zpData", "data", "result"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            values.append(nested)
    for value in values:
        for name in names:
            candidate = value.get(name)
            if isinstance(candidate, list):
                return tuple(item for item in candidate if isinstance(item, Mapping))
            if isinstance(candidate, Mapping):
                nested_records = _extract_records(candidate, names)
                if nested_records:
                    return nested_records
    return ()


def _first_text(record: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return None


def _first_int(record: Mapping[str, Any], *names: str) -> int | None:
    for name in names:
        value = record.get(name)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    for nested_name in ("zpData", "data", "result"):
        nested = record.get(nested_name)
        if isinstance(nested, Mapping):
            nested_value = _first_int(nested, *names)
            if nested_value is not None:
                return nested_value
    return None


def _looks_like_login_response(payload: Mapping[str, Any]) -> bool:
    text = str(payload.get("message") or payload.get("msg") or payload.get("code") or "").lower()
    return "login" in text or "登录" in text


def _looks_like_block_response(payload: Mapping[str, Any]) -> bool:
    text = str(payload.get("message") or payload.get("msg") or payload.get("code") or "")
    return "验证" in text or "请稍候" in text or "captcha" in text.lower()


def _name_variants(value: str) -> tuple[str, ...]:
    stripped = strip_legal_suffixes(value)
    raw = (value, stripped)
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        key = normalize_for_compare(item)
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return tuple(result)


def _documents(
    company: ZhipinCompanyCandidate, jobs: list[ZhipinJobItem], reported_total: int | None
) -> tuple[RawDocument, ...]:
    url = company.url or f"https://www.zhipin.com/gongsi/{company.brand_id}.html"
    lines = [f"company={company.name}", f"brand_id={company.brand_id}"]
    if reported_total is not None:
        lines.append(f"reported_total={reported_total}")
    lines.extend(f"{item.title} {item.city or ''} {item.salary or ''} {item.url}" for item in jobs)
    return (
        RawDocument(
            provider="zhipin_cdp_company",
            external_id=company.brand_id,
            url=HttpUrl(url),
            title=f"BOSS直聘公司职位：{company.name}",
            text="\n".join(lines),
            published_at=None,
            authority_level=2,
        ),
    )


def _parsed_job(item: ZhipinJobItem) -> ParsedJob:
    salary_min_k, salary_max_k, salary_months = _guess_salary_fields(
        " ".join(v for v in (item.salary, item.title, item.description) if v)
    )
    employment_type = item.employment_type or _guess_employment_type(
        " ".join(v for v in (item.title, item.description) if v)
    )
    return ParsedJob(
        title=item.title,
        url=item.url,
        city=item.city,
        employment_type=employment_type,
        job_type=employment_type,
        salary_min_monthly=salary_min_k,
        salary_max_monthly=salary_max_k,
        salary_months=salary_months,
        description=item.description,
        posted_at=_parse_date(item.posted_at),
        provider="zhipin_cdp_company",
        source_raw_id=item.job_id or item.url,
        external_id=item.job_id,
    )


def _parse_date(value: str | None):
    if not value:
        return None
    normalized = value.replace("/", "-")
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None

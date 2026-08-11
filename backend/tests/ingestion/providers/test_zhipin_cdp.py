import json

import pytest

from app.ingestion.contracts import ProviderQuery
from app.ingestion.providers.zhipin_cdp import (
    PlaywrightZhipinCdpClient,
    ZhipinCdpClientError,
    ZhipinCdpCompanyProvider,
    ZhipinCompanyCandidate,
    ZhipinJobItem,
    ZhipinJobPage,
)


class FakeZhipinCdpClient:
    def __init__(self) -> None:
        self.company_calls: list[str] = []
        self.job_calls: list[tuple[str, int, int]] = []

    async def search_companies(self, company_name: str) -> tuple[ZhipinCompanyCandidate, ...]:
        self.company_calls.append(company_name)
        return (
            ZhipinCompanyCandidate(brand_id="wrong", name="美团配送服务商"),
            ZhipinCompanyCandidate(
                brand_id="meituan",
                name="北京三快科技有限公司",
                url="https://www.zhipin.com/gongsi/meituan.html",
                extra={"brand_name": "美团"},
            ),
        )

    async def list_company_jobs(self, brand_id: str, *, page: int, page_size: int) -> ZhipinJobPage:
        self.job_calls.append((brand_id, page, page_size))
        if page == 1:
            return ZhipinJobPage(
                jobs=(
                    ZhipinJobItem(
                        title="高级后端工程师",
                        url="https://www.zhipin.com/job_detail/1.html",
                        job_id="1",
                        city="北京",
                        salary="30-50K·15薪",
                        experience="5-10年",
                        education="本科",
                        posted_at="2026-08-01",
                    ),
                ),
                has_more=True,
                total=2,
            )
        return ZhipinJobPage(
            jobs=(
                ZhipinJobItem(
                    title="算法实习生",
                    url="https://www.zhipin.com/job_detail/2.html",
                    job_id="2",
                    city="上海",
                    salary="200-300元/天",
                    employment_type="internship",
                ),
            ),
            has_more=False,
            total=2,
        )


class FakeCdpPage:
    url = "https://www.zhipin.com/web/geek/jobs"

    def __init__(self, *, title: str = "BOSS直聘") -> None:
        self._title = title
        self.evaluations: list[dict[str, object]] = []

    async def title(self) -> str:
        return self._title

    async def goto(self, url: str, **kwargs: object) -> None:
        self.url = url

    async def evaluate(self, _script: str, payload: dict[str, object]) -> dict[str, object]:
        self.evaluations.append(payload)
        path = payload["path"]
        if path == "/wapi/zpgeek/brand/search.json":
            return {
                "status": 200,
                "url": "https://www.zhipin.com/wapi/zpgeek/brand/search.json",
                "text": json.dumps(
                    {
                        "zpData": {
                            "brandList": [
                                {
                                    "encryptBrandId": "didiglobal",
                                    "brandName": "滴滴",
                                    "companyName": "北京嘀嘀无限科技发展有限公司",
                                    "brandUrl": "/gongsi/didiglobal.html",
                                }
                            ]
                        }
                    }
                ),
            }
        return {
            "status": 200,
            "url": "https://www.zhipin.com/wapi/zpgeek/brand/joblist.json",
            "text": json.dumps(
                {
                    "zpData": {
                        "totalCount": 1,
                        "jobList": [
                            {
                                "encryptJobId": "job-1",
                                "jobName": "推荐算法工程师",
                                "cityName": "北京",
                                "salaryDesc": "40-70K·15薪",
                                "jobDegree": "本科",
                            }
                        ],
                    }
                }
            ),
        }


class BlockingZhipinCdpClient:
    def __init__(self) -> None:
        self.company_calls = 0

    async def search_companies(self, company_name: str) -> tuple[ZhipinCompanyCandidate, ...]:
        self.company_calls += 1
        raise ZhipinCdpClientError("captcha_required")

    async def list_company_jobs(self, brand_id: str, *, page: int, page_size: int) -> ZhipinJobPage:
        raise AssertionError("should not list jobs after company search block")


@pytest.mark.asyncio
async def test_playwright_zhipin_cdp_client_parses_company_search() -> None:
    page = FakeCdpPage()
    client = PlaywrightZhipinCdpClient(page=page)

    result = await client.search_companies("滴滴")

    assert result == (
        ZhipinCompanyCandidate(
            brand_id="didiglobal",
            name="滴滴",
            url="https://www.zhipin.com/gongsi/didiglobal.html",
            extra={
                "brand_name": "滴滴",
                "short_name": None,
                "company_name": "北京嘀嘀无限科技发展有限公司",
                "legal_name": None,
            },
        ),
    )
    assert page.evaluations[0]["path"] == "/wapi/zpgeek/brand/search.json"
    assert page.evaluations[0]["data"] == {"query": "滴滴", "page": 1, "pageSize": 20}


@pytest.mark.asyncio
async def test_playwright_zhipin_cdp_client_parses_company_jobs() -> None:
    page = FakeCdpPage()
    client = PlaywrightZhipinCdpClient(page=page)

    result = await client.list_company_jobs("didiglobal", page=1, page_size=30)

    assert result.total == 1
    assert result.has_more is False
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "推荐算法工程师"
    assert result.jobs[0].url == "https://www.zhipin.com/job_detail/job-1.html"
    assert result.jobs[0].salary == "40-70K·15薪"
    assert page.evaluations[0]["path"] == "/wapi/zpgeek/brand/joblist.json"
    assert page.evaluations[0]["data"] == {"brandId": "didiglobal", "page": 1, "pageSize": 30}


@pytest.mark.asyncio
async def test_playwright_zhipin_cdp_client_detects_login_page() -> None:
    client = PlaywrightZhipinCdpClient(page=FakeCdpPage(title="登录 - BOSS直聘"))

    with pytest.raises(ZhipinCdpClientError) as exc:
        await client.search_companies("滴滴")

    assert exc.value.code == "login_required"


@pytest.mark.asyncio
async def test_zhipin_cdp_provider_matches_company_and_paginates_jobs() -> None:
    client = FakeZhipinCdpClient()
    provider = ZhipinCdpCompanyProvider(enabled=True, client=client, page_size=1, max_pages=3)

    result = await provider.search(ProviderQuery(query="美团", max_results=10))

    assert result.warnings == ()
    assert len(result.documents) == 1
    assert len(result.parsed_jobs) == 2
    assert result.parsed_jobs[0].title == "高级后端工程师"
    assert result.parsed_jobs[0].salary_min_monthly == 30
    assert result.parsed_jobs[0].salary_max_monthly == 50
    assert result.parsed_jobs[0].salary_months == 15
    assert result.parsed_jobs[0].provider == "zhipin_cdp_company"
    assert result.parsed_jobs[1].employment_type == "internship"
    assert result.parsed_jobs[1].salary_min_monthly is None
    assert result.stats[0].entries_discovered == 2
    assert result.stats[0].pages_fetched == 2
    assert result.stats[0].parsed_jobs == 2
    assert client.job_calls == [("meituan", 1, 1), ("meituan", 2, 1)]


@pytest.mark.asyncio
async def test_zhipin_cdp_provider_is_disabled_by_default() -> None:
    client = FakeZhipinCdpClient()
    provider = ZhipinCdpCompanyProvider(client=client)

    result = await provider.search(ProviderQuery(query="美团"))

    assert result.documents == ()
    assert result.parsed_jobs == ()
    assert client.company_calls == []


@pytest.mark.asyncio
async def test_zhipin_cdp_provider_records_block_and_enters_cooldown() -> None:
    client = BlockingZhipinCdpClient()
    provider = ZhipinCdpCompanyProvider(
        enabled=True,
        client=client,
        platform_block_threshold=1,
    )

    first = await provider.search(ProviderQuery(query="美团"))
    second = await provider.search(ProviderQuery(query="滴滴"))

    assert first.warnings == ("captcha_required",)
    assert first.stats[0].blocked_pages == 1
    assert first.stats[0].error_code == "captcha_required"
    assert second.warnings == ("platform_cooldown",)
    assert second.stats[0].blocked_pages == 1
    assert second.stats[0].error_code == "platform_cooldown"
    assert client.company_calls == 1


@pytest.mark.asyncio
async def test_zhipin_cdp_provider_rejects_low_confidence_company_match() -> None:
    client = FakeZhipinCdpClient()
    provider = ZhipinCdpCompanyProvider(enabled=True, client=client, min_match_score=100.0)

    result = await provider.search(ProviderQuery(query="完全不同公司"))

    assert result.documents == ()
    assert result.parsed_jobs == ()
    assert result.warnings == ("company_not_matched",)
    assert result.stats[0].entries_discovered == 2
    assert result.stats[0].error_code == "company_not_matched"

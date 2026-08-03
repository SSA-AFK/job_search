from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.ingestion.contracts import ProviderQuery
from app.ingestion.errors import ProviderError
from app.ingestion.providers.zhihu import ZhihuGlobalSearchProvider


ENDPOINT = "https://developer.zhihu.com/api/v1/content/global_search"


def zhihu_payload(
    *,
    items: int = 1,
    has_more: bool = False,
    code: int = 0,
) -> dict[str, object]:
    item = {
        "Title": "Example <em>Company</em>",
        "ContentType": "Answer",
        "ContentID": "answer-123",
        "ContentText": "A <em>matching</em> result",
        "Url": "https://www.example.com/answer/123",
        "CommentCount": 3,
        "VoteUpCount": 5,
        "AuthorName": "Example Author",
        "AuthorAvatar": "https://example.com/avatar.jpg",
        "AuthorBadge": "",
        "AuthorBadgeText": "",
        "EditTime": 1_754_000_000,
        "CommentInfoList": [{"Content": "A useful comment"}],
        "AuthorityLevel": "3",
    }
    return {
        "Code": code,
        "Message": "success" if code == 0 else "upstream error",
        "Data": {"HasMore": has_more, "Items": [item for _ in range(items)]},
    }


def first_payload_item(payload: dict[str, object]) -> dict[str, object]:
    data = payload["Data"]
    assert isinstance(data, dict)
    items = data["Items"]
    assert isinstance(items, list)
    item = items[0]
    assert isinstance(item, dict)
    return item


class ExpiredAfterRequest:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        del args
        raise TimeoutError


@pytest.fixture
def frozen_time() -> datetime:
    return datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def recorded_delays() -> list[float]:
    return []


@pytest.fixture
def provider(frozen_time: datetime, recorded_delays: list[float]) -> ZhihuGlobalSearchProvider:
    async def record_sleep(delay: float) -> None:
        recorded_delays.append(delay)

    return ZhihuGlobalSearchProvider(
        enabled=True,
        access_secret="test-secret",
        clock=lambda: frozen_time,
        sleep=record_sleep,
        jitter=lambda: 0.0,
    )


@pytest.mark.anyio
async def test_encodes_filter_and_authentication(
    provider: ZhihuGlobalSearchProvider,
    frozen_time: datetime,
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=zhihu_payload()))

    result = await provider.search(
        ProviderQuery(
            query="Example Company hiring", allowed_hosts=frozenset({"zhipin.com"}), max_results=20
        )
    )

    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer test-secret"
    assert request.headers["X-Request-Timestamp"] == str(int(frozen_time.timestamp()))
    assert request.headers["Content-Type"] == "application/json"
    assert request.url.params["Query"] == "Example Company hiring"
    assert request.url.params["Count"] == "20"
    assert request.url.params["Filter"] == 'host=="zhipin.com"'
    assert request.url.params["SearchDB"] == "all"
    assert len(result.documents) == 1


@pytest.mark.anyio
async def test_caps_count_at_documented_maximum(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=zhihu_payload()))
    query = ProviderQuery.model_construct(query="Example Company", allowed_hosts=frozenset(), max_results=99)

    await provider.search(query)

    assert route.calls[0].request.url.params["Count"] == "20"


@pytest.mark.anyio
async def test_excludes_zhihu_hosts_from_documented_filter(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=zhihu_payload()))

    await provider.search(
        ProviderQuery(
            query="Example Company",
            allowed_hosts=frozenset(
                {"zhipin.com", "zhihu.com", "www.zhihu.com", "careers.example.com"}
            ),
        )
    )

    assert route.calls[0].request.url.params["Filter"] == (
        '(host=="careers.example.com" OR host=="zhipin.com")'
    )


@pytest.mark.anyio
async def test_does_not_search_when_all_allowed_hosts_are_forbidden(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=zhihu_payload()))

    result = await provider.search(
        ProviderQuery(query="Example Company", allowed_hosts=frozenset({"zhihu.com"}))
    )

    assert result.documents == ()
    assert route.call_count == 0


@pytest.mark.anyio
async def test_parses_documents_and_removes_emphasis_markup(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=zhihu_payload()))

    result = await provider.search(ProviderQuery(query="Example Company"))

    document = result.documents[0]
    assert document.provider == "zhihu_global_search"
    assert document.external_id == "answer-123"
    assert str(document.url) == "https://www.example.com/answer/123"
    assert document.title == "Example Company"
    assert document.text == "A matching result"
    assert document.published_at == datetime.fromtimestamp(1_754_000_000, tz=UTC)
    assert document.authority_level == 3


@pytest.mark.anyio
async def test_marks_result_truncated_without_requesting_a_page(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json=zhihu_payload(has_more=True))
    )

    result = await provider.search(ProviderQuery(query="Example Company"))

    assert result.truncated is True
    assert route.call_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [429, 503])
async def test_retries_retryable_statuses_three_times(
    provider: ZhihuGlobalSearchProvider,
    recorded_delays: list[float],
    respx_mock: respx.MockRouter,
    status_code: int,
) -> None:
    route = respx_mock.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(status_code),
            httpx.Response(status_code),
            httpx.Response(status_code),
            httpx.Response(200, json=zhihu_payload()),
        ]
    )

    result = await provider.search(ProviderQuery(query="Example Company"))

    assert len(result.documents) == 1
    assert route.call_count == 4
    assert recorded_delays == [0.5, 1.0, 2.0]


@pytest.mark.anyio
async def test_reports_total_deadline_expiry_without_waiting(
    frozen_time: datetime, recorded_delays: list[float], respx_mock: respx.MockRouter
) -> None:
    async def record_sleep(delay: float) -> None:
        recorded_delays.append(delay)

    provider = ZhihuGlobalSearchProvider(
        enabled=True,
        access_secret="test-secret",
        clock=lambda: frozen_time,
        sleep=record_sleep,
        jitter=lambda: 0.0,
        timeout=lambda _seconds: ExpiredAfterRequest(),
    )
    route = respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(503))

    with pytest.raises(ProviderError, match="request_timeout") as caught:
        await provider.search(ProviderQuery(query="Example Company"))

    assert caught.value.retryable is True
    assert route.call_count == 4
    assert recorded_delays == [0.5, 1.0, 2.0]


@pytest.mark.anyio
async def test_does_not_retry_non_retryable_status(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(404))

    with pytest.raises(ProviderError, match="http_status") as caught:
        await provider.search(ProviderQuery(query="Example Company"))

    assert caught.value.retryable is False
    assert route.call_count == 1


@pytest.mark.anyio
async def test_reports_invalid_json(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, text="not json"))

    with pytest.raises(ProviderError, match="invalid_json") as caught:
        await provider.search(ProviderQuery(query="Example Company"))

    assert caught.value.retryable is False


@pytest.mark.anyio
async def test_reports_out_of_range_timestamp_as_invalid_response(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    payload = zhihu_payload()
    first_payload_item(payload)["EditTime"] = 10**1000
    respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(ProviderError, match="invalid_response") as caught:
        await provider.search(ProviderQuery(query="Example Company"))

    assert caught.value.retryable is False


@pytest.mark.anyio
async def test_reports_overflowing_numeric_field_as_invalid_response(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            content=(
                b'{"Code":0,"Data":{"HasMore":false,"Items":[{"ContentID":"answer-123",'
                b'"Url":"https://www.example.com/answer/123","Title":"Company",'
                b'"ContentText":"matching","EditTime":1754000000,"AuthorityLevel":1e1000}]}}'
            ),
        )
    )

    with pytest.raises(ProviderError, match="invalid_response") as caught:
        await provider.search(ProviderQuery(query="Example Company"))

    assert caught.value.retryable is False


@pytest.mark.anyio
async def test_reports_malformed_response_schema_as_invalid_response(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    payload = zhihu_payload()
    payload["Code"] = "0"
    respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(ProviderError, match="invalid_response") as caught:
        await provider.search(ProviderQuery(query="Example Company"))

    assert caught.value.retryable is False


@pytest.mark.anyio
async def test_reports_nonzero_api_code(
    provider: ZhihuGlobalSearchProvider, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(ENDPOINT).mock(return_value=httpx.Response(200, json=zhihu_payload(code=1001)))

    with pytest.raises(ProviderError, match="api_error") as caught:
        await provider.search(ProviderQuery(query="Example Company"))

    assert caught.value.retryable is False


def test_requires_access_secret_only_when_enabled() -> None:
    ZhihuGlobalSearchProvider(enabled=False, access_secret=None)

    with pytest.raises(ProviderError, match="missing_access_secret"):
        ZhihuGlobalSearchProvider(enabled=True, access_secret=None)

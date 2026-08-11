import pytest

from app.ingestion.contracts import ProviderQuery
from app.ingestion.providers.ymicp import YmicpProvider


@pytest.mark.anyio
async def test_ymicp_uses_canonical_domain_and_returns_official_evidence() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def get_json(url: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((url, params))
        return {"code": 200, "success": True, "params": {"list": [{"domain": "example.com"}]}}

    result = await YmicpProvider(get_json=get_json).search(
        ProviderQuery(query="Example", website="https://www.example.com")
    )

    assert calls == [("http://127.0.0.1:16181/query/web", {"search": "example.com", "pageNum": 1, "pageSize": 10})]
    assert result.documents[0].provider == "ymicp"
    assert result.documents[0].authority_level == 4
    assert "example.com" in result.documents[0].text


@pytest.mark.anyio
async def test_ymicp_empty_result_is_inconclusive_not_negative() -> None:
    async def get_json(_url: str, _params: dict[str, object]) -> dict[str, object]:
        return {"code": 200, "success": True, "params": {"list": []}}

    result = await YmicpProvider(get_json=get_json).search(
        ProviderQuery(query="Example", website="https://example.com")
    )

    assert result.documents == ()
    assert result.warnings == ("ymicp_no_match",)

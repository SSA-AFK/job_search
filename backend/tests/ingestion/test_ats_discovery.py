from datetime import UTC, datetime

from pydantic import HttpUrl

from app.ingestion.contracts import RawDocument
from app.ingestion.orchestrator import _scan_ats_career_urls


def _document(title: str, text: str) -> RawDocument:
    return RawDocument(
        provider="zhihu_global_search",
        external_id="result-1",
        url=HttpUrl("https://www.zhihu.com/question/1"),
        title=title,
        text=text,
        published_at=datetime.now(UTC),
        authority_level=1,
    )


def test_ats_scan_keeps_only_urls_from_matching_company_documents() -> None:
    documents = (
        _document("重庆铱石科技招聘", "重庆铱石科技（集团）有限公司招聘 https://jobs.feishu.cn/acme/position"),
        _document("其他公司招聘", "其他公司 https://app.mokahr.com/campus-recruitment/other"),
    )

    candidates = _scan_ats_career_urls(documents, "重庆铱石科技集团有限公司")

    assert [candidate.url for candidate in candidates] == ["https://jobs.feishu.cn/acme/position"]

from datetime import UTC, datetime

from app.ingestion.contracts import RawDocument
from app.ingestion.extraction.schemas import CompanyCandidate
from app.ingestion.normalization.company import normalize_company
from app.ingestion.persistence.contracts import (
    NormalizedBatch,
    NormalizedCompanyRecord,
    NormalizedDocument,
)
from app.rankings.official_profile import attach_official_ai_profile_hints


def _batch(text: str) -> NormalizedBatch:
    document = NormalizedDocument(
        evidence_id="official-1",
        document=RawDocument(
            provider="official_news",
            external_id=None,
            url="https://example.com/products",
            title="官方动态",
            text=text,
            published_at=None,
            authority_level=1,
        ),
        fetched_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    company = NormalizedCompanyRecord(
        candidate=normalize_company(
            CompanyCandidate(
                name="Example AI",
                website="https://example.com",
                evidence_ids=["official-1"],
                confidence=0.9,
            )
        ),
        company_id=None,
    )
    return NormalizedBatch(documents=(document,), company=company, collected_at=document.fetched_at)


def test_official_documents_create_pending_evidence_linked_hints() -> None:
    enriched = attach_official_ai_profile_hints(
        _batch("人工智能大模型产品发布，服务客户案例，完成合作签约。")
    )

    assert {item.field_key for item in enriched.profile_fields} == {
        "ai.track",
        "ai.core_level",
        "ai.products",
        "ai.market_proofs",
        "ai.technology_signals",
        "ai.growth_events",
    }
    assert {item.source_evidence_id for item in enriched.profile_fields} == {"official-1"}
    assert all(
        item.verification_status.value == "pending_verification" for item in enriched.profile_fields
    )


def test_unrelated_documents_do_not_create_hints() -> None:
    assert attach_official_ai_profile_hints(_batch("公司年度团建通知")).profile_fields == ()

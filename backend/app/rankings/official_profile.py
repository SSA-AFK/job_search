"""Conservative, evidence-linked AI profile hints from first-party documents."""

from app.ingestion.persistence.contracts import (
    NormalizedBatch,
    NormalizedDocument,
    NormalizedProfileFieldRecord,
)
from app.models import VerificationStatus

_SIGNALS = {
    "ai.track": ("人工智能", "大模型", "生成式", "机器学习", "ai"),
    "ai.core_level": ("人工智能", "大模型", "生成式", "机器学习", "ai"),
    "ai.products": ("产品", "平台", "解决方案", "模型"),
    "ai.market_proofs": ("客户", "案例", "合作", "签约", "落地"),
    "ai.technology_signals": ("模型", "算法", "开源", "专利", "备案"),
    "ai.growth_events": ("融资", "发布", "上线", "扩张", "签约"),
}


def attach_official_ai_profile_hints(batch: NormalizedBatch) -> NormalizedBatch:
    """Add pending-verification hints only when a first-party document contains a signal."""
    existing = {field.field_key for field in batch.profile_fields}
    additions: list[NormalizedProfileFieldRecord] = []
    for field_key, terms in _SIGNALS.items():
        if field_key in existing:
            continue
        document = _first_matching_document(batch.documents, terms)
        if document is None:
            continue
        additions.append(
            NormalizedProfileFieldRecord(
                field_key=field_key,
                value={"title": document.document.title, "url": str(document.document.url)},
                source_evidence_id=document.evidence_id,
                verification_status=VerificationStatus.PENDING_VERIFICATION,
            )
        )
    return batch.model_copy(update={"profile_fields": (*batch.profile_fields, *additions)})


def _first_matching_document(
    documents: tuple[NormalizedDocument, ...], terms: tuple[str, ...]
) -> NormalizedDocument | None:
    for document in documents:
        text = f"{document.document.title or ''}\n{document.document.text}".lower()
        if any(term in text for term in terms):
            return document
    return None

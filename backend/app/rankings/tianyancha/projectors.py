"""Allow-list projectors for ranking-relevant Tianyancha responses."""

import json
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any

from app.core.normalization import normalize_name
from app.rankings.gap_plan import EnrichmentCategory
from app.rankings.tianyancha.contracts import JsonValue, ProjectedSignal

_AI_TERMS = (
    "人工智能",
    "大模型",
    "机器学习",
    "深度学习",
    "神经网络",
    "自然语言",
    "计算机视觉",
    "智能驾驶",
    "机器人",
    "算法",
    "ai",
    "llm",
)
_MATERIAL_RISK_TYPES = {
    "经营异常",
    "严重违法",
    "失信被执行人",
    "被执行人",
    "限制高消费",
    "破产重整",
    "清算",
    "行政处罚",
}


def project_response(
    category: EnrichmentCategory,
    payload: dict[str, Any],
    *,
    company_name: str,
    window_start: date,
    company_aliases: frozenset[str] | None = None,
) -> tuple[ProjectedSignal, ...]:
    responses = payload.get("responses")
    if isinstance(responses, list):
        signals: list[ProjectedSignal] = []
        for response in responses:
            if not isinstance(response, dict):
                continue
            tool = response.get("tool")
            nested = response.get("payload")
            if not isinstance(tool, str) or not isinstance(nested, dict):
                continue
            signals.extend(
                _project_tool(
                    category, tool, nested, company_name, window_start, company_aliases
                )
            )
        return tuple(signals)
    return _project_tool(category, "", payload, company_name, window_start, company_aliases)


def _project_tool(
    category: EnrichmentCategory,
    tool: str,
    payload: dict[str, Any],
    company_name: str,
    window_start: date,
    company_aliases: frozenset[str] | None,
) -> tuple[ProjectedSignal, ...]:
    if category == EnrichmentCategory.GROWTH:
        return _growth(payload, window_start)
    if category == EnrichmentCategory.INTELLECTUAL_PROPERTY:
        if tool == "software-copyright-info":
            return _software_copyrights(payload, window_start)
        return _patents(payload, window_start)
    if category == EnrichmentCategory.MARKET_VALIDATION:
        if tool == "qualifications":
            return _qualifications(payload)
        return _bids(payload, company_name, window_start, company_aliases)
    if category == EnrichmentCategory.MATERIAL_RISK:
        return _risk(payload)
    raise ValueError(f"unsupported enrichment category: {category}")


def _software_copyrights(
    payload: dict[str, Any], window_start: date
) -> tuple[ProjectedSignal, ...]:
    signals = []
    for item in _items(payload):
        event_date = _as_date(item.get("regtime"))
        title = _text(item.get("fullname"))
        if event_date is None or event_date < window_start or not _is_ai_related(title):
            continue
        value: dict[str, JsonValue] = {"title": title[:300], "version": _text(item.get("version"))}
        signals.append(
            _signal(
                EnrichmentCategory.INTELLECTUAL_PROPERTY,
                "ai_software_copyright",
                value,
                event_date,
            )
        )
    return tuple(signals)


def _qualifications(payload: dict[str, Any]) -> tuple[ProjectedSignal, ...]:
    signals = []
    for item in _source_items(payload, "cert"):
        start = _as_date(item.get("startDate"))
        end = _as_date(item.get("endDate"))
        if end is not None and end < datetime.now(UTC).date():
            continue
        value: dict[str, JsonValue] = {
            "name": _text(item.get("certificateName"))[:300],
            "type": _text(item.get("certificateType"))[:200],
            "valid_until": end.isoformat() if end is not None else None,
        }
        signals.append(
            _signal(
                EnrichmentCategory.MARKET_VALIDATION,
                "active_qualification",
                value,
                start,
            )
        )
    return tuple(signals)


def _growth(payload: dict[str, Any], window_start: date) -> tuple[ProjectedSignal, ...]:
    items = _source_items(payload, "rongzi")
    signals = []
    for item in items:
        event_date = _as_date(item.get("date") or item.get("pubTime"))
        if event_date is None or event_date < window_start:
            continue
        value: dict[str, JsonValue] = {
            "round": _text(item.get("round")),
            "investors": _organizations(item.get("investorName")),
        }
        signals.append(_signal(EnrichmentCategory.GROWTH, "financing", value, event_date))
    return tuple(signals)


def _patents(payload: dict[str, Any], window_start: date) -> tuple[ProjectedSignal, ...]:
    signals = []
    for item in _items(payload):
        event_date = _as_date(item.get("applicationTime") or item.get("pubDate"))
        title = _text(item.get("patentName") or item.get("title"))
        classification = _text(item.get("mainCatNum"))
        if event_date is None or event_date < window_start or not _is_ai_related(title):
            continue
        value: dict[str, JsonValue] = {
            "title": title[:300],
            "classification": classification,
            "patent_type": _text(item.get("patentType")),
            "status": _text(item.get("patentStatus")),
        }
        signals.append(
            _signal(
                EnrichmentCategory.INTELLECTUAL_PROPERTY,
                "ai_invention_patent",
                value,
                event_date,
            )
        )
    return tuple(signals)


def _bids(
    payload: dict[str, Any],
    company_name: str,
    window_start: date,
    company_aliases: frozenset[str] | None = None,
) -> tuple[ProjectedSignal, ...]:
    # Normalize all known aliases plus the primary company_name. Tianyancha records
    # the winning bidder using any of the company's legal/brand/short forms, so we
    # must check against the full normalized alias set instead of a single name.
    alias_set: set[str] = {company_name}
    if company_aliases:
        alias_set.update(company_aliases)
    normalized_company_names = frozenset(normalize_name(name) for name in alias_set if name)
    signals = []
    for item in _items(payload):
        event_date = _as_date(item.get("publishTime"))
        winners = _organization_names(item.get("bidWinner"))
        is_winner = item.get("enterpriseIdentity") == "中标方" or any(
            normalize_name(winner) in normalized_company_names for winner in winners
        )
        if event_date is None or event_date < window_start or not is_winner:
            continue
        value: dict[str, JsonValue] = {
            "title": _text(item.get("title"))[:500],
            "purchaser": _text(item.get("purchaser"))[:200],
            "stage": _text(item.get("stage"))[:100],
        }
        signals.append(
            _signal(EnrichmentCategory.MARKET_VALIDATION, "winning_bid", value, event_date)
        )
    return tuple(signals)


def _risk(payload: dict[str, Any]) -> tuple[ProjectedSignal, ...]:
    signals = []
    tool_risks = payload.get("toolRisks")
    if not isinstance(tool_risks, list):
        return ()
    for item in tool_risks:
        if not isinstance(item, dict):
            continue
        risk_type = _text(item.get("riskType"))
        if not any(term in risk_type for term in _MATERIAL_RISK_TYPES):
            continue
        count = item.get("count")
        value: dict[str, JsonValue] = {
            "risk_type": risk_type,
            "risk_level": _text(item.get("riskLevel")),
            "count": count if isinstance(count, int) else None,
        }
        signals.append(_signal(EnrichmentCategory.MATERIAL_RISK, "material_risk", value, None))
    return tuple(signals)


def _signal(
    category: EnrichmentCategory,
    signal_key: str,
    value: dict[str, JsonValue],
    event_date: date | None,
) -> ProjectedSignal:
    fingerprint = sha256(
        json.dumps(
            {"key": signal_key, "value": value, "date": str(event_date)},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return ProjectedSignal(category, signal_key, value, event_date, fingerprint)


def _items(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    items = payload.get("items")
    if not isinstance(items, list):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _source_items(payload: dict[str, Any], key: str) -> tuple[dict[str, Any], ...]:
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return ()
    source = sources.get(key)
    return _items(source) if isinstance(source, dict) else ()


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _organizations(value: object) -> list[JsonValue]:
    return list(_organization_names(value))


def _organization_names(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return [part.strip()[:200] for part in value.replace("，", ",").split(",") if part.strip()]


def _is_ai_related(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _AI_TERMS)

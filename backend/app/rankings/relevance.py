"""Deterministic AI-relevance signals derived from ephemeral business scope text."""

from dataclasses import dataclass

_AI_SCOPE_TERMS = (
    "人工智能",
    "大模型",
    "生成式人工智能",
    "机器学习",
    "深度学习",
    "神经网络",
    "自然语言处理",
    "计算机视觉",
    "智能驾驶",
    "智能机器人",
    "算法开发",
    "artificial intelligence",
    "machine learning",
    "large language model",
)


@dataclass(frozen=True)
class AiScopeAssessment:
    is_ai_related: bool
    matched_term_count: int


def assess_ai_business_scope(scope: str | None) -> AiScopeAssessment:
    """Return a minimized assessment without retaining the source scope text."""
    lowered = (scope or "").lower()
    matches = {term for term in _AI_SCOPE_TERMS if term in lowered}
    return AiScopeAssessment(bool(matches), len(matches))

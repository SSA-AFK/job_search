from app.rankings.relevance import assess_ai_business_scope


def test_ai_scope_assessment_is_deterministic_and_minimized() -> None:
    result = assess_ai_business_scope("人工智能软件开发；机器学习平台服务")

    assert result.is_ai_related
    assert result.matched_term_count == 2
    assert not hasattr(result, "source_text")


def test_generic_software_or_intelligence_word_does_not_prove_ai_relevance() -> None:
    assert not assess_ai_business_scope("软件开发及技术咨询").is_ai_related
    assert not assess_ai_business_scope("智能家居设备销售").is_ai_related

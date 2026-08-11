import pytest

from app.ingestion.identity_matching import match_company_name


@pytest.mark.parametrize("requested, observed", [("重庆铱石科技（集团）有限公司", "重庆铱石科技集团有限公司"), ("Example AI Co., Ltd.", "Example AI Co Ltd")])
def test_accepts_normalized_or_high_similarity_company_names(requested: str, observed: str) -> None:
    assert match_company_name(requested, observed).accepted is True


def test_rejects_low_similarity_company_names() -> None:
    match = match_company_name("重庆铱石科技（集团）有限公司", "广东龙达数智信息技术有限公司")
    assert match.accepted is False
    assert match.score < 92

import pytest

from app.core.normalization import normalize_name, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  DeepSeek（深度求索） ", "deepseek(深度求索)"), ("示 例 科技", "示例科技")],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" HTTPS://Example.COM/jobs/1#apply ", "https://example.com/jobs/1"),
        ("https://example.com/", "https://example.com/"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://user@example.com/logo.png",
        "https://user:password@example.com/logo.png",
    ],
)
def test_normalize_url_rejects_userinfo(raw: str) -> None:
    with pytest.raises(ValueError, match="credentials"):
        normalize_url(raw)

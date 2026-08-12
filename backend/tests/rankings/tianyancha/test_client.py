from app.rankings.tianyancha.client import _error_code


def test_cli_http_402_maps_to_quota_exhausted() -> None:
    assert (
        _error_code("请求失败: core/tools/call HTTP 402\n今日有效工具调用次数已用完")
        == "tianyancha_quota_exhausted"
    )

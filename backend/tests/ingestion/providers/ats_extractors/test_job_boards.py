"""单元测试：猎聘 / 拉勾 HTML 解析器 + 字段提取。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.jobs.contracts import AtsParseStatus
from app.ingestion.jobs.parser import (
    _guess_city,
    _guess_employment_type,
    _guess_salary_fields,
    parse_html_job_list,
)

_DATA = Path("data/ats")


# ---------------------------------------------------------------------------
# Salary / city / employment-type helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "min_k", "max_k", "months"),
    [
        ("25-50K·16薪", 25, 50, 16),
        ("15-25k", 15, 25, None),
        ("2-3.5万·14薪", 20, 35, 14),
        ("1.5-2.5w", 15, 25, None),
        ("8-12千", 8, 12, None),
        ("150-200/天", None, None, None),  # 日薪
        ("面议", None, None, None),
        ("30-60万/年", None, None, None),  # 年薪 → 保守跳过
        ("1-501K", None, None, None),  # 超范围
    ],
)
def test_salary_guess(
    text: str, min_k: int | None, max_k: int | None, months: int | None
) -> None:
    assert _guess_salary_fields(text) == (min_k, max_k, months)


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("北京 朝阳区 望京", "北京"),
        ("杭州 · 余杭区 阿里巴巴附近", "杭州"),
        ("remote worldwide", "远程"),
        ("深圳市南山区科技园1号线", "深圳"),
        ("无城市信息随机文本", None),
    ],
)
def test_city_guess(text: str, city: str | None) -> None:
    assert _guess_city(text) == city


@pytest.mark.parametrize(
    ("text", "etype"),
    [
        ("全职 3-5年 硕士", "full_time"),
        ("校招 应届毕业生 全职", "full_time"),
        ("产品实习生 4天/周", "internship"),
        ("小时工 兼职", "part_time"),
        ("劳务派遣 临时", "temporary"),
        ("随机标签 无 类型", None),
    ],
)
def test_employment_guess(text: str, etype: str | None) -> None:
    assert _guess_employment_type(text) == etype


# ---------------------------------------------------------------------------
# Liepin
# ---------------------------------------------------------------------------

def test_liepin_parser_extracts_three_jobs() -> None:
    html = (_DATA / "liepin_list.html").read_text(encoding="utf-8")
    result = parse_html_job_list(html, platform="liepin")
    assert result.status == AtsParseStatus.SUCCEEDED
    assert result.observed_count == 3
    titles = [c.title for c in result.candidates]
    assert any("高级后端工程师" in t for t in titles)
    assert any("数据分析师" in t for t in titles)
    assert any("HR 实习生" in t for t in titles)

    analyst = next(c for c in result.candidates if "数据分析师" in c.title)
    assert analyst.city == "北京"
    assert analyst.employment_type == "full_time"
    assert analyst.raw_attributes.get("salary_min_k") == "18"
    assert analyst.raw_attributes.get("salary_max_k") == "30"

    hr = next(c for c in result.candidates if "HR 实习" in c.title)
    assert hr.city == "远程"
    assert hr.employment_type == "internship"


# ---------------------------------------------------------------------------
# Lagou
# ---------------------------------------------------------------------------

def test_lagou_parser_extracts_three_jobs() -> None:
    html = (_DATA / "lagou_list.html").read_text(encoding="utf-8")
    result = parse_html_job_list(html, platform="lagou")
    assert result.status == AtsParseStatus.SUCCEEDED
    assert result.observed_count == 3

    ios = next(c for c in result.candidates if "iOS" in c.title)
    assert ios.city == "北京"
    assert ios.raw_attributes.get("salary_min_k") == "25"
    assert ios.raw_attributes.get("salary_max_k") == "45"

    algo = next(c for c in result.candidates if "多媒体" in c.title)
    assert algo.city == "上海"
    assert algo.raw_attributes.get("salary_min_k") == "40"
    assert algo.raw_attributes.get("salary_max_k") == "70"
    assert algo.raw_attributes.get("salary_months") == "15"

    ambassador = next(c for c in result.candidates if "校园大使" in c.title)
    assert ambassador.city == "广州"
    assert ambassador.employment_type == "part_time"


# ---------------------------------------------------------------------------
# Platform guards: wrong platform -> no candidates
# ---------------------------------------------------------------------------

def test_wrong_platform_returns_partial_not_raises() -> None:
    html = (_DATA / "liepin_list.html").read_text(encoding="utf-8")
    result = parse_html_job_list(html, platform="feishu")
    assert result.status == AtsParseStatus.PARTIAL
    assert result.observed_count == 0
    assert result.error_code == "no_candidates"


def test_empty_html_fails_cleanly() -> None:
    result = parse_html_job_list("", platform="liepin")
    assert result.status == AtsParseStatus.FAILED
    assert result.error_code == "parse_failed"
    assert result.candidates == ()

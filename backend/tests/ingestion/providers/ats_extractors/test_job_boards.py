"""单元测试：BOSS直聘 / 猎聘 / 拉勾 HTML 解析器 + 字段提取。"""
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
# Zhipin
# ---------------------------------------------------------------------------

def test_zhipin_parser_extracts_three_jobs() -> None:
    html = (_DATA / "zhipin_list.html").read_text(encoding="utf-8")
    result = parse_html_job_list(html, platform="zhipin")
    assert result.status == AtsParseStatus.SUCCEEDED
    assert result.observed_count == 3
    titles = [c.title for c in result.candidates]
    assert "高级算法工程师 (NLP)" in titles
    assert "产品实习生" in titles
    assert "前端开发工程师" in titles

    algo = next(c for c in result.candidates if "算法" in c.title)
    assert algo.city == "北京"
    assert algo.employment_type == "full_time"
    assert algo.raw_attributes.get("salary_min_k") == "25"
    assert algo.raw_attributes.get("salary_max_k") == "50"
    assert algo.raw_attributes.get("salary_months") == "16"

    intern = next(c for c in result.candidates if "实习生" in c.title)
    assert intern.city == "上海"
    assert intern.employment_type == "internship"
    # 日薪被保护跳过，无 K 值
    assert intern.raw_attributes.get("salary_min_k") is None

    fe = next(c for c in result.candidates if "前端" in c.title)
    assert fe.city == "深圳"
    # 2-3.5万 → 20K-35K
    assert fe.raw_attributes.get("salary_min_k") == "20"
    assert fe.raw_attributes.get("salary_max_k") == "35"
    assert fe.raw_attributes.get("salary_months") == "14"


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
    html = (_DATA / "zhipin_list.html").read_text(encoding="utf-8")
    # feishu selectors won't match zhipin HTML → partial, no candidates, no raise
    result = parse_html_job_list(html, platform="feishu")
    assert result.status == AtsParseStatus.PARTIAL
    assert result.observed_count == 0
    assert result.error_code == "no_candidates"


def test_zhipin_real_company_mobile_snapshot_meituan_and_didi() -> None:
    html = """
    <html><body><ul class="job-list-box">
      <li class="job-card-wrapper">
        <a class="job-name" href="https://m.zhipin.com/job_detail/47d26b77ad245d230nd73Nu-F1pT.html">美团BD销售</a>
        <span class="salary">8-13K·13薪</span>
        <div class="job-area">成都武侯区科华北路</div>
        <div class="tag-list">1-3年 大专 全职</div>
        <a href="https://m.zhipin.com/gongsi/b633a34f787d94f21nZ_0929E1I~.html">美团</a>
      </li>
      <li class="job-card-wrapper">
        <a class="job-name" href="https://m.zhipin.com/job_detail/65677bde329d768b0nFy2NW1ElZQ.html">高级专家工程师（乘客推荐引擎方向）</a>
        <span class="salary">55-85K·15薪</span>
        <div class="job-area">北京海淀区上地</div>
        <div class="tag-list">10年以上 本科 全职</div>
        <a href="https://m.zhipin.com/gongsi/8548fadc0b5c265403V93t61EQ~~.html">滴滴</a>
      </li>
      <li class="job-card-wrapper">
        <a class="job-name" href="https://m.zhipin.com/job_detail/ec44da489b4627ce0nJ409-6F1FZ.html">潜力市场-产品与用户运营实习生</a>
        <span class="salary">150-200元/天</span>
        <div class="job-area">成都成华区建设路</div>
        <div class="tag-list">本科 实习</div>
        <a href="https://m.zhipin.com/gongsi/8548fadc0b5c265403V93t61EQ~~.html">滴滴</a>
      </li>
    </ul></body></html>
    """
    result = parse_html_job_list(html, platform="zhipin")
    assert result.status == AtsParseStatus.SUCCEEDED
    assert result.observed_count == 3

    meituan = next(c for c in result.candidates if "美团BD" in c.title)
    assert meituan.city == "成都"
    assert meituan.employment_type == "full_time"
    assert meituan.raw_attributes.get("salary_min_k") == "8"
    assert meituan.raw_attributes.get("salary_max_k") == "13"
    assert meituan.raw_attributes.get("salary_months") == "13"

    didi = next(c for c in result.candidates if "乘客推荐" in c.title)
    assert didi.city == "北京"
    assert didi.raw_attributes.get("salary_min_k") == "55"
    assert didi.raw_attributes.get("salary_max_k") == "85"
    assert didi.raw_attributes.get("salary_months") == "15"

    intern = next(c for c in result.candidates if "实习生" in c.title)
    assert intern.city == "成都"
    assert intern.employment_type == "internship"
    assert intern.raw_attributes.get("salary_min_k") is None


def test_empty_html_fails_cleanly() -> None:
    result = parse_html_job_list("", platform="zhipin")
    assert result.status == AtsParseStatus.FAILED
    assert result.error_code == "parse_failed"
    assert result.candidates == ()

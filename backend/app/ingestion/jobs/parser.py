# backend/app/ingestion/jobs/parser.py
import hashlib
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from app.ingestion.jobs.contracts import AtsJobCandidate, AtsListResult, AtsParseStatus

# (card_selector, link_selector)  per platform
_PLATFORM_SELECTORS: dict[str, tuple[str, str]] = {
    "feishu": (".positionItem, div.job-card, li.job-item, [data-job-id]", "a"),
    "moka": ("a.link-abc, .link-abc, div.position-list-item, li.position-item, [data-position-id]", "a"),
    "zhipin": (
        (
            "li.job-card-wrapper, div.job-list-box li, div.job-card, [ka='search_list'] li, "
            ".search-job-result li, .job-lists li"
        ),
        "a.job-name, .job-card-left a, a[href*='/job_detail/']",
    ),
    "liepin": (
        (
            "div.sojob-item, div.job-list-item, div.search-result-list div.job-item, "
            "li.job-list-item, .job-list-view > section, div[data-position-id]"
        ),
        "a.job-title, h3.job-title a, a[href*='/job/'], a[href*='liepin.com/job']",
    ),
    "lagou": (
        (
            "div.job-box, li.con_list_item, a.position_link, div.list_item_top, "
            "div.passed_bar, .job-list li, div.job-card"
        ),
        "a.position_link, a.s-top-name, h3 a, a[href*='/jobs/'], a[href*='lagou.com/jobs']",
    ),
    "bytedance": (
        (
            ".positionItem, a[href*='/detail'], a[href*='position/'], "
            ".job-list-item, div[class*='job-card'], li[class*='job-item']"
        ),
        "a",
    ),
    "generic": ("a.job-card, .job-listing a, .position a", "a"),
}


# ---------------------------------------------------------------------------
# Salary / city / employment-type extraction helpers (pure rules)
# ---------------------------------------------------------------------------

_SALARY_RANGE_RE = re.compile(
    r"(?P<min>\d+(?:\.\d+)?)\s*"
    r"(?P<min_unit>K|k|千|万|w|W|元)?"
    r"\s*[-~到至×Xx*]\s*"
    r"(?P<max>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>K|k|千|万|w|W|元|薪)?"
)
_MONTHS_RE = re.compile(r"(?P<n>\d{1,2})\s*薪")


def _guess_salary_fields(text: str) -> tuple[int | None, int | None, int | None]:
    """Try to infer (salary_min_monthly_k, salary_max_monthly_k, months) from a card's string.

    单位归一化：
      15-25K / 15-25k → (15, 25, None)
      1.5-2.5万 / 1.5-2.5w → (15, 25, None)   # 换算成 K
      15-25K·14薪 / 15-25K 14薪 → (15, 25, 14)
      8千-1.2万 → 单位混杂，忽略（保守）
      150-200/天、200元/天 → 日薪，直接跳过
      30-60万/年 → 年薪，保守跳过
    """
    if not text:
        return None, None, None
    t = text
    # 日薪 / 年薪 → 明确不是月薪范围，提前拒绝
    if any(k in t for k in ("/天", "元/天", "每天", "日结", "/日", "日薪")):
        return None, None, None
    if any(k in t for k in ("万/年", "w/年", "W/年", "元/年", "每年", "/年", "年薪")):
        return None, None, None
    months_match = _MONTHS_RE.search(t)
    months = int(months_match.group("n")) if months_match else None

    # 扫描所有匹配，优先找"带明确货币单位"的合法薪资，避免误命中 3-5年 之类经验
    matches = list(_SALARY_RANGE_RE.finditer(t))
    salary_match = None
    for m in matches:
        min_unit = (m.group("min_unit") or "").lower()
        unit = (m.group("unit") or "").lower()
        combined_unit = min_unit or unit
        start = m.start()
        end = m.end()
        # 上下文排除：紧邻 "年/经验/个月/天" 表示这是经验/年限不是薪资
        post_ctx = t[end:end + 3]
        pre_ctx = t[max(0, start - 2):start]
        if any(k in post_ctx for k in ("年", "月", "经验", "天")) and not combined_unit:
            continue
        if any(k in pre_ctx for k in ("经验", "工作", "不限")) and not combined_unit:
            continue
        # 有单位的匹配优先
        if combined_unit in {"k", "千", "万", "w"}:
            salary_match = m
            break
        if salary_match is None:
            salary_match = m

    if salary_match is None:
        return None, None, months
    m = salary_match
    try:
        low = float(m.group("min"))
        high = float(m.group("max"))
    except (TypeError, ValueError):
        return None, None, months
    min_unit = (m.group("min_unit") or "").lower()
    unit = (m.group("unit") or "").lower()
    combined_unit = min_unit or unit
    # 前后单位不一致（如 8千-1.2万）时保守丢弃
    if min_unit and unit and min_unit != unit:
        if {min_unit, unit} <= {"k", "千"} or {min_unit, unit} <= {"万", "w"}:
            # 大小写或同义差异可接受
            pass
        else:
            return None, None, months
    if combined_unit in {"万", "w"}:
        low *= 10
        high *= 10
    elif combined_unit in {"千", "元"}:
        # 千: 8-12千 = 8k-12k; 元通常是日/面议，不处理
        if combined_unit == "千":
            pass  # 已是 K 级别
        else:
            return None, None, months
    elif not combined_unit:
        # 完全无单位的裸数字（如 25-50）不采信，避免误匹配
        return None, None, months
    # 超出合理范围的月薪（> 500K 或 < 0.5K 或 low > high）直接丢弃，避免误解析
    if high < 0.5 or high > 500 or low > 500 or low > high:
        return None, None, months
    return round(low), round(high), months


def _guess_city(text: str) -> str | None:
    """Very light-weight city extraction from raw card text.

    返回第一个命中的城市名（按长度优先，避免"南"命中"南通"前）。
    不依赖外部词表，只抓最常见的一二线城市，避免过度猜测。
    """
    if not text:
        return None
    cities = (
        "北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "西安", "重庆", "天津",
        "苏州", "青岛", "长沙", "郑州", "合肥", "福州", "厦门", "东莞", "宁波", "无锡",
        "佛山", "沈阳", "大连", "济南", "石家庄", "哈尔滨", "昆明", "南昌", "南宁", "温州",
        "贵阳", "泉州", "珠海", "长春", "太原", "中山", "嘉兴", "常州", "徐州", "南通",
        "金华", "保定", "惠州", "台州", "绍兴", "烟台", "潍坊", "洛阳", "唐山", "海口",
        "兰州", "银川", "西宁", "呼和浩特", "乌鲁木齐", "拉萨",
        "香港", "澳门", "台北", "新加坡", "东京", "西雅图", "旧金山", "纽约", "伦敦",
        "远程", "Remote", "remote", "异地",
    )
    # 按长度降序，先命中较长名称（如 乌鲁木齐 优先于 乌？）
    for city in sorted(cities, key=len, reverse=True):
        if city in text:
            if city in {"Remote", "remote"}:
                return "远程"
            return city
    return None


def _guess_employment_type(text: str) -> str | None:
    if not text:
        return None
    t = text.lower()
    if any(k in text for k in ("实习", "实习生")) or "intern" in t:
        return "internship"
    if any(k in text for k in ("兼职", "小时工", "钟点工")) or "part" in t:
        return "part_time"
    if any(k in text for k in ("临时", "外包", "劳务派遣")):
        return "temporary"
    if any(k in text for k in ("全职", "正式", "校招", "社招", "经验不限")) or "full" in t:
        return "full_time"
    return None


# ---------------------------------------------------------------------------
# Card-level attribute extraction per platform
# ---------------------------------------------------------------------------

def _extract_card_attributes(card: Tag, platform: str) -> dict[str, str]:
    """Return a flat dict of raw attributes from the card node, e.g. salary/city/area."""
    if platform == "zhipin":
        return {
            "salary": _text(card.select_one(".salary, .job-salary, span.red, [class*='salary']")),
            "city": _text(card.select_one(".job-area, .area-wrapper, [class*='job-area'], .info-area")),
            "tags": _text(card.select_one(".tag-list, .job-labels, [class*='tag']")),
            "title_extra": _text(card.select_one(".job-info, .job-title-box")),
        }
    if platform == "liepin":
        return {
            "salary": _text(card.select_one(".job-salary, .text-warning, [class*='salary'], span.compensate")),
            "city": _text(card.select_one(".job-area, .area, [class*='area'], span.ellipsis-1")),
            "labels": _text(card.select_one(".job-labels, .labels-box, [class*='label']")),
            "title_extra": _text(card.select_one(".job-title-box")),
        }
    if platform == "lagou":
        return {
            "salary": _text(card.select_one(".money, .salary, [class*='money'], span.salary")),
            "city": _text(card.select_one(".add, [class*='add'], span.add, .job-address")),
            "tags": _text(card.select_one(".industry, .position-label, [class*='con_list_item']")),
            "title_extra": _text(card.select_one(".position-name, h3, [class*='top']")),
        }
    if platform == "bytedance":
        return {
            "city": _text(card.select_one("[class*='location'], [class*='city'], .job-location")),
            "tags": _text(card.select_one("[class*='tag'], [class*='label'], .job-tags")),
            "title_extra": _text(card.select_one("[class*='title'], .job-name, .job-subtitle")),
        }
    return {}


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return node.get_text(" ", strip=True)


def _best_effort_title(card: Tag, link: Tag, platform: str) -> str:
    """Get job title with some per-platform fallbacks."""
    title_selectors = {
        "zhipu": [".job-name", ".job-title", "h3", "h4", ".title"],  # typo kept accidentally; covered by generic
        "zhipin": [".job-name", ".job-title-box .job-name", "h3 .name", "span.job-name", ".job-title", "h3", "h4"],
        "liepin": [".job-title", "h3.job-title", "span.title", "h3 a", "h4", ".title"],
        "lagou": [".position-name", "h3.position-name", ".s-top-name", "h3", ".job-name"],
    }
    for selector in title_selectors.get(platform, [".job-title", ".position-name", "h3", "h4"]):
        n = card.select_one(selector)
        if n and n.get_text(strip=True):
            return n.get_text(strip=True)
    # Fallback to the link node itself; re-use parser logic
    title_el = (
        link.select_one(".positionItem-title-text")
        or link.select_one("[class*='title-']")
        or link
    )
    return title_el.get_text(strip=True)


def parse_html_job_list(html: str, platform: str) -> AtsListResult:
    if not html or not html.strip():
        return AtsListResult(candidates=(), status=AtsParseStatus.FAILED, error_code="parse_failed")
    try:
        soup = BeautifulSoup(html, "html.parser")
        card_selector, link_selector = _PLATFORM_SELECTORS.get(
            platform, _PLATFORM_SELECTORS["generic"]
        )
        candidates: list[AtsJobCandidate] = []
        seen_hrefs: set[str] = set()
        for card in soup.select(card_selector):
            link = card if card.name == "a" else card.select_one(link_selector)
            if link is None:
                continue
            href_val = link.get("href", "")
            href = href_val[0] if isinstance(href_val, list) else href_val
            if not isinstance(href, str):
                continue
            href_key = href.strip()
            if not href_key or href_key in seen_hrefs:
                continue
            seen_hrefs.add(href_key)

            # Title (platform-aware selectors + link fallback)
            title = _best_effort_title(card, link, platform)[:500]
            if not title:
                continue

            url = href if href.startswith("http") else urljoin("https://example.com", href)

            # Platform-specific structured attributes
            attrs = _extract_card_attributes(card, platform)

            # Aggregate all text blobs for city/type/salary inference
            blob_parts: list[str] = [title]
            if platform in {"zhipin", "liepin", "lagou", "bytedance"}:
                for v in attrs.values():
                    if v:
                        blob_parts.append(v)
                # Grab the full card text as a fallback signal
                blob_parts.append(card.get_text(" ", strip=True)[:1000])
            blob = " | ".join(blob_parts)

            city = _guess_city(blob) or attrs.get("city") or None
            employment_type = _guess_employment_type(blob)

            sal_min_k, sal_max_k, sal_months = _guess_salary_fields(blob)
            raw_attributes: dict[str, str] = dict(attrs)
            if sal_min_k is not None:
                raw_attributes["salary_min_k"] = str(sal_min_k)
            if sal_max_k is not None:
                raw_attributes["salary_max_k"] = str(sal_max_k)
            if sal_months is not None:
                raw_attributes["salary_months"] = str(sal_months)
            try:
                candidate = AtsJobCandidate(
                    title=title,
                    url=HttpUrl(url),
                    external_id=None,
                    city=city,
                    employment_type=employment_type,
                    raw_attributes=raw_attributes,
                )
            except Exception:  # noqa: BLE001, S112 - skip malformed candidate; parser never raises.
                continue
            candidates.append(candidate)
        fingerprint = hashlib.sha256(html.encode("utf-8")).hexdigest()[:128]
        status = AtsParseStatus.SUCCEEDED if candidates else AtsParseStatus.PARTIAL
        return AtsListResult(
            candidates=tuple(candidates),
            status=status,
            observed_count=len(candidates),
            reported_total=None,
            error_code=None if candidates else "no_candidates",
            content_fingerprint=fingerprint,
        )
    except Exception:  # noqa: BLE001 - parser boundary: never raise, return FAILED.
        return AtsListResult(candidates=(), status=AtsParseStatus.FAILED, error_code="parse_failed")

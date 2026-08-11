"""Contracts for recruiting-entry discovery.

公司名称池 + 入口候选数据结构，纯规则校验，无 LLM 依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from rapidfuzz.fuzz import ratio, partial_ratio
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.core.normalization import normalize_name


# ---------------------------------------------------------------------------
# Legal-name prefix / suffix removal for the "去后缀名" variant
# 顺序：先剥地名前缀，再剥工商/行业后缀，避免"北京xx科技"被剥不干净
# ---------------------------------------------------------------------------

# 国内常见省级/直辖市级地名前缀（含省/市两可）
_CITY_PREFIXES = (
    "北京", "上海市", "上海", "广州市", "广州", "深圳市", "深圳",
    "杭州市", "杭州", "南京市", "南京", "成都市", "成都", "武汉市", "武汉",
    "西安市", "西安", "重庆市", "重庆", "天津市", "天津", "苏州市", "苏州",
    "青岛市", "青岛", "长沙市", "长沙", "郑州市", "郑州", "合肥市", "合肥",
    "福州市", "福州", "厦门市", "厦门", "东莞市", "东莞", "宁波市", "宁波",
    "无锡市", "无锡", "佛山市", "佛山", "沈阳市", "沈阳", "大连市", "大连",
    "济南市", "济南", "石家庄市", "石家庄", "哈尔滨市", "哈尔滨", "昆明市", "昆明",
    "广东省", "广东省深圳市", "浙江省", "江苏省", "四川省", "湖北省", "陕西省",
    "福建省", "山东省", "河南省", "安徽省", "湖南省", "河北省", "辽宁省",
    "黑龙江省", "云南省", "贵州省", "广西", "新疆", "内蒙古", "西藏", "宁夏", "海南",
    "中国",
)

_LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "集团有限公司",
    "有限公司",
    "股份公司",
    "集团公司",
    "公司",
    "责任公司",
    "科技",
    "信息技术",
    "信息科技",
    "网络科技",
    "软件",
    "控股",
    "集团",
)


def strip_legal_suffixes(name: str) -> str:
    """Remove geographic prefix + legal / industrial suffixes. Non-idempotent."""
    value = name.strip()

    # 1) 剥最外层地名前缀（只剥一次，从最长匹配开始）
    prefixes_sorted = sorted(_CITY_PREFIXES, key=len, reverse=True)
    for prefix in prefixes_sorted:
        if value.startswith(prefix) and len(value) > len(prefix):
            value = value[len(prefix):].lstrip("（）()[]【】·-—")
            break

    # 2) 反复剥工商/行业后缀
    changed = True
    while changed:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)].rstrip("（）()[]【】·-—")
                changed = True
                break
    return value.strip()


def extract_root_domain(host: str) -> str:
    """Return the registrable-ish root domain, e.g. www.moonshot.cn -> moonshot.cn."""
    host = host.lower().rstrip(".")
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Skip common country-2 TLD co. / com. patterns
    if len(parts) >= 3 and parts[-2] in {"com", "co", "net", "org", "gov", "edu", "ac"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize_for_compare(value: str) -> str:
    """Case-fold + remove whitespace + punctuation for fuzzy matching."""
    return "".join(ch for ch in normalize_name(value) if ch.isalnum())


# ---------------------------------------------------------------------------
# Name pool
# ---------------------------------------------------------------------------

class CompanyNamePool(BaseModel):
    """Multiform name set used for candidate-entry validation.

    字段说明：
      canonical_name      统一的对外展示名（如 "月之暗面"）
      legal_name          工商登记全称（如 "北京月之暗面科技有限公司"）
      brand_names         品牌/产品名，可能和公司主名不同（如 "Kimi"）
      historical_aliases  历史曾用名 / 简称别名
      domains             已知官网域名列表（带或不带 www 均可）
    """

    model_config = ConfigDict(frozen=True)

    canonical_name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=300)
    brand_names: tuple[str, ...] = Field(default_factory=tuple)
    historical_aliases: tuple[str, ...] = Field(default_factory=tuple)
    domains: tuple[str, ...] = Field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Derived variants
    # ------------------------------------------------------------------

    @property
    def stripped_legal_name(self) -> str | None:
        """"去后缀名"：legal_name 去掉工商/行业后缀。"""
        if not self.legal_name:
            return None
        value = strip_legal_suffixes(self.legal_name)
        return value or None

    def all_name_variants(self) -> tuple[str, ...]:
        """All forms used for textual similarity. 去重 + 去空。"""
        raw: list[str] = [self.canonical_name]
        if self.legal_name:
            raw.append(self.legal_name)
        stripped = self.stripped_legal_name
        if stripped:
            raw.append(stripped)
        raw.extend(self.brand_names)
        raw.extend(self.historical_aliases)
        seen: set[str] = set()
        result: list[str] = []
        for item in raw:
            key = normalize_for_compare(item)
            if key and key not in seen:
                seen.add(key)
                result.append(item)
        return tuple(result)

    def root_domains(self) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for domain in self.domains:
            root = extract_root_domain(domain)
            if root and root not in seen:
                seen.add(root)
                result.append(root)
        return tuple(result)


# ---------------------------------------------------------------------------
# Entry candidates
# ---------------------------------------------------------------------------

class EntryPlatform:
    ATS_FEISHU = "ats_feishu"
    ATS_MOKA = "ats_moka"
    COMPANY_SITE_CAREERS = "company_site_careers"
    BOSS_ZHIPIN = "boss_zhipin"
    LIEPIN = "liepin"
    LAGOU = "lagou"
    ZHIHU = "zhihu_global_search"
    OTHER = "other"


@dataclass(frozen=True)
class EntryCandidate:
    """A single discovered recruiting entry URL with validation signals."""

    url: str
    platform: str = EntryPlatform.OTHER
    title: str | None = None
    snippet: str | None = None
    source_provider: str | None = None  # e.g. "serper", "zhihu_global_search"
    source_url: str | None = None
    # Validation signals (populated by validator)
    name_mention: bool = False
    best_name_similarity: float = 0.0
    best_name_variant: str | None = None
    domain_match: bool = False
    root_domain_match: bool = False
    overall_confidence: float = 0.0  # 0.0 ~ 1.0

    def is_high_confidence(self, threshold: float = 0.6) -> bool:
        return self.overall_confidence >= threshold


# ---------------------------------------------------------------------------
# Site-restricted discovery query builders
# ---------------------------------------------------------------------------

_ATS_SITES = (
    (EntryPlatform.ATS_FEISHU, "jobs.feishu.cn"),
    (EntryPlatform.ATS_MOKA, "app.mokahr.com"),
)

_JOB_BOARD_SITES = (
    (EntryPlatform.BOSS_ZHIPIN, "zhipin.com"),
    (EntryPlatform.LIEPIN, "liepin.com"),
    (EntryPlatform.LAGOU, "lagou.com"),
)


def build_site_queries(
    name_pool: CompanyNamePool, *, include_job_boards: bool = True
) -> tuple[tuple[str, str], ...]:
    """返回 (平台, 查询字符串) 列表。

    对每一种入口站点 + 每一个名称变体（canonical + 品牌名 + 去后缀工商名）
    组合生成 site: 精确查询。
    """
    queries: list[tuple[str, str]] = []
    variants = list(name_pool.all_name_variants())
    # Prefer the canonical name first
    variants.insert(0, name_pool.canonical_name)
    seen: set[tuple[str, str]] = set()

    def add(platform: str, site: str) -> None:
        for name in variants:
            q = f'"{name}" site:{site}'
            key = (platform, q)
            if key in seen:
                continue
            seen.add(key)
            queries.append((platform, q))

    for platform, site in _ATS_SITES:
        add(platform, site)
    if include_job_boards:
        for platform, site in _JOB_BOARD_SITES:
            add(platform, site)
    return tuple(queries)


def build_careers_probe_paths(domain: str) -> tuple[str, ...]:
    """Given a bare domain, guess common careers-page URLs (HTTPS only)."""
    if not domain:
        return ()
    domain = domain.lower().rstrip("/")
    paths = (
        "",
        "/careers",
        "/join-us",
        "/jobs",
        "/job",
        "/recruitment",
        "/about/careers",
        "/about/jobs",
        "/hr",
        "/zhaopin",
        "/zhaopin.html",
        "/join",
        "/work-with-us",
    )
    results: list[str] = []
    for path in paths:
        if path:
            results.append(f"https://{domain}{path}")
        results.append(f"https://www.{domain}{path or '/'}")
    return tuple(results)


# ---------------------------------------------------------------------------
# Validation (pure rules, no LLM)
# ---------------------------------------------------------------------------

_NAME_EXACT_THRESHOLD = 100.0
_NAME_HIGH_THRESHOLD = 92.0
_NAME_LOW_THRESHOLD = 78.0


def _check_domain(url: str, root_domains: tuple[str, ...]) -> tuple[bool, bool]:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False, False
    if not host:
        return False, False
    exact = host in root_domains or any(
        host == d or host.endswith("." + d) for d in root_domains
    )
    root = extract_root_domain(host)
    root_match = bool(root and root in root_domains)
    return exact, root_match


def validate_entry_candidate(
    candidate: EntryCandidate, name_pool: CompanyNamePool
) -> EntryCandidate:
    """Populate validation signals on a candidate (pure rules)."""
    variants = name_pool.all_name_variants()
    compare_variants = [normalize_for_compare(v) for v in variants]

    text_blobs: list[str] = []
    if candidate.title:
        text_blobs.append(candidate.title)
    if candidate.snippet:
        text_blobs.append(candidate.snippet)
    text_blobs.append(candidate.url)
    combined_text = " ".join(text_blobs)
    text_normalized = normalize_for_compare(combined_text)

    name_mention = any(v and v in text_normalized for v in compare_variants)

    best_sim = 0.0
    best_variant: str | None = None
    for original, compared in zip(variants, compare_variants):
        if not compared:
            continue
        # Check the title first as it's the most authoritative field
        title_compared = normalize_for_compare(candidate.title or "")
        if title_compared:
            sim = max(ratio(title_compared, compared), partial_ratio(title_compared, compared))
        else:
            sim = 0.0
        if candidate.snippet:
            snippet_compared = normalize_for_compare(candidate.snippet)
            sim = max(sim, partial_ratio(snippet_compared, compared))
        if sim > best_sim:
            best_sim = sim
            best_variant = original

    root_domains = name_pool.root_domains()
    exact_domain, root_match_domain = _check_domain(candidate.url, root_domains)

    # Confidence scoring (heuristic weights)
    score = 0.0
    if exact_domain:
        score += 0.45
    elif root_match_domain:
        score += 0.25
    if name_mention:
        score += 0.25
    if best_sim >= _NAME_EXACT_THRESHOLD:
        score += 0.30
    elif best_sim >= _NAME_HIGH_THRESHOLD:
        score += 0.22
    elif best_sim >= _NAME_LOW_THRESHOLD:
        score += 0.10
    # Bonus for known ATS platforms even without perfect match
    if candidate.platform in {EntryPlatform.ATS_FEISHU, EntryPlatform.ATS_MOKA}:
        score += 0.05
    confidence = min(1.0, score)
    title_normalized = normalize_for_compare(candidate.title or "")
    title_has_company_evidence = any(
        value and value in title_normalized for value in compare_variants
    ) or best_sim >= _NAME_HIGH_THRESHOLD
    if (
        candidate.platform in {EntryPlatform.ATS_FEISHU, EntryPlatform.ATS_MOKA}
        and not title_has_company_evidence
        and not root_match_domain
    ):
        confidence = min(confidence, 0.59)

    return EntryCandidate(
        url=candidate.url,
        platform=candidate.platform,
        title=candidate.title,
        snippet=candidate.snippet,
        source_provider=candidate.source_provider,
        source_url=candidate.source_url,
        name_mention=name_mention,
        best_name_similarity=best_sim,
        best_name_variant=best_variant,
        domain_match=exact_domain,
        root_domain_match=root_match_domain,
        overall_confidence=round(confidence, 3),
    )

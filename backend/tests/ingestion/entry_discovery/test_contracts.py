"""Unit tests for CompanyNamePool, site-query builder, and pure-rule validator."""

from __future__ import annotations

import pytest

from app.ingestion.entry_discovery.contracts import (
    CompanyNamePool,
    EntryCandidate,
    EntryPlatform,
    build_careers_probe_paths,
    build_site_queries,
    extract_root_domain,
    strip_legal_suffixes,
    validate_entry_candidate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def moonshot_pool() -> CompanyNamePool:
    return CompanyNamePool(
        canonical_name="月之暗面",
        legal_name="北京月之暗面科技有限公司",
        brand_names=("Kimi",),
        historical_aliases=("北京月之暗面",),
        domains=("moonshot.cn", "kimi.moonshot.cn"),
    )


# ---------------------------------------------------------------------------
# Name pool helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("北京月之暗面科技有限公司", "月之暗面"),
        ("上海商汤智能科技有限公司", "商汤智能"),
        ("小米科技有限责任公司", "小米"),
        ("字节跳动有限公司", "字节跳动"),
        ("阿里巴巴集团控股有限公司", "阿里巴巴"),
    ],
)
def test_strip_legal_suffixes(raw: str, expected: str) -> None:
    assert strip_legal_suffixes(raw) == expected


@pytest.mark.parametrize(
    ("host", "root"),
    [
        ("www.moonshot.cn", "moonshot.cn"),
        ("kimi.moonshot.cn", "moonshot.cn"),
        ("jobs.feishu.cn", "feishu.cn"),
        ("app.mokahr.com", "mokahr.com"),
        ("moonshot.cn", "moonshot.cn"),
        ("x.y.z.com.cn", "z.com.cn"),
    ],
)
def test_extract_root_domain(host: str, root: str) -> None:
    assert extract_root_domain(host) == root


# ---------------------------------------------------------------------------
# Name pool
# ---------------------------------------------------------------------------

def test_name_pool_stripped_legal(moonshot_pool: CompanyNamePool) -> None:
    assert moonshot_pool.stripped_legal_name == "月之暗面"


def test_name_pool_all_variants(moonshot_pool: CompanyNamePool) -> None:
    variants = moonshot_pool.all_name_variants()
    # 至少包含 canonical、legal、stripped、brand、historical 去重后
    assert "月之暗面" in variants
    assert "Kimi" in variants
    # 去重：canonical == stripped，不会出现两次
    assert variants.count("月之暗面") == 1


def test_name_pool_root_domains(moonshot_pool: CompanyNamePool) -> None:
    assert moonshot_pool.root_domains() == ("moonshot.cn",)


def test_known_entry_url_field_defaults_to_empty() -> None:
    pool = CompanyNamePool(canonical_name="Acme")
    assert pool.known_entry_urls == ()


# ---------------------------------------------------------------------------
# Site queries
# ---------------------------------------------------------------------------

def test_build_site_queries_contains_ats_sites(moonshot_pool: CompanyNamePool) -> None:
    queries = list(build_site_queries(moonshot_pool))
    platforms = {k for k, _ in queries}
    assert EntryPlatform.ATS_FEISHU in platforms
    assert EntryPlatform.ATS_MOKA in platforms
    feishu_queries = [q for p, q in queries if p == EntryPlatform.ATS_FEISHU]
    assert any("site:jobs.feishu.cn" in q for q in feishu_queries)
    assert any('"月之暗面"' in q for q in feishu_queries)


def test_build_careers_probe_paths() -> None:
    urls = build_careers_probe_paths("moonshot.cn")
    assert any(u == "https://moonshot.cn/careers" for u in urls)
    assert any(u == "https://www.moonshot.cn/join-us" for u in urls)
    assert all(u.startswith("https://") for u in urls)


# ---------------------------------------------------------------------------
# Candidate validation
# ---------------------------------------------------------------------------

def test_validate_high_confidence_ats(moonshot_pool: CompanyNamePool) -> None:
    cand = validate_entry_candidate(
        EntryCandidate(
            url="https://jobs.feishu.cn/careers/xxx/moonshot",
            platform=EntryPlatform.ATS_FEISHU,
            title="月之暗面 - 加入我们",
            snippet="北京月之暗面科技有限公司招聘页",
            source_provider="serper",
        ),
        moonshot_pool,
    )
    assert cand.name_mention is True
    assert cand.best_name_similarity >= 92
    assert cand.is_high_confidence(threshold=0.6)


def test_validate_exact_domain_match(moonshot_pool: CompanyNamePool) -> None:
    cand = validate_entry_candidate(
        EntryCandidate(
            url="https://kimi.moonshot.cn/careers",
            platform=EntryPlatform.COMPANY_SITE_CAREERS,
            title="Kimi Careers",
        ),
        moonshot_pool,
    )
    assert cand.domain_match is True or cand.root_domain_match is True
    assert cand.overall_confidence >= 0.7


def test_validate_false_positive_rejected(moonshot_pool: CompanyNamePool) -> None:
    cand = validate_entry_candidate(
        EntryCandidate(
            url="https://jobs.feishu.cn/careers/xxx/aliyun",
            platform=EntryPlatform.ATS_FEISHU,
            title="阿里云招聘",
            snippet="阿里巴巴集团的云服务部门",
            source_provider="serper",
        ),
        moonshot_pool,
    )
    assert cand.is_high_confidence(threshold=0.6) is False
    assert cand.best_name_similarity < 78


def test_validate_brand_name_kimi(moonshot_pool: CompanyNamePool) -> None:
    cand = validate_entry_candidate(
        EntryCandidate(
            url="https://app.mokahr.com/apply/kimi/xxx",
            platform=EntryPlatform.ATS_MOKA,
            title="Kimi 招聘",
            snippet="月之暗面旗下 Kimi 产品团队招聘",
            source_provider="serper",
        ),
        moonshot_pool,
    )
    # 品牌名 Kimi 应被命中
    assert cand.best_name_variant in {"Kimi", "月之暗面"}
    assert cand.overall_confidence >= 0.55

"""Stable internet-company profile fields and their evidence requirements."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileFieldDefinition:
    key: str
    label: str
    category: str
    minimum_sources_for_verified: int


PROFILE_FIELD_CATALOG = (
    ProfileFieldDefinition("financing.latest_round", "最新融资轮次", "financing", 2),
    ProfileFieldDefinition("financing.latest_date", "最近融资日期", "financing", 2),
    ProfileFieldDefinition("financing.investors", "已披露投资方", "financing", 2),
    ProfileFieldDefinition("financing.total_rounds", "融资轮次总数", "financing", 2),
    ProfileFieldDefinition("customers.case_studies", "公开客户案例", "customers", 1),
    ProfileFieldDefinition("business.revenue_model", "商业模式", "business", 1),
    ProfileFieldDefinition("product.maturity", "产品成熟度", "product", 1),
    ProfileFieldDefinition("technology.github_org", "官方 GitHub 组织", "technology", 1),
    ProfileFieldDefinition("technology.github.stars_total", "GitHub Star 总数", "technology", 1),
    ProfileFieldDefinition("technology.github.active_repositories", "活跃开源仓库数", "technology", 1),
    ProfileFieldDefinition("technology.patents", "公开专利信号", "technology", 1),
    ProfileFieldDefinition("growth.active_jobs", "当前在招职位数", "growth", 1),
    ProfileFieldDefinition("growth.news_count_90d", "近 90 天新闻数量", "growth", 1),
    ProfileFieldDefinition("organization.employee_scale", "员工规模", "organization", 1),
    ProfileFieldDefinition("organization.rnd_headcount", "研发团队规模", "organization", 1),
)

PROFILE_FIELDS_BY_KEY = {field.key: field for field in PROFILE_FIELD_CATALOG}


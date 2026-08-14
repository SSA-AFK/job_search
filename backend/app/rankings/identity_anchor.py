"""Verify brand-to-legal-company anchors before Tianyancha enrichment."""

import asyncio
import json
import shutil
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company

LEGAL_NAME_CANDIDATES = {
    "DeepSeek（深度求索）": "杭州深度求索人工智能基础技术研究有限公司",
    "MiniMax": "上海稀宇极智科技有限公司",
    "京东": "北京京东世纪贸易有限公司",
    "华为": "华为技术有限公司",
    "哔哩哔哩": "上海宽娱数码科技有限公司",
    "大疆": "深圳市大疆创新科技有限公司",
    "小米": "小米科技有限责任公司",
    "小红书": "行吟信息科技（上海）有限公司",
    "得物": "上海得物信息集团有限公司",
    "快手": "北京快手科技有限公司",
    "携程": "上海携程商务有限公司",
    "智谱AI": "北京智谱华章科技股份有限公司",
    "月之暗面": "北京月之暗面科技股份有限公司",
    "百度": "北京百度网讯科技有限公司",
    "米哈游": "上海米哈游网络科技股份有限公司",
    "网易": "网易（杭州）网络有限公司",
    "美团": "北京三快在线科技有限公司",
    "腾讯": "深圳市腾讯计算机系统有限公司",
    "蚂蚁集团": "蚂蚁科技集团股份有限公司",
    "钉钉": "钉钉（中国）信息技术有限公司",
}

_ACTIVE_STATUSES = {"存续", "在业", "开业", "正常"}
BRAND_SEARCH_QUERIES = {"DeepSeek（深度求索）": "DeepSeek"}
COMPANY_LOOKUP_NAMES = {
    "DeepSeek（深度求索）": ("DeepSeek（深度求索）", "杭州深度求索人工智能基础技术研究有限公司"),
}


class IdentityAnchorError(RuntimeError):
    pass


def legal_search_key(company: Company) -> str:
    if company.identity_anchor_status != "verified" or not company.legal_name:
        raise IdentityAnchorError("company identity is not verified")
    return company.legal_name


def approved_company(session: Session, brand: str) -> Company | None:
    lookup_names = COMPANY_LOOKUP_NAMES.get(brand, (brand,))
    return session.scalar(select(Company).where(Company.canonical_name.in_(lookup_names)))


async def search_brand(brand: str, *, page_size: int = 5) -> dict[str, Any]:
    return await _run_tyc(
        "company", "companies", brand,
        "--pageNum", "1", "--pageSize", str(page_size), "--compact",
    )


async def registration_info(legal_name: str) -> dict[str, Any]:
    return await _run_tyc("company", "registration-info", legal_name, "--compact")


async def _run_tyc(*args: str) -> dict[str, Any]:
    executable = _tyc_executable()
    process = await asyncio.create_subprocess_exec(
        executable,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    if process.returncode != 0:
        raise IdentityAnchorError("Tianyancha CLI lookup failed")
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise IdentityAnchorError("invalid Tianyancha response")
    return payload


def _tyc_executable() -> str:
    discovered = shutil.which("tyc") or shutil.which("tyc.cmd")
    if discovered:
        return discovered
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm:
        result = subprocess.run(
            [npm, "prefix", "-g"], capture_output=True, text=True, timeout=10, check=False
        )
        candidate = Path(result.stdout.strip()) / ("tyc.cmd" if result.stdout else "tyc")
        if candidate.exists():
            return str(candidate)
    return "tyc"


def verify_search_candidate(payload: dict[str, Any], expected_legal_name: str) -> dict[str, Any]:
    items = payload.get("items")
    matches = [
        item for item in items if isinstance(item, dict) and item.get("name") == expected_legal_name
    ] if isinstance(items, list) else []
    if len(matches) != 1:
        raise IdentityAnchorError("expected legal company was not uniquely matched")
    match = matches[0]
    if match.get("regStatus") not in _ACTIVE_STATUSES:
        raise IdentityAnchorError("legal company is not active")
    if not str(match.get("id", "")).strip() or not str(match.get("creditCode", "")).strip():
        raise IdentityAnchorError("company identity fields are incomplete")
    return match


def verify_registration_name(payload: dict[str, Any], expected_legal_name: str) -> dict[str, Any]:
    sources = payload.get("sources")
    base = sources.get("base") if isinstance(sources, dict) else None
    if not isinstance(base, dict) or base.get("empty") is True:
        raise IdentityAnchorError("registration response is empty")
    if base.get("name") != expected_legal_name:
        raise IdentityAnchorError("registration name does not match verified legal company")
    return base


async def anchor_company(session: Session, company: Company, expected_legal_name: str) -> None:
    try:
        search_query = BRAND_SEARCH_QUERIES.get(company.canonical_name, company.canonical_name)
        candidate = verify_search_candidate(await search_brand(search_query), expected_legal_name)
        verify_registration_name(await registration_info(expected_legal_name), expected_legal_name)
    except Exception:
        company.identity_anchor_status = "review_required"
        company.identity_anchored_at = datetime.now(UTC)
        session.commit()
        raise
    company.legal_name = expected_legal_name
    company.tianyancha_company_id = str(candidate["id"])
    company.uscc_sha256 = sha256(str(candidate["creditCode"]).encode()).hexdigest()
    company.identity_anchor_status = "verified"
    company.identity_anchored_at = datetime.now(UTC)
    session.commit()


async def anchor_approved_companies(session: Session) -> dict[str, list[str]]:
    succeeded: list[str] = []
    failed: list[str] = []
    for brand, legal_name in LEGAL_NAME_CANDIDATES.items():
        company = approved_company(session, brand)
        if company is None:
            failed.append(brand)
            continue
        if (
            company.identity_anchor_status == "verified"
            and company.legal_name == legal_name
            and company.tianyancha_company_id
            and company.uscc_sha256
        ):
            succeeded.append(brand)
            continue
        try:
            await anchor_company(session, company, legal_name)
            succeeded.append(brand)
        except Exception:
            session.rollback()
            failed.append(brand)
    return {"succeeded": succeeded, "failed": failed}

"""Collect minimized public baseline fields for the approved JobHunt companies."""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Company, CompanyRankingSignal, RankingPilot
from app.models.enums import CompanyScale
from app.rankings.identity_anchor import (
    LEGAL_NAME_CANDIDATES,
    approved_company,
    anchor_approved_companies,
    legal_search_key,
    registration_info,
    verify_registration_name,
)
from app.rankings.relevance import assess_ai_business_scope
from app.rankings.service import rescore_ai_pilot

def _optional(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() not in {"-", ""} else None


async def collect(session: Session, pilot_id: object) -> dict[str, object]:
    succeeded: list[str] = []
    failed: list[str] = []
    anchor_result = await anchor_approved_companies(session)
    failed.extend(anchor_result["failed"])
    for brand in LEGAL_NAME_CANDIDATES:
        if brand in failed:
            continue
        company = approved_company(session, brand)
        if company is None:
            failed.append(brand)
            continue
        try:
            search_key = legal_search_key(company)
            payload = await registration_info(search_key)
            base = verify_registration_name(payload, search_key)
            scope = _optional(base.get("businessScope"))
            industry = base.get("industryAll") if isinstance(base.get("industryAll"), dict) else {}
            company.industry = company.industry or "人工智能"
            company.sub_industry = _optional(base.get("industry")) or company.sub_industry
            company.city = _optional(base.get("city")) or company.city
            company.province = company.province
            company.district = _optional(base.get("district"))
            company.company_type = _optional(base.get("companyOrgType"))
            company.registered_capital = _optional(base.get("regCapital"))
            company.paid_in_capital = _optional(base.get("actualCapital"))
            company.industry_sector = _optional(industry.get("category"))
            company.industry_middle = _optional(industry.get("categoryMiddle"))
            company.insured_employee_count = base.get("socialStaffNum") if isinstance(base.get("socialStaffNum"), int) else None
            company.business_scope = scope
            established = _optional(base.get("estiblishTime"))
            if established:
                try:
                    company.established_at = date.fromisoformat(established[:10])
                    company.founded_year = company.established_at.year
                except ValueError:
                    pass
            size = _optional(base.get("staffNumRange"))
            if size:
                company.scale = CompanyScale.ONE_TO_49 if "小于50" in size else company.scale
            assessment = assess_ai_business_scope(scope)
            existing = session.scalar(select(CompanyRankingSignal).where(CompanyRankingSignal.company_id == company.id, CompanyRankingSignal.category == "ai_relevance", CompanyRankingSignal.signal_key == "ai_business_scope"))
            if assessment.is_ai_related:
                fingerprint = sha256(f"{search_key}:approved-ai-scope-v1".encode()).hexdigest()
                value = {"classification": "ai_related_business_scope", "matched_term_count": assessment.matched_term_count}
                if existing is None:
                    session.add(CompanyRankingSignal(company_id=company.id, source_document_id=None, category="ai_relevance", signal_key="ai_business_scope", value=value, event_date=None, source_fingerprint=fingerprint, response_sha256=None, confidence=Decimal("0.900"), verification_status="internal_verified", fetched_at=datetime.now(UTC), expires_at=None))
                else:
                    existing.value = value; existing.source_fingerprint = fingerprint
            elif existing is not None:
                session.delete(existing)
            session.commit()
            succeeded.append(brand)
        except Exception:
            session.rollback()
            failed.append(brand)
    session.rollback()
    rescore_ai_pilot(session, pilot_id)
    return {"succeeded": succeeded, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    with Session(engine) as session:
        pilot_id = session.scalar(select(RankingPilot.id).where(RankingPilot.industry == "ai").order_by(RankingPilot.created_at.desc()))
        if pilot_id is None:
            parser.error("AI pilot not found")
        session.rollback()
        print(json.dumps(asyncio.run(collect(session, pilot_id)), ensure_ascii=False))


if __name__ == "__main__":
    main()

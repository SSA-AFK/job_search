"""Collect Tianyancha ranking signals for the fixed approved JobHunt companies."""

import argparse
import asyncio
import json
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import RankingCollectionRun, RankingPilot
from app.rankings.gap_plan import EnrichmentCategory
from app.rankings.identity_anchor import LEGAL_NAME_CANDIDATES, approved_company, legal_search_key
from app.rankings.service import rescore_ai_pilot
from app.rankings.tianyancha.client import TianyanchaRankingClient
from app.rankings.tianyancha.service import _persist_signals, _three_year_window
from app.rankings.tianyancha.projectors import project_response

RULE = "tyc-approved-jobhunt-v1"


async def collect(session: Session, pilot_id: object) -> dict[str, int]:
    client = TianyanchaRankingClient()
    today = datetime.now(UTC).date()
    window_start = _three_year_window(today)
    succeeded = failed = skipped = 0
    companies = tuple(
        company
        for brand in LEGAL_NAME_CANDIDATES
        if (company := approved_company(session, brand)) is not None
    )
    for company in companies:
        for category in EnrichmentCategory:
            run_key = sha256(f"{today}:{category.value}:{RULE}".encode()).hexdigest()
            run = session.scalar(select(RankingCollectionRun).where(RankingCollectionRun.pilot_id == pilot_id, RankingCollectionRun.company_id == company.id, RankingCollectionRun.category == category.value, RankingCollectionRun.run_key == run_key))
            if run is not None and run.status == "succeeded":
                skipped += 1
                continue
            if run is None:
                run = RankingCollectionRun(pilot_id=pilot_id, company_id=company.id, category=category.value, run_key=run_key, status="running", logical_call_count=1, tool_call_count=client.tool_call_count(category), started_at=datetime.now(UTC))
                session.add(run)
            else:
                run.status = "running"; run.error_code = None; run.started_at = datetime.now(UTC)
            session.commit()
            try:
                search_key = legal_search_key(company)
                payload = await client.fetch(category, search_key, window_start=window_start, window_end=today)
                response_hash = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                signals = project_response(category, payload, company_name=search_key, company_aliases=(company.canonical_name,), window_start=window_start)
                _persist_signals(session, company.id, category, signals, response_hash=response_hash, fetched_at=datetime.now(UTC))
                run.status = "succeeded"; run.response_sha256 = response_hash; succeeded += 1
            except Exception as error:
                session.rollback()
                run = session.get(RankingCollectionRun, run.id)
                assert run is not None
                run.status = "failed"; run.error_code = getattr(error, "code", "collection_failed"); failed += 1
            run.finished_at = datetime.now(UTC)
            session.commit()
    rescore_ai_pilot(session, pilot_id)
    return {"companies": len(companies), "succeeded": succeeded, "failed": failed, "skipped": skipped}


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

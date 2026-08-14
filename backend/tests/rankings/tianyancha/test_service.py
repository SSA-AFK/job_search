import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, Company, CompanyProfileField, CompanyRankingSignal, RankingCollectionRun
from app.rankings.service import import_ai_pilot, rescore_ai_pilot
from app.rankings.tianyancha.service import collect_pilot_tianyancha
from tests.rankings.test_selection import _workbook


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, category: Any, search_key: str, **_: Any) -> dict[str, object]:
        self.calls.append(category.value)
        return {"items": [], "toolRisks": []}

    def tool_call_count(self, category: Any) -> int:
        return 2 if category.value in {"intellectual_property", "market_validation"} else 1


def test_collection_is_bounded_to_four_categories_and_resumes_without_calls(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    workbook = _workbook(tmp_path / "companies.xlsx", count=1)
    client = FakeClient()
    with Session(engine) as session:
        summary = import_ai_pilot(session, workbook, sample_size=1)
        company = session.scalar(select(Company))
        assert company is not None
        company.legal_name = company.canonical_name
        company.identity_anchor_status = "verified"
        session.commit()
        first = asyncio.run(
            collect_pilot_tianyancha(
                session,
                summary.pilot_id,
                str(workbook),
                client=client,  # type: ignore[arg-type]
                as_of=date(2026, 8, 12),
            )
        )
        second = asyncio.run(
            collect_pilot_tianyancha(
                session,
                summary.pilot_id,
                str(workbook),
                client=client,  # type: ignore[arg-type]
                as_of=date(2026, 8, 12),
            )
        )
        rescore_ai_pilot(session, summary.pilot_id)

        assert first.logical_calls == 4
        assert first.tool_calls == 6
        assert second.logical_calls == 0
        assert len(client.calls) == 4
        assert session.scalar(select(func.count()).select_from(RankingCollectionRun)) == 4
        assert session.scalar(select(func.count()).select_from(CompanyRankingSignal)) == 1
        assert session.scalar(select(func.count()).select_from(CompanyProfileField)) == 0

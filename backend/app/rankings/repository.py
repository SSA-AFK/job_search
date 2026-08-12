from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import Company, CompanyRankingSnapshot, RankingPilot, RankingPilotMember
from app.rankings.service import AI_INDUSTRY, RULE_VERSION


@dataclass(frozen=True)
class PublishedRankingRow:
    company: Company
    snapshot: CompanyRankingSnapshot
    rank: int | None


class RankingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def current_pilot_id(self) -> UUID | None:
        return self.session.scalar(
            select(RankingPilot.id)
            .join(RankingPilotMember, RankingPilotMember.pilot_id == RankingPilot.id)
            .join(
                CompanyRankingSnapshot,
                (CompanyRankingSnapshot.pilot_id == RankingPilot.id)
                & (CompanyRankingSnapshot.company_id == RankingPilotMember.company_id),
            )
            .where(
                RankingPilot.industry == AI_INDUSTRY,
                CompanyRankingSnapshot.rule_version == RULE_VERSION,
            )
            .group_by(RankingPilot.id, RankingPilot.created_at)
            .order_by(RankingPilot.created_at.desc())
            .limit(1)
        )

    def member_statement(self, pilot_id: UUID) -> Select[tuple[Company, CompanyRankingSnapshot]]:
        return (
            select(Company, CompanyRankingSnapshot)
            .join(RankingPilotMember, RankingPilotMember.company_id == Company.id)
            .join(
                CompanyRankingSnapshot,
                (CompanyRankingSnapshot.company_id == Company.id)
                & (CompanyRankingSnapshot.pilot_id == RankingPilotMember.pilot_id),
            )
            .where(
                RankingPilotMember.pilot_id == pilot_id,
                CompanyRankingSnapshot.rule_version == RULE_VERSION,
            )
        )

    def rows(self) -> tuple[PublishedRankingRow, ...]:
        pilot_id = self.current_pilot_id()
        if pilot_id is None:
            return ()
        rows = tuple(self.session.execute(self.member_statement(pilot_id)))
        component_order = (
            "ai_core",
            "market_validation",
            "growth_momentum",
            "industry_influence",
            "reliability",
        )
        ordered = sorted(
            rows,
            key=lambda row: (
                not row[1].is_eligible,
                -int(row[1].total_score),
                *(-int(row[1].component_scores.get(key, 0)) for key in component_order),
                row[0].canonical_name,
            ),
        )
        rank = 0
        result = []
        for company, snapshot in ordered:
            if snapshot.is_eligible:
                rank += 1
                assigned_rank: int | None = rank
            else:
                assigned_rank = None
            result.append(PublishedRankingRow(company, snapshot, assigned_rank))
        return tuple(result)

    def calculated_at(self, pilot_id: UUID) -> datetime | None:
        return self.session.scalar(
            select(func.max(CompanyRankingSnapshot.calculated_at)).where(
                CompanyRankingSnapshot.pilot_id == pilot_id,
                CompanyRankingSnapshot.rule_version == RULE_VERSION,
            )
        )

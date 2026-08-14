from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from collections import defaultdict

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import Company, CompanyRankingSnapshot, JobPosting, JobSource, RankingPilot, RankingPilotMember
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

    def early_career_counts(self, company_ids: list[UUID]) -> dict[UUID, tuple[int, int]]:
        if not company_ids:
            return {}
        title = func.lower(JobPosting.title)
        predicate = or_(
            JobPosting.job_type.in_(["campus", "internship"]),
            *(title.like(f"%{keyword}%") for keyword in ("实习", "校招", "校园", "应届", "intern", "graduate")),
        )
        rows = self.session.execute(
            select(JobPosting.company_id, JobPosting.job_type, JobPosting.title)
            .where(JobPosting.company_id.in_(company_ids), JobPosting.is_active.is_(True), predicate)
            .where(exists(select(1).where(JobSource.job_posting_id == JobPosting.id, JobSource.provider.like("jobhunt:%"), JobSource.is_active.is_(True))))
        )
        counts: dict[UUID, list[int]] = defaultdict(lambda: [0, 0])
        for company_id, job_type, job_title in rows:
            lowered = job_title.lower()
            value = getattr(job_type, "value", str(job_type))
            index = 1 if "实习" in lowered or "intern" in lowered or value == "internship" else 0
            counts[company_id][index] += 1
        return {key: (value[0], value[1]) for key, value in counts.items()}

    def active_job_counts(self, company_ids: list[UUID]) -> dict[UUID, int]:
        if not company_ids:
            return {}
        rows = self.session.execute(
            select(JobPosting.company_id, func.count(JobPosting.id))
            .where(JobPosting.company_id.in_(company_ids), JobPosting.is_active.is_(True))
            .where(exists(select(1).where(
                JobSource.job_posting_id == JobPosting.id,
                JobSource.provider.like("jobhunt:%"),
                JobSource.is_active.is_(True),
            )))
            .group_by(JobPosting.company_id)
        )
        return {company_id: count for company_id, count in rows}

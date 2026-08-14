from fastapi import status

from app.core.errors import DomainError
from app.rankings.repository import PublishedRankingRow, RankingRepository
from app.rankings.schemas import (
    RankingComponents,
    RankingListResponse,
    RankingMemberItem,
    RankingQuery,
)


class RankingNotPublishedError(DomainError):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="ranking_not_published",
            message="AI ranking is not published",
        )


def ranking_reason(row: PublishedRankingRow) -> str:
    if not row.snapshot.is_eligible:
        return "AI 相关性证据不足，暂列观察。"
    scores = row.snapshot.component_scores
    labels = {
        "ai_core": "AI 核心性",
        "market_validation": "市场验证",
        "growth_momentum": "成长动能",
        "industry_influence": "行业影响力",
        "reliability": "可靠性",
    }
    strongest = max(labels, key=lambda key: int(scores.get(key, 0)))
    return f"{labels[strongest]}在同阶段公司中表现突出。"


class PublicRankingService:
    def __init__(self, repository: RankingRepository) -> None:
        self.repository = repository

    def list_ai(self, query: RankingQuery) -> RankingListResponse:
        pilot_id = self.repository.current_pilot_id()
        if pilot_id is None:
            raise RankingNotPublishedError
        all_rows = self.repository.rows()
        ranked_total = sum(row.snapshot.is_eligible for row in all_rows)
        observation_total = len(all_rows) - ranked_total
        selected = [
            row
            for row in all_rows
            if row.snapshot.is_eligible == (query.status == "ranked")
            and (query.stage is None or row.snapshot.company_stage == query.stage)
        ]
        start = (query.page - 1) * query.page_size
        page_rows = selected[start : start + query.page_size]
        counts = self.repository.early_career_counts([row.company.id for row in page_rows])
        active_counts = self.repository.active_job_counts([row.company.id for row in page_rows])
        items = [self._item(row, counts.get(row.company.id, (0, 0)), active_counts.get(row.company.id, 0)) for row in page_rows]
        calculated_at = self.repository.calculated_at(pilot_id)
        assert calculated_at is not None
        return RankingListResponse(
            rule_version=all_rows[0].snapshot.rule_version,
            calculated_at=calculated_at,
            ranked_total=ranked_total,
            observation_total=observation_total,
            page=query.page,
            page_size=query.page_size,
            total=len(selected),
            items=items,
        )

    @staticmethod
    def _item(row: PublishedRankingRow, counts: tuple[int, int] = (0, 0), active_count: int = 0) -> RankingMemberItem:
        scores = RankingComponents.model_validate(row.snapshot.component_scores)
        return RankingMemberItem(
            company_id=row.company.id,
            company_name=row.company.canonical_name,
            rank=row.rank,
            status="ranked" if row.snapshot.is_eligible else "observation",
            total_score=int(row.snapshot.total_score),
            company_stage=row.snapshot.company_stage or "growth",
            component_scores=scores,
            reason=ranking_reason(row),
            missing_fields=row.snapshot.missing_fields,
            campus_job_count=counts[0],
            internship_job_count=counts[1],
            active_job_count=active_count,
        )

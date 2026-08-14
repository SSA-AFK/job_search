from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RankingComponents(BaseModel):
    ai_core: int = 0
    market_validation: int = 0
    growth_momentum: int = 0
    industry_influence: int = 0
    reliability: int = 0


class RankingMemberItem(BaseModel):
    company_id: UUID
    company_name: str
    rank: int | None
    status: Literal["ranked", "observation"]
    total_score: int
    company_stage: str
    component_scores: RankingComponents
    reason: str
    missing_fields: list[str]
    active_job_count: int = 0
    campus_job_count: int = 0
    internship_job_count: int = 0


class RankingListResponse(BaseModel):
    industry: Literal["ai"] = "ai"
    rule_version: str
    calculated_at: datetime
    ranked_total: int
    observation_total: int
    page: int
    page_size: int
    total: int
    items: list[RankingMemberItem]


class RankingQuery(BaseModel):
    status: Literal["ranked", "observation"] = "ranked"
    stage: Literal["early", "growth", "mature"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

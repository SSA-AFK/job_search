from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.rankings.public_service import PublicRankingService
from app.rankings.repository import RankingRepository
from app.rankings.schemas import RankingListResponse, RankingQuery

router = APIRouter(prefix="/rankings", tags=["rankings"])


@router.get("/ai", response_model=RankingListResponse)
def list_ai_ranking(
    session: Annotated[Session, Depends(get_session)],
    status: Annotated[Literal["ranked", "observation"], Query()] = "ranked",
    stage: Annotated[Literal["early", "growth", "mature"] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RankingListResponse:
    return PublicRankingService(RankingRepository(session)).list_ai(
        RankingQuery(status=status, stage=stage, page=page, page_size=page_size)
    )

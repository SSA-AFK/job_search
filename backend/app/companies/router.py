from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.cache.redis import configured_company_cache
from app.companies.repository import CompanyRepository
from app.companies.schemas import (
    CompanyDetail,
    CompanyListItem,
    CompanyQuery,
    JobListItem,
    JobQuery,
    Page,
)
from app.companies.service import CompanyService
from app.core.config import settings
from app.core.database import get_session

router = APIRouter(prefix="/companies", tags=["companies"])


def get_company_service(session: Annotated[Session, Depends(get_session)]) -> CompanyService:
    return CompanyService(
        CompanyRepository(session), cache=configured_company_cache(settings.cache_redis_url)
    )


@router.get("", response_model=Page[CompanyListItem])
def search_companies(
    query: Annotated[CompanyQuery, Query()],
    service: Annotated[CompanyService, Depends(get_company_service)],
) -> Page[CompanyListItem]:
    return service.search(query)


@router.get("/{company_id}", response_model=CompanyDetail)
def get_company_detail(
    company_id: UUID,
    service: Annotated[CompanyService, Depends(get_company_service)],
) -> CompanyDetail:
    return service.get_detail(company_id)


@router.get("/{company_id}/jobs", response_model=Page[JobListItem])
def list_company_jobs(
    company_id: UUID,
    query: Annotated[JobQuery, Query()],
    service: Annotated[CompanyService, Depends(get_company_service)],
) -> Page[JobListItem]:
    return service.list_jobs(company_id, query)

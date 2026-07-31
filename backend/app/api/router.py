from fastapi import APIRouter

from app.collection.router import router as collection_router
from app.companies.router import router as companies_router

api_router = APIRouter()
api_router.include_router(companies_router)
api_router.include_router(collection_router)


@api_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

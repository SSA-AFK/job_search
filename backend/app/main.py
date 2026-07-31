from fastapi import FastAPI

from app.api.router import api_router
from app.core.errors import register_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="AI Company Search")
    register_error_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()

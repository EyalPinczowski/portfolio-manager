"""FastAPI application. OpenAPI is served at /api/openapi.json."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, imports, misc, portfolios
from app.config import get_settings
from app.db import get_engine, init_db, new_session
from app.securities import seed_securities


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db(get_engine())
    with new_session() as db:
        seed_securities(db)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Portfolio Manager API",
        version="0.1.0",
        description="Phase 1 backend. Not financial advice.",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    api = APIRouter(prefix="/api")
    for module in (auth, portfolios, imports, misc):
        api.include_router(module.router)

    @api.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api)
    return app


app = create_app()

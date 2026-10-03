"""FastAPI application. OpenAPI is served at /api/openapi.json."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, imports, misc, portfolios
from app.config import get_settings, validate_production
from app.db import get_engine, new_session, prepare_database
from app.logging_setup import configure_logging
from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.securities import seed_securities


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    validate_production(settings)  # refuses to start with unsafe production settings
    prepare_database(
        get_engine()
    )  # migrates (dev) or checks the schema (production); no create_all
    with new_session() as db:
        seed_securities(db)
    runner = None
    if settings.scheduler_in_process:  # lazy: the scheduler pulls in the market-data stack
        from app.scheduler.inprocess import start_in_process_scheduler

        runner = start_in_process_scheduler(settings)
    app.state.scheduler = runner
    try:
        yield
    finally:
        if runner is not None:
            runner.shutdown()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    production = settings.env == "production"  # no interactive docs or schema endpoint there
    app = FastAPI(
        title="Portfolio Manager API",
        version="0.1.0",
        description="Phase 1 backend. Not financial advice.",
        openapi_url=None if production else "/api/openapi.json",
        docs_url=None if production else "/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(BodySizeLimitMiddleware, settings_factory=get_settings)
    app.add_middleware(SecurityHeadersMiddleware, settings_factory=get_settings)
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

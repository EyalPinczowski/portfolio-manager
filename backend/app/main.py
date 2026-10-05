"""FastAPI application. OpenAPI is served at /api/openapi.json."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    admin,
    analyze,
    ask,
    auth,
    buy_ideas,
    dividends,
    exit_levels,
    funds,
    imports,
    lists,
    misc,
    portfolios,
    telegram,
    track_record,
    xray_rules,
)
from app.api import settings as settings_api
from app.config import get_settings, validate_production, validate_proxy
from app.db import get_engine, new_session, prepare_database
from app.errors import ApiError, api_error_handler
from app.health import HealthOut, HealthProbe, scheduler_state
from app.logging_setup import configure_logging
from app.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.model_probe import start_probe_in_background
from app.securities import seed_securities
from app.strictjson import StrictJsonRoute


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    validate_proxy(settings)  # a trusted client-IP header needs the shared secret (any env)
    validate_production(settings)  # refuses to start with unsafe production settings
    prepare_database(
        get_engine()
    )  # migrates (dev) or checks the schema (production); no create_all
    with new_session() as db:
        seed_securities(db)
    if settings.model_probe_enabled:  # in the background: a slow provider never delays startup
        start_probe_in_background(settings)
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


async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 without echoing the offending input back (it can be a 100 KB string or an account
    number); location, type and message are enough for the UI."""
    errors = [{k: v for k, v in e.items() if k in ("type", "loc", "msg")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    production = settings.env == "production"  # no interactive docs or schema endpoint there
    app = FastAPI(
        title="Holdwise API",
        version="0.1.0",
        description="Phase 1 backend. Not financial advice.",
        openapi_url=None if production else "/api/openapi.json",
        docs_url=None if production else "/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.health_probe = HealthProbe()
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
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
    api = APIRouter(prefix="/api", route_class=StrictJsonRoute)
    for module in (
        auth,
        portfolios,
        imports,
        misc,
        exit_levels,
        buy_ideas,
        analyze,
        ask,
        lists,
        settings_api,
        telegram,
        admin,
        track_record,
        xray_rules,
        funds,
        dividends,
    ):
        api.include_router(module.router)

    @api.get("/health", tags=["meta"], response_model=HealthOut)
    def health(request: Request) -> HealthOut:
        state, leader = scheduler_state(
            getattr(request.app.state, "scheduler", None), get_settings().scheduler_in_process
        )
        quotes_at, snapshot_at, paper_at = request.app.state.health_probe.read()
        return HealthOut(
            status="ok",
            scheduler=state,
            leader=leader,
            last_quotes_at=quotes_at,
            last_snapshot_at=snapshot_at,
            last_paper_resolve_at=paper_at,
        )

    app.include_router(api)
    return app


app = create_app()

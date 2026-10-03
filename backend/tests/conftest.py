from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.auth.passwords import reset_hasher_cache
from app.auth.ratelimit import clear_all_limiters
from app.config import get_settings
from app.db import get_engine, init_db, new_session
from app.providers.base import OcrResult, Quote
from app.providers.registry import Providers, set_providers
from app.securities import seed_securities
from app.timeutil import utcnow

# Run the whole suite on Postgres: `DATABASE_URL=postgresql+psycopg://user:pw@host/db pytest`
# (needs `uv sync --extra postgres`). Anything else means SQLite in a per-test temp file. The
# database is emptied before and after every test, so use a throwaway database.
_PG_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = _PG_URL.startswith(("postgresql", "postgres:"))


class FakeQuotes:
    def __init__(self) -> None:
        self.quotes: dict[str, Quote] = {}
        self.calls: list[list[str]] = []

    def set(self, symbol: str, price: float, currency: str, change_pct: float = 0.0) -> None:
        self.quotes[symbol] = Quote(
            symbol=symbol, price=price, currency=currency, change_pct=change_pct, as_of=utcnow()
        )

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        self.calls.append(list(symbols))
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


class FakeHistory:
    def __init__(self) -> None:
        self.frames: dict[str, pd.DataFrame] = {}

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        return self.frames.get(symbol)


class FakeOcr:
    name = "fake"

    def __init__(self, text: str) -> None:
        self.text = text

    def extract(self, image_bytes: bytes) -> OcrResult:
        return OcrResult(provider="fake", text=self.text)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(
        "DATABASE_URL", _PG_URL if USE_POSTGRES else f"sqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ATTEMPTS", "3")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    get_settings.cache_clear()
    get_engine.cache_clear()
    clear_all_limiters()
    reset_hasher_cache()
    if USE_POSTGRES:
        import app.models  # noqa: F401  (registers the tables)

        SQLModel.metadata.drop_all(get_engine())  # a clean slate left by nothing else
    init_db()
    with new_session() as db:
        seed_securities(db)
    yield
    set_providers(None)
    if USE_POSTGRES:
        SQLModel.metadata.drop_all(get_engine())
        get_engine().dispose()
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def db(env: None) -> Iterator[Session]:
    with new_session() as session:
        yield session


@pytest.fixture
def quotes(env: None) -> FakeQuotes:
    return FakeQuotes()


@pytest.fixture
def history(env: None) -> FakeHistory:
    return FakeHistory()


@pytest.fixture
def ocr_text() -> dict[str, str]:
    return {"text": ""}


@pytest.fixture
def providers(quotes: FakeQuotes, history: FakeHistory, ocr_text: dict[str, str]) -> Providers:
    p = Providers(quotes=quotes, history=history, ocr_factory=lambda: FakeOcr(ocr_text["text"]))
    set_providers(p)
    return p


@pytest.fixture
def client(env: None, providers: Providers) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def make_invite(days: int = 7) -> str:
    from app.cli import create_invite

    return create_invite(days)


SignupFn = Callable[..., TestClient]


@pytest.fixture
def signup(env: None, providers: Providers) -> Iterator[SignupFn]:
    """Create independent logged-in clients (separate cookie jars)."""
    from app.main import create_app

    clients: list[TestClient] = []

    def _signup(
        email: str = "alice@mail.com", password: str = "correct horse battery"
    ) -> TestClient:
        c = TestClient(create_app())
        c.__enter__()
        clients.append(c)
        r = c.post(
            "/api/auth/signup",
            json={
                "invite_code": make_invite(),
                "email": email,
                "password": password,
                "accept_disclaimer": True,
                "locale": "en",
            },
        )
        assert r.status_code == 201, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        return c

    yield _signup
    for c in clients:
        c.__exit__(None, None, None)


def png_bytes(w: int = 200, h: int = 300) -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGB", (w, h), (255, 255, 255))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import cli
from app.config import SIGNAL_NAMES, Settings
from app.main import create_app
from app.models import Invite, Security, User
from app.providers.base import OcrUnavailableError
from app.providers.ocr import get_ocr_provider
from app.providers.ocr.gemini import GeminiProvider
from app.providers.ocr.tesseract import TesseractProvider, tesseract_available
from app.securities import DEFAULT_SEED, search_securities, seed_securities
from app.timeutil import utcnow


def test_cli_create_invite_and_signup_with_it(
    env: None, providers: object, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["create-invite", "--days", "3"]) == 0
    code = capsys.readouterr().out.strip()
    from app.db import new_session

    with new_session() as db:
        inv = db.get(Invite, code)
        assert inv is not None and inv.used_by is None and inv.expires_at > utcnow()
    with TestClient(create_app()) as c:
        r = c.post(
            "/api/auth/signup",
            json={
                "invite_code": code,
                "email": "cli@mail.com",
                "password": "long enough pw",
                "accept_disclaimer": True,
                "locale": "he",
            },
        )
        assert r.status_code == 201


def test_cli_create_admin(env: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli.main(["create-admin", "--email", "Admin@Mail.com", "--password", "admin password 1"])
        == 0
    )
    from app.db import new_session

    with new_session() as db:
        u = db.exec(select(User)).one()
        assert (
            u.is_admin and u.email == "admin@mail.com" and u.password_hash.startswith("$argon2id$")
        )
    assert (
        cli.main(["create-admin", "--email", "admin@mail.com", "--password", "admin password 1"])
        == 1
    )
    assert cli.main(["create-admin", "--email", "x@mail.com", "--password", "short"]) == 1
    assert "error" in capsys.readouterr().err


def test_cli_bootstrap_admin_is_idempotent_and_silent(
    env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.config import get_settings
    from app.db import new_session

    assert cli.main(["bootstrap-admin"]) == 0
    assert "not configured" in capsys.readouterr().out
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "Boss@Mail.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "bootstrap password 1")
    get_settings.cache_clear()
    for expected in ("created", "already exists"):
        assert cli.main(["bootstrap-admin"]) == 0
        out = capsys.readouterr().out
        assert expected in out and "boss@mail.com" not in out.lower() and "password 1" not in out
    with new_session() as db:
        u = db.exec(select(User)).one()
        assert u.is_admin and u.email == "boss@mail.com"
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "short")
    get_settings.cache_clear()
    assert cli.bootstrap_admin(get_settings()) in ("already exists", "error: password too short")
    get_settings.cache_clear()


def test_cli_bootstrap_admin_reset_changes_password_and_signs_out(
    env: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import timedelta

    from app.auth.passwords import verify_password
    from app.config import get_settings
    from app.db import new_session
    from app.models import AuthSession
    from app.timeutil import utcnow

    admin_id = cli.create_admin("boss@mail.com", "old password 123")
    plain_id = 0
    with new_session() as db:
        plain = User(email="plain@mail.com", password_hash="h", is_admin=False)
        db.add(plain)
        db.add(
            AuthSession(
                token_hash="t1",
                user_id=admin_id,
                csrf_token="c",
                expires_at=utcnow() + timedelta(1),
            )
        )
        db.commit()
        assert plain.id is not None
        plain_id = plain.id
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "Boss@Mail.com")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "new password 456")
    get_settings.cache_clear()
    assert cli.bootstrap_admin(get_settings()) == "already exists"  # no reset without the flag
    monkeypatch.setenv("BOOTSTRAP_ADMIN_RESET", "true")
    get_settings.cache_clear()
    assert cli.main(["bootstrap-admin"]) == 0
    out = capsys.readouterr().out
    assert "password reset" in out and "boss@" not in out.lower() and "456" not in out
    with new_session() as db:
        admin = db.get(User, admin_id)
        assert admin is not None and verify_password(admin.password_hash, "new password 456")
        assert db.exec(select(AuthSession).where(AuthSession.user_id == admin_id)).first() is None
    # A non-admin with the configured email is never touched.
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "plain@mail.com")
    get_settings.cache_clear()
    assert "admin accounts only" in cli.bootstrap_admin(get_settings())
    with new_session() as db:
        p = db.get(User, plain_id)
        assert p is not None and p.password_hash == "h" and not p.is_admin
    get_settings.cache_clear()


# ---------------------------------------------------------------- seed data
def load_seed() -> list[dict[str, str]]:
    with Path(DEFAULT_SEED).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_seed_csv_shape_and_integrity() -> None:
    rows = load_seed()
    assert 140 <= len(rows) <= 200
    symbols = [r["symbol"] for r in rows]
    assert len(symbols) == len(set(symbols))
    assert set(rows[0]) == {
        "symbol",
        "name_en",
        "name_he",
        "tase_number",
        "asset_type",
        "market",
        "currency",
        "sector",
        "country",
        "dual_listing_group",
    }
    for r in rows:
        assert r["asset_type"] in {"stock", "etf", "crypto", "fund", "bond", "cash"}
        assert r["market"] in {"US", "TASE", "CRYPTO"}
        if r["market"] == "TASE":
            assert (
                r["symbol"].endswith(".TA") and r["currency"] == "ILS" and r["name_he"]
            )  # normalised from ILA
        if r["market"] == "CRYPTO":
            assert r["symbol"].endswith("-USD")
    tase_numbers = [r["tase_number"] for r in rows if r["tase_number"]]
    assert len(tase_numbers) == len(set(tase_numbers)) and all(n.isdigit() for n in tase_numbers)
    assert sum(1 for r in rows if r["market"] == "TASE") >= 40
    assert sum(1 for r in rows if r["market"] == "CRYPTO") == 15
    assert sum(1 for r in rows if r["asset_type"] == "etf") >= 25


def test_seed_dual_listings_pair_a_tase_and_a_us_line() -> None:
    groups: dict[str, set[str]] = {}
    for r in load_seed():
        if r["dual_listing_group"]:
            groups.setdefault(r["dual_listing_group"], set()).add(r["market"])
    assert {"TEVA", "NICE", "ESLT", "ICL"} <= set(groups)
    assert all(m == {"TASE", "US"} for m in groups.values())
    by = {r["symbol"]: r for r in load_seed()}
    assert (
        by["CHKP"]["dual_listing_group"] == "" and by["WIX"]["dual_listing_group"] == ""
    )  # US-only
    assert by["TEVA.TA"]["tase_number"] == "629014" and by["TEVA.TA"]["name_he"] == "טבע"


def test_seed_is_idempotent(db: Session) -> None:
    before = len(db.exec(select(Security)).all())
    assert before >= 140
    assert seed_securities(db) == 0
    assert len(db.exec(select(Security)).all()) == before
    assert {s.symbol for s in search_securities(db, "ביטקוין")} >= {"BTC-USD"}


# ---------------------------------------------------------------- OCR provider selection
def test_ocr_provider_selection_and_graceful_degradation(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_ocr_provider(Settings(gemini_api_key=None)), TesseractProvider)
    assert isinstance(get_ocr_provider(Settings(gemini_api_key="k")), GeminiProvider)
    with pytest.raises(OcrUnavailableError):
        GeminiProvider(Settings(gemini_api_key=None)).extract(b"x")
    monkeypatch.setattr("app.providers.ocr.tesseract.shutil.which", lambda _: None)
    assert tesseract_available() is False
    with pytest.raises(OcrUnavailableError):
        TesseractProvider(Settings()).extract(b"x")


def test_tesseract_provider_uses_heb_eng(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.ocr.tesseract as mod

    monkeypatch.setattr(mod, "tesseract_available", lambda: True)
    seen: dict[str, str] = {}

    def fake(img: object, lang: str, config: str = "") -> str:
        seen["lang"] = lang
        return "טבע 1 2 2"

    import pytesseract

    monkeypatch.setattr(pytesseract, "image_to_string", fake)
    from tests.conftest import png_bytes

    res = TesseractProvider(Settings()).extract(png_bytes())
    assert seen["lang"] == "heb+eng" and res.text == "טבע 1 2 2" and res.provider == "tesseract"


def test_gemini_provider_sends_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.genai as genai

    captured: dict[str, object] = {}

    class Models:
        def generate_content(self, model: str, contents: object, config: object) -> object:
            captured["model"], captured["config"] = model, config

            class R:
                text = '{"rows":[{"name":"טבע","quantity":1,"price":2,"value":2,"unit":"agorot","currency":"ILS"}]}'

            return R()

    class Client:
        def __init__(self, api_key: str) -> None:
            captured["key"] = api_key
            self.models = Models()

    monkeypatch.setattr(genai, "Client", Client)
    from tests.conftest import png_bytes

    res = GeminiProvider(Settings(gemini_api_key="secret", gemini_model="m")).extract(png_bytes())
    assert captured["key"] == "secret" and captured["model"] == "m"
    cfg = captured["config"]
    assert getattr(cfg, "response_mime_type", None) == "application/json"
    assert getattr(cfg, "response_schema", None) is not None
    assert res.rows is not None and res.rows[0].unit == "agorot"


def test_signal_names_are_the_six_signals() -> None:
    assert SIGNAL_NAMES == (
        "technical",
        "patterns",
        "fundamentals",
        "analysts",
        "geo_news",
        "sentiment",
    )

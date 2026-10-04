"""openapi.json is the contract the frontend generates its types from."""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

SPEC_FILE = Path(__file__).resolve().parent.parent / "openapi.json"


def live_spec() -> dict:  # type: ignore[type-arg]
    return create_app().openapi()


def test_committed_openapi_json_is_current() -> None:
    committed = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(live_spec())), (
        "backend/openapi.json is stale: run `python scripts/dump_openapi.py` in backend/"
    )


def test_enums_are_real_enums() -> None:
    schemas = live_spec()["components"]["schemas"]

    def enum_of(schema: str, field: str) -> list[str]:
        return list(schemas[schema]["properties"][field]["enum"])

    assert enum_of("HoldingOut", "market") == ["US", "TASE", "CRYPTO"]
    assert enum_of("SecurityHit", "market") == ["US", "TASE", "CRYPTO"]
    assert enum_of("HoldingOut", "asset_type") == ["stock", "etf", "crypto", "fund", "bond", "cash"]
    assert enum_of("MeOut", "locale") == ["he", "en"]
    assert enum_of("SignupIn", "locale") == ["he", "en"]
    assert enum_of("ProposedChange", "type") == [
        "buy",
        "sell",
        "deposit",
        "withdrawal",
        "keep",  # 2.0-E: a holding missing from a full update that the user keeps
    ]
    assert enum_of("RiskFilterOut", "stop_type") == ["fixed", "trailing", "both"]
    flags = schemas["ImportRowModel"]["properties"]["flags"]["items"]
    assert {"currency_changed", "low_confidence_match", "missing_fields"} <= set(flags["enum"])


def test_contract_table_routes_and_shapes_exist() -> None:
    spec = live_spec()
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]
    assert "post" in paths["/api/me/export"] and "get" not in paths["/api/me/export"]
    assert "delete" in paths["/api/me"]
    for p, m in [
        ("/api/auth/sessions", "get"),
        ("/api/auth/sessions/{session_id}", "delete"),
        ("/api/auth/sessions/revoke-all", "post"),
        ("/api/launch-gate", "get"),
        ("/api/portfolios/{portfolio_id}/imports/rows", "post"),
    ]:
        assert m in paths[p], (p, m)
    assert set(schemas["SessionOut"]["properties"]) == {
        "id",
        "created_at",
        "last_seen_at",
        "current",
    }
    assert schemas["SessionOut"]["properties"]["id"]["type"] == "integer"
    assert set(schemas["LaunchGateOut"]["properties"]) == {"open", "reasons"}
    assert "expires_at" in schemas["ImportDraftOut"]["properties"]
    assert schemas["ImportRowsBody"]["required"] == ["rows"]
    assert schemas["PasswordBody"]["required"] == ["password"]


def test_upload_route_is_a_raw_image_body_not_multipart() -> None:
    body = live_spec()["paths"]["/api/portfolios/{portfolio_id}/imports"]["post"]["requestBody"]
    assert set(body["content"]) == {"image/png", "image/jpeg", "image/webp"}
    assert "multipart/form-data" not in body["content"]


def test_every_body_model_forbids_unknown_fields() -> None:
    schemas = live_spec()["components"]["schemas"]
    for name in (
        "SignupIn", "LoginIn", "PasswordBody", "PortfolioCreate", "PortfolioPatch", "RiskFilterIn",
        "RiskOverride", "HoldingCreate", "HoldingPatch", "AlertCreate", "ImportPatch",
        "ImportRowsBody", "ImportRowModel", "ProposedChange",
    ):  # fmt: skip
        assert schemas[name].get("additionalProperties") is False, name

"""Every blind spot the Phase 1.5 re-review found in `verdict_contract.py` is now caught."""

from __future__ import annotations

import enum
from typing import Any, Literal

import pytest
from fastapi import Depends, FastAPI
from pydantic import BaseModel

from app.launchgate import require_launch_gate
from tests.verdict_contract import find_violations, verdict_token


def demo(model: type[BaseModel], gated: bool = False) -> FastAPI:
    app = FastAPI()
    deps = [Depends(require_launch_gate)] if gated else []

    @app.get("/x", response_model=model, dependencies=deps)
    def route() -> Any:
        return {}

    return app


def make(**fields: Any) -> type[BaseModel]:
    from pydantic import create_model

    return create_model("Payload", **{k: (t, ...) for k, t in fields.items()})


@pytest.mark.parametrize(
    "field",
    ["rating", "outlook", "target_price", "bullish", "bearish", "trim_pct", "STRONGBUY",
     "opinion", "call", "signal", "grade", "strong", "add", "strongBuy", "sellnow", "analystRating"],
)  # fmt: skip
def test_review_blind_spot_field_names_are_caught(field: str) -> None:
    assert verdict_token(field) is not None, field
    found = find_violations(demo(make(**{field: str})))
    assert [v.field for v in found] == [field]


def test_ordinary_names_still_pass() -> None:
    for name in ("holding", "holdings", "holder", "signals", "weights", "scores", "advisor_id"):
        assert verdict_token(name) is None, name


def test_literal_enum_values_are_scanned() -> None:
    found = find_violations(demo(make(status=Literal["buy", "sell"])))  # the review's example
    assert len(found) == 2 and all("enum value" in v.reason for v in found)
    assert find_violations(demo(make(status=Literal["open", "closed"]))) == []


def test_enum_class_values_and_const_are_scanned() -> None:
    class Stance(enum.StrEnum):
        A = "STRONGBUY"
        B = "neutral"

    first = next(v.reason for v in find_violations(demo(make(kind=Stance))))
    assert first.startswith("verdict-like enum value 'STRONGBUY'")
    assert len(find_violations(demo(make(kind=Literal["Hold"])))) == 1


def test_gated_route_may_carry_an_allowlisted_enum_value() -> None:
    model = make(status=Literal["buy", "hold"])
    assert find_violations(demo(model, gated=True), {"Payload.status": "CIO output"}) == []
    open_route = find_violations(demo(model), {"Payload.status": "CIO output"})
    assert open_route and "require_launch_gate" in open_route[0].reason


def test_dict_str_any_payloads_are_banned_unless_allowlisted() -> None:
    model = make(explanation=dict[str, Any])  # the shape `explanation` had before the typed one
    assert [v.field for v in find_violations(demo(model))] == ["explanation"]
    used: set[str] = set()
    assert find_violations(demo(model), None, {"Payload.explanation": "temp"}, used) == []
    assert used == {"Payload.explanation"}


@pytest.mark.parametrize(
    "hidden", [dict[str, Any], list[dict[str, Any]], Any, dict[str, Any] | None]
)
def test_untyped_fields_hidden_in_lists_and_unions_are_caught(hidden: Any) -> None:
    assert find_violations(demo(make(payload=hidden)))


def test_typed_dicts_and_models_pass() -> None:
    class Inner(BaseModel):
        score: float

    assert find_violations(demo(make(by_name=dict[str, float], items=list[Inner]))) == []


def test_a_route_without_response_model_is_untyped() -> None:
    app = FastAPI()

    @app.get("/bare")
    def bare() -> dict[str, Any]:
        return {"verdict": "buy"}  # nothing in the schema shows it

    found = find_violations(app)
    assert [(v.method, v.path, v.reason) for v in found] == [
        ("GET", "/bare", "untyped response (Any/dict)")
    ]
    assert find_violations(app, None, {"GET /bare": "legacy"}) == []


def test_a_typed_model_field_literal_returned_by_a_bare_dict_route_is_still_caught_by_type() -> (
    None
):
    app = FastAPI()

    @app.get("/bare")
    def bare() -> list[Any]:
        return []

    assert find_violations(app)

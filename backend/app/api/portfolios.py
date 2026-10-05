"""Portfolios, holdings, summary, x-ray and heat map."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from sqlmodel import Session, col, select

from app.api.schemas import (
    FundHoldingOut,
    HeatmapItem,
    HoldingCreate,
    HoldingOut,
    HoldingPatch,
    PortfolioCreate,
    PortfolioOut,
    PortfolioPatch,
    RiskFilterIn,
    ScoreCardMini,
    SummaryOut,
    XrayOut,
)
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.auth.ratelimit import enforce_limit, holding_add_limiter
from app.config import Settings
from app.db import new_session
from app.funds import ValueBasis, fund_id_of, is_fund_symbol, value_flags
from app.models import (
    FundHolding,
    Holding,
    HoldingsSnapshot,
    ImportDraft,
    Portfolio,
    PortfolioSnapshot,
    Transaction,
)
from app.portfolio.freshness import price_is_fresh
from app.portfolio.heatmap import build_heatmap
from app.portfolio.postmortem import PostmortemOut
from app.portfolio.postmortem_data import post_mortem_for_portfolio
from app.portfolio.quotes import refresh_symbols
from app.portfolio.screenshot import update_is_stale
from app.portfolio.summary import build_summary
from app.portfolio.valuation import (
    PortfolioValuation,
    ValuedHolding,
    ensure_tracking_started,
    record_quantity_change,
    sync_pending_flows,
    value_portfolio,
)
from app.portfolio.xray import build_xray
from app.providers.registry import get_fund_provider, get_providers
from app.repo import get_holding_in_portfolio, get_portfolio, list_portfolios
from app.scoring.risk import resolve_risk_filter
from app.scoring.scorecard import get_cached_scorecard, is_fresh, refresh_scorecard
from app.securities import get_or_create_security
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc, local_today, utcnow
from app.usersettings import effective

router = APIRouter(tags=["portfolios"], route_class=StrictJsonRoute)


# ---------------------------------------------------------------- helpers
def portfolio_out(p: Portfolio, settings: Settings) -> PortfolioOut:
    assert p.id is not None
    return PortfolioOut(
        id=p.id,
        name=p.name,
        base_currency=p.base_currency,
        risk_filter=resolve_risk_filter(p.risk_filter, settings).model_dump(),  # type: ignore[arg-type]
        tracking_started_at=p.tracking_started_at.isoformat() if p.tracking_started_at else None,
        expected_return_pct=p.expected_return_pct,
        expected_return_horizon_months=p.expected_return_horizon_months,
        last_screenshot_update_at=(
            as_utc(p.last_screenshot_update_at) if p.last_screenshot_update_at else None
        ),
        screenshot_update_stale=update_is_stale(p, settings),
        created_at=as_utc(p.created_at),
    )


def _clean_risk_filter(body: RiskFilterIn, settings: Settings) -> dict[str, Any]:
    """Validated input (bounds and enums are enforced by `RiskFilterIn`) -> the stored dict."""
    raw = body.model_dump(exclude_none=True)
    resolved = resolve_risk_filter(raw, settings)
    out: dict[str, Any] = {"preset": resolved.preset}
    out.update({k: v for k, v in raw.items() if k != "preset"})
    return out


def _fund_out(db: Session, v: ValuedHolding, settings: Settings) -> FundHoldingOut | None:
    h = v.holding
    fid = fund_id_of(h.symbol)
    if v.security.asset_type != "fund" or fid is None or h.id is None:
        return None
    fh = db.get(FundHolding, h.id)
    basis: ValueBasis = (
        "manual_value" if v.price_source == "manual" else "cost_only" if h.avg_cost else "no_value"
    )
    return FundHoldingOut(
        fund_id=fid,
        track=fh.track if fh else None,
        value_basis=basis,
        manual_value_ils=fh.manual_value_ils if fh else None,
        manual_value_as_of=fh.manual_value_as_of if fh else None,
        flags=value_flags(basis),
        credit=settings.gemelnet_credit,
    )


def holding_outs(
    db: Session,
    portfolio: Portfolio,
    settings: Settings,
    valuation: PortfolioValuation | None = None,
) -> tuple[list[HoldingOut], list[str]]:
    val = valuation or value_portfolio(db, portfolio, settings)
    total = val.total_ils
    out: list[HoldingOut] = []
    missing_scores: list[str] = []
    for v in val.holdings:
        h, sec = v.holding, v.security
        assert h.id is not None
        card = get_cached_scorecard(db, h.symbol)
        if card is None or not is_fresh(db, h.symbol, settings):
            missing_scores.append(h.symbol)
        mini = ScoreCardMini(
            total=card["total"] if card else 0.0,
            technical=card["technical"] if card else 0.0,
            patterns=card["patterns"] if card else 0.0,
            confidence=card["confidence"] if card else 0.0,
        )
        pnl = None
        if v.pnl_ils is not None and v.pnl_usd is not None and v.pnl_pct is not None:
            pnl = {
                "ils": round(v.pnl_ils, 2),
                "usd": round(v.pnl_usd, 2),
                "pct": round(v.pnl_pct, 4),
            }
        fund = _fund_out(db, v, settings)
        fh_row = db.get(FundHolding, h.id) if fund is not None else None
        out.append(
            HoldingOut(
                id=h.id,
                symbol=h.symbol,
                name_en=(fh_row.fund_name if fh_row and fh_row.fund_name else sec.name_en),
                name_he=sec.name_he,
                asset_type=sec.asset_type,
                market=sec.market,
                quantity=h.quantity,
                price=round(v.price, 6),
                currency=v.currency,
                day_change_pct=round(v.day_change_pct, 4),
                value_ils=round(v.value_ils, 2),
                pnl=pnl,  # type: ignore[arg-type]
                weight_pct=round(v.value_ils / total * 100.0, 2) if total else 0.0,
                horizon=h.horizon,  # type: ignore[arg-type]
                stop_tp_status=(
                    "no_levels"
                    if fund is not None
                    else "needs_horizon"
                    if h.horizon is None
                    else "missing"
                ),
                score_card=mini,
                price_stale=v.stale,
                price_source=v.quote_source,
                price_basis=v.price_basis,  # type: ignore[arg-type]
                price_as_of=as_utc(v.as_of) if v.as_of is not None else None,
                price_is_fresh=price_is_fresh(v),
                price_flag=v.quote_flag,
                fund=fund,
            )
        )
    out.sort(key=lambda x: x.value_ils, reverse=True)
    return out, missing_scores


def refresh_symbol_data(symbols: list[str]) -> None:
    """Background task: fetch quotes and score cards for symbols that have none yet."""
    providers = get_providers()
    with new_session() as db:
        refresh_symbols(db, symbols, providers.quotes)
        for sym in symbols:
            if is_fund_symbol(sym):
                continue  # no chart to score
            try:
                refresh_scorecard(db, sym, providers.history)
            except Exception:  # pragma: no cover - defensive
                db.rollback()


# ---------------------------------------------------------------- portfolios CRUD
@router.get("/portfolios", response_model=list[PortfolioOut])
def get_portfolios(user: UserDep, db: DbDep, settings: SettingsDep) -> list[PortfolioOut]:
    assert user.id is not None
    return [portfolio_out(p, settings) for p in list_portfolios(db, user.id)]


@router.post("/portfolios", response_model=PortfolioOut, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    body: PortfolioCreate, user: UserDep, db: DbDep, settings: SettingsDep
) -> PortfolioOut:
    assert user.id is not None
    p = Portfolio(
        owner_id=user.id,
        name=body.name,
        base_currency=body.base_currency,
        risk_filter={"preset": settings.default_risk_preset},
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return portfolio_out(p, settings)


# NOTE: declared before /portfolios/{portfolio_id}/... so "combined" is not parsed as an id.
@router.get("/portfolios/combined/summary", response_model=SummaryOut)
def combined_summary(user: UserDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    assert user.id is not None
    return build_summary(
        db,
        list_portfolios(db, user.id),
        get_providers().history,
        settings,
        week_start_day=effective(db, user, settings).week_start_day,
    )


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioOut)
def get_one(portfolio_id: int, user: UserDep, db: DbDep, settings: SettingsDep) -> PortfolioOut:
    assert user.id is not None
    return portfolio_out(get_portfolio(db, user.id, portfolio_id), settings)


@router.patch("/portfolios/{portfolio_id}", response_model=PortfolioOut)
def patch_portfolio(
    portfolio_id: int, body: PortfolioPatch, user: UserDep, db: DbDep, settings: SettingsDep
) -> PortfolioOut:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    if body.name is not None:
        p.name = body.name
    if body.base_currency is not None:
        p.base_currency = body.base_currency
    if "risk_filter" in body.model_fields_set and body.risk_filter is not None:
        p.risk_filter = _clean_risk_filter(body.risk_filter, settings)
    if "expected_return_pct" in body.model_fields_set:  # validated as a pair by the schema
        p.expected_return_pct = body.expected_return_pct
        p.expected_return_horizon_months = body.expected_return_horizon_months
    db.add(p)
    db.commit()
    db.refresh(p)
    return portfolio_out(p, settings)


@router.delete("/portfolios/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(portfolio_id: int, user: UserDep, db: DbDep) -> Response:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    for model in (Holding, HoldingsSnapshot, Transaction, PortfolioSnapshot, ImportDraft):
        for row in db.exec(select(model).where(col(model.portfolio_id) == p.id)).all():  # type: ignore[attr-defined]
            db.delete(row)
    db.delete(p)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- reads
@router.get("/portfolios/{portfolio_id}/summary", response_model=SummaryOut)
def summary(portfolio_id: int, user: UserDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    return build_summary(
        db,
        [p],
        get_providers().history,
        settings,
        week_start_day=effective(db, user, settings).week_start_day,
    )


@router.get("/portfolios/{portfolio_id}/holdings", response_model=list[HoldingOut])
def holdings(
    portfolio_id: int,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> list[HoldingOut]:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    out, missing = holding_outs(db, p, settings)
    if missing:
        background.add_task(refresh_symbol_data, missing)
    return out


@router.get("/portfolios/{portfolio_id}/post-mortem", response_model=PostmortemOut)
def post_mortem(
    portfolio_id: int,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    start: date | None = None,
    end: date | None = None,
) -> PostmortemOut:
    """Why the return differs from the expectation: deterministic, template text, no LLM."""
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    if start is not None and end is not None and start >= end:
        raise HTTPException(422, "start must be before end")
    return post_mortem_for_portfolio(
        db, p, get_providers().history, settings, start, end, local_today()
    )


@router.get("/portfolios/{portfolio_id}/xray", response_model=XrayOut)
def xray(portfolio_id: int, user: UserDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    assert user.id is not None
    return build_xray(db, get_portfolio(db, user.id, portfolio_id), settings)


@router.get("/portfolios/{portfolio_id}/heatmap", response_model=list[HeatmapItem])
def heatmap(
    portfolio_id: int, user: UserDep, db: DbDep, settings: SettingsDep
) -> list[dict[str, Any]]:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    return build_heatmap(value_portfolio(db, p, settings))


# ---------------------------------------------------------------- manual holding edits
_FUND_FIELDS = ("manual_value_ils", "manual_value_as_of", "fund_name", "track")


def _check_fund_fields(symbol: str, fields: set[str], as_of: date | None) -> None:
    """Fund-only inputs on a non-fund holding, or a statement date in the future: a 422."""
    given = [f for f in _FUND_FIELDS if f in fields]
    if given and not is_fund_symbol(symbol):
        raise HTTPException(
            422,
            f"{', '.join(given)} apply to Israeli fund holdings only (symbol GEMEL-<fund number>)",
        )
    if as_of is not None and as_of > local_today():
        raise HTTPException(422, "manual_value_as_of cannot be in the future")


def _public_fund_facts(fund_id: str) -> tuple[str | None, str | None]:
    """(name, classification) from the fund dataset, best effort: a gap is just (None, None)."""
    try:
        field = get_fund_provider().get_fund(fund_id)
    except Exception:  # a provider failure never blocks a manual entry
        return None, None
    if field.value is None:
        return None, None
    return field.value.info.name, field.value.info.classification


def _single(db: Session, p: Portfolio, holding_id: int, settings: Settings) -> HoldingOut:
    outs, _ = holding_outs(db, p, settings)
    for o in outs:
        if o.id == holding_id:
            return o
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Holding not found")


def _valued(
    db: Session, p: Portfolio, settings: Settings, holding_id: int | None
) -> ValuedHolding | None:
    """The valuation of one holding (price and currency exactly as the portfolio value uses them).

    A flow is recorded in the price's own currency, never `Security.currency`, and only when the
    price is a real one (`performance_priced`); otherwise the quantity goes into the holding's
    marker (`record_quantity_change`).
    """
    for v in value_portfolio(db, p, settings).holdings:
        if v.holding.id == holding_id:
            return v
    return None


@router.post(
    "/portfolios/{portfolio_id}/holdings",
    response_model=HoldingOut,
    status_code=status.HTTP_201_CREATED,
)
def add_holding(
    portfolio_id: int,
    body: HoldingCreate,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> HoldingOut:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    assert p.id is not None
    enforce_limit(
        holding_add_limiter, f"user:{user.id}", settings.holding_add_rate_limit_per_hour, 3600.0
    )
    symbol = body.symbol  # already trimmed, upper-cased and pattern-checked
    _check_fund_fields(symbol, set(body.model_fields_set), body.manual_value_as_of)
    if is_fund_symbol(symbol) and body.cost_currency == "USD":
        raise HTTPException(422, "Israeli funds are held in ILS only")
    pub_name, pub_track = (None, None)
    fund_id = fund_id_of(symbol)
    if fund_id is not None:
        pub_name, pub_track = _public_fund_facts(fund_id)
    sec = get_or_create_security(db, symbol)
    if pub_name and sec.name_en.startswith("Fund "):
        sec.name_en = pub_name  # the dataset's public name; the user's own entry stays per holding
        db.add(sec)
    dup = db.exec(
        select(Holding).where(Holding.portfolio_id == p.id, Holding.symbol == symbol)
    ).first()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{symbol} is already in this portfolio")
    cost_currency = body.cost_currency or sec.currency
    h = Holding(
        portfolio_id=p.id,
        symbol=symbol,
        quantity=body.quantity,
        avg_cost=body.avg_cost,
        cost_currency=cost_currency,
        horizon=body.horizon,
    )
    db.add(h)
    db.flush()
    if fund_id is not None:
        assert h.id is not None
        db.add(
            FundHolding(
                holding_id=h.id,
                fund_id=fund_id,
                fund_name=body.fund_name,
                track=body.track or pub_track,
                manual_value_ils=body.manual_value_ils,
                manual_value_as_of=(
                    (body.manual_value_as_of or local_today()) if body.manual_value_ils else None
                ),
            )
        )
    db.commit()
    db.refresh(h)
    refresh_symbols(db, [symbol], get_providers().quotes)  # best effort, bounded by the provider
    if p.tracking_started_at is None:
        ensure_tracking_started(db, p)
    else:
        # A priced holding is a buy flow at its price; an unpriced one (or one valued at its cost)
        # gets its one marker, settled at the first real price.
        record_quantity_change(db, p, h, body.quantity, _valued(db, p, settings, h.id))
        sync_pending_flows(db, p, settings=settings)
        db.commit()
    background.add_task(refresh_symbol_data, [symbol])
    assert h.id is not None
    return _single(db, p, h.id, settings)


def _patch_fund_row(db: Session, h: Holding, body: HoldingPatch) -> None:
    assert h.id is not None
    fid = fund_id_of(h.symbol)
    assert fid is not None
    row = db.get(FundHolding, h.id) or FundHolding(holding_id=h.id, fund_id=fid)
    fields = body.model_fields_set
    if "manual_value_ils" in fields:
        row.manual_value_ils = body.manual_value_ils  # explicit null clears it
        if body.manual_value_ils is None:
            row.manual_value_as_of = None
        elif "manual_value_as_of" not in fields:
            row.manual_value_as_of = local_today()
    if "manual_value_as_of" in fields and body.manual_value_as_of is not None:
        if row.manual_value_ils is None:
            raise HTTPException(422, "manual_value_as_of needs a manual_value_ils")
        row.manual_value_as_of = body.manual_value_as_of
    if "fund_name" in fields:
        row.fund_name = body.fund_name
    if "track" in fields:
        row.track = body.track
    row.updated_at = utcnow()
    db.add(row)
    db.flush()


@router.patch("/portfolios/{portfolio_id}/holdings/{holding_id}", response_model=HoldingOut)
def patch_holding(
    portfolio_id: int,
    holding_id: int,
    body: HoldingPatch,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
) -> HoldingOut:
    assert user.id is not None
    h, p = get_holding_in_portfolio(db, user.id, portfolio_id, holding_id)
    fields = body.model_fields_set
    _check_fund_fields(h.symbol, set(fields), body.manual_value_as_of)
    if is_fund_symbol(h.symbol) and body.cost_currency == "USD":
        raise HTTPException(422, "Israeli funds are held in ILS only")
    if body.quantity is not None and body.quantity != h.quantity:
        record_quantity_change(db, p, h, body.quantity - h.quantity, _valued(db, p, settings, h.id))
        h.quantity = body.quantity
    if "avg_cost" in fields:
        h.avg_cost = body.avg_cost
    if body.cost_currency is not None:
        h.cost_currency = body.cost_currency
    if "horizon" in fields:
        h.horizon = body.horizon
    if "risk_override" in fields:
        override = body.risk_override.model_dump(exclude_none=True) if body.risk_override else None
        h.risk_override = override or None
    db.add(h)
    db.flush()
    if any(f in fields for f in _FUND_FIELDS):
        _patch_fund_row(db, h, body)
    sync_pending_flows(db, p, settings=settings)  # settles only on a real quote/screenshot price
    db.commit()
    assert h.id is not None
    return _single(db, p, h.id, settings)


@router.delete(
    "/portfolios/{portfolio_id}/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_holding(
    portfolio_id: int, holding_id: int, user: UserDep, db: DbDep, settings: SettingsDep
) -> Response:
    assert user.id is not None
    h, p = get_holding_in_portfolio(db, user.id, portfolio_id, holding_id)
    record_quantity_change(db, p, h, -h.quantity, _valued(db, p, settings, h.id))
    db.delete(h)
    db.flush()
    sync_pending_flows(db, p, settings=settings)  # drops the marker of an unpriced holding
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

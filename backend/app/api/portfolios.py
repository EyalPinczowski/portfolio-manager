"""Portfolios, holdings, summary, x-ray and heat map."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from sqlmodel import Session, col, select

from app.api.schemas import (
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
from app.config import Settings
from app.db import new_session
from app.models import (
    Holding,
    HoldingsSnapshot,
    ImportDraft,
    Portfolio,
    PortfolioSnapshot,
    Transaction,
)
from app.portfolio.heatmap import build_heatmap
from app.portfolio.quotes import refresh_symbols
from app.portfolio.summary import build_summary
from app.portfolio.valuation import (
    PortfolioValuation,
    ensure_tracking_started,
    sync_pending_flows,
    value_portfolio,
)
from app.portfolio.xray import build_xray
from app.providers.fx_provider import get_usd_ils
from app.providers.registry import get_providers
from app.repo import get_holding_in_portfolio, get_portfolio, list_portfolios
from app.scoring.risk import resolve_risk_filter
from app.scoring.scorecard import get_cached_scorecard, is_fresh, refresh_scorecard
from app.securities import get_or_create_security
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc, local_today

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
        created_at=as_utc(p.created_at),
    )


def _clean_risk_filter(body: RiskFilterIn, settings: Settings) -> dict[str, Any]:
    """Validated input (bounds and enums are enforced by `RiskFilterIn`) -> the stored dict."""
    raw = body.model_dump(exclude_none=True)
    resolved = resolve_risk_filter(raw, settings)
    out: dict[str, Any] = {"preset": resolved.preset}
    out.update({k: v for k, v in raw.items() if k != "preset"})
    return out


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
        out.append(
            HoldingOut(
                id=h.id,
                symbol=h.symbol,
                name_en=sec.name_en,
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
                stop_tp_status="needs_horizon" if h.horizon is None else "missing",
                score_card=mini,
                price_stale=v.stale,
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
    return build_summary(db, list_portfolios(db, user.id), get_providers().history, settings)


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
    return build_summary(db, [p], get_providers().history, settings)


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
def _single(db: Session, p: Portfolio, holding_id: int, settings: Settings) -> HoldingOut:
    outs, _ = holding_outs(db, p, settings)
    for o in outs:
        if o.id == holding_id:
            return o
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Holding not found")


def _valued(
    db: Session, p: Portfolio, settings: Settings, holding_id: int | None
) -> tuple[float, str]:
    """(price, currency) the valuation uses for this holding. Never `Security.currency`.

    A cost-basis price is in the holding's `cost_currency`, a quote in the quote's currency; the
    flow must be recorded in whatever currency the price actually is (AAPL with an ILS cost of 700
    is a 700 ILS price, not 700 USD). An unpriced holding gives (0, ...): no flow is recorded now,
    `sync_pending_flows` records it when the first real price arrives.
    """
    val = value_portfolio(db, p, settings)
    for v in val.holdings:
        if v.holding.id == holding_id:
            return v.price, v.currency
    return 0.0, "USD"


def _record_flow(
    db: Session, p: Portfolio, symbol: str, delta: float, price: float, currency: str
) -> None:
    """Quantity change after tracking started is an external flow (buy in / sell out).

    `price`/`currency` come from the valuation (`_valued`). With no price there is nothing to
    record yet: see `sync_pending_flows` (deferred flow).
    """
    assert p.id is not None
    if p.tracking_started_at is None or abs(delta) < 1e-12 or price <= 0:
        return
    usd_ils = get_usd_ils(db)
    db.add(
        Transaction(
            portfolio_id=p.id,
            symbol=symbol,
            type="buy" if delta > 0 else "sell",
            quantity=abs(delta),
            price=price,
            amount=abs(delta) * price,
            currency=currency,
            fx_to_ils=usd_ils if currency == "USD" else 1.0,
            date=local_today(),
            inferred=False,
        )
    )


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
    symbol = body.symbol  # already trimmed, upper-cased and pattern-checked
    sec = get_or_create_security(db, symbol)
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
    db.commit()
    db.refresh(h)
    refresh_symbols(db, [symbol], get_providers().quotes)  # best effort, bounded by the provider
    price, currency = _valued(db, p, settings, h.id)
    if p.tracking_started_at is None:
        ensure_tracking_started(db, p)
    else:
        _record_flow(db, p, symbol, body.quantity, price, currency)
        sync_pending_flows(db, p, settings=settings)  # unpriced: a marker, settled at first price
        db.commit()
    background.add_task(refresh_symbol_data, [symbol])
    assert h.id is not None
    return _single(db, p, h.id, settings)


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
    if body.quantity is not None and body.quantity != h.quantity:
        price, currency = _valued(db, p, settings, h.id)
        _record_flow(db, p, h.symbol, body.quantity - h.quantity, price, currency)
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
    sync_pending_flows(
        db, p, settings=settings
    )  # e.g. an avg_cost gave an unpriced holding a price
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
    price, currency = _valued(db, p, settings, h.id)
    _record_flow(db, p, h.symbol, -h.quantity, price, currency)
    db.delete(h)
    db.flush()
    sync_pending_flows(db, p, settings=settings)  # drops the marker of an unpriced holding
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""Portfolio fit: what the stock would do to THIS portfolio, under the user's risk filter.

Pure computation on already-loaded data (cached quote and bars, the user's valuation): it never
fetches anything and never produces a buy/sell verdict. Rules (CLAUDE.md):
- amount, currency and horizon are inputs with no defaults: missing ones go to `needs_input`.
- Sector / country / position caps are checked before and after spending the amount; an amount that
  would break a cap is shrunk to fit (the cap is named), never waved through.
- Levels come from `compute_exit_levels` (fresh live price only). The stop is never tightened to
  fit the risk filter: the size shrinks instead.
- A holding's own limits win over the portfolio's; a stock the portfolio already owns (or owns on
  its other listing) is an increase of an existing position, not a new one.
"""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from app.analyze.schemas import (
    ExposureCheck,
    FitStatus,
    HeldPosition,
    MaxPositionSize,
    NeedsInput,
    PortfolioFit,
    RuleResult,
)
from app.config import Settings
from app.models import Portfolio, PriceQuote, Security
from app.portfolio.valuation import PortfolioValuation
from app.portfolio.xray import _position_override
from app.providers.fx_provider import SUPPORTED_CURRENCIES, to_ils
from app.scoring.exit_levels import ExitLevelsResult, compute_exit_levels
from app.scoring.risk import RiskFilter
from app.scoring.screener import (
    SizeOut,
    _book,
    _cut,
    _floor_qty,
    _headroom,
    _valued,
    annualised_volatility_pct,
)
from app.signals.base import (
    MAX_ITEMS,
    Explanation,
    ExplanationSource,
    as_float_inputs,
)
from app.timeutil import as_utc

NOT_CHECKED = [
    "max_total_portfolio_risk_pct: needs a stop for every holding (see the portfolio exit review).",
    "blacklist: the risk presets have no blacklist field yet.",
]


def _exposure(
    *,
    dimension: str,
    name: str,
    rule: str,
    applies: bool,
    limit: float,
    source: str,
    existing: float,
    total: float,
    amount_ils: float | None,
    why_not: str = "",
) -> ExposureCheck:
    before = existing / total * 100.0 if total > 0 else 0.0
    after: float | None = None
    breaks: bool | None = None
    headroom: float | None = None
    if applies and total > 0:
        headroom = _headroom(existing, total, limit)
        headroom = None if math.isinf(headroom) else round(headroom, 2)
    if amount_ils is not None:
        after = (
            (existing + amount_ils) / (total + amount_ils) * 100.0 if total + amount_ils else 0.0
        )
        breaks = bool(applies and after > limit + 1e-9)
    label = {
        "position": "this position",
        "sector": f"{name} sector",
        "country": f"{name} exposure",
    }[dimension]
    if not applies:
        reason = why_not
    elif after is None:
        reason = f"{label.capitalize()} is {before:.1f}% of the portfolio now (limit {limit:g}%)."
    elif breaks:
        reason = (
            f"{label.capitalize()} would go from {before:.1f}% to {after:.1f}%, "
            f"above your {limit:g}% limit."
        )
    else:
        reason = (
            f"{label.capitalize()} would go from {before:.1f}% to {after:.1f}% (limit {limit:g}%)."
        )
    return ExposureCheck(
        dimension=dimension,  # type: ignore[arg-type]
        name=name,
        rule=rule,  # type: ignore[arg-type]
        applies=applies,
        limit_pct=limit,
        limit_source=source,
        before_pct=round(before, 2),
        after_pct=None if after is None else round(after, 2),
        breaks=breaks,
        headroom_ils=headroom,
        reason=reason,
    )


def incomplete_fit(needs: list[NeedsInput], portfolio_id: int | None, why: str) -> PortfolioFit:
    return PortfolioFit(
        status="incomplete",
        needs_input=needs,
        summary=why,
        portfolio_id=portfolio_id,
        rules_not_checked=list(NOT_CHECKED),
        explanation=Explanation(summary=why),
    )


def compute_fit(
    portfolio: Portfolio,
    val: PortfolioValuation,
    sec: Security,
    quote: PriceQuote | None,
    df: pd.DataFrame | None,
    risk: RiskFilter,
    *,
    risk_source: str,
    amount: float | None,
    currency: str | None,
    horizon: str | None,
    horizon_source: str | None,
    settings: Settings,
    now: datetime,
) -> PortfolioFit:
    assert portfolio.id is not None
    s = settings
    usd_ils = val.usd_ils
    needs: list[NeedsInput] = []
    if amount is None:
        needs.append("amount")
    if amount is not None and currency is None:
        needs.append("currency")
    if horizon is None:
        needs.append("horizon")
    amount_ils: float | None = None
    if amount is not None and currency is not None:
        amount_ils = to_ils(amount, currency, usd_ils)

    book = _book(val)
    total = book.total
    existing = book.existing(sec)
    held = book.holdings.get(sec.symbol)
    mode = "increase_existing" if existing > 0 else "new_position"
    held_out = (
        HeldPosition(
            holding_id=held.id if held is not None else None,
            quantity=held.quantity if held is not None else 0.0,
            value_ils=round(existing, 2),
            weight_pct=round(existing / total * 100.0, 2) if total > 0 else 0.0,
            horizon=held.horizon if held is not None else None,
            via_dual_listing=held is None,
        )
        if existing > 0
        else None
    )

    # ---- limits: the holding's own position limit wins when no what-if preset was asked for
    override = _position_override(held.risk_override) if held is not None else None
    pos_source = (
        "holding_override" if override is not None and risk_source != "request" else "risk_filter"
    )
    pos_cap = risk.max_position_pct
    sector_skip = {x.lower() for x in s.diversified_sectors}
    country_skip = {x.lower() for x in s.non_country_labels}
    have_book = total > 0
    why_empty = "The portfolio is empty, so concentration limits cannot be measured yet."
    exposures = [
        _exposure(
            dimension="position", name=sec.symbol, rule="max_position_pct", applies=have_book,
            limit=pos_cap, source=pos_source, existing=existing, total=total,
            amount_ils=amount_ils, why_not=why_empty,
        ),
        _exposure(
            dimension="sector", name=sec.sector, rule="max_sector_pct",
            applies=have_book and sec.sector.lower() not in sector_skip,
            limit=risk.max_sector_pct, source="risk_filter",
            existing=book.sector.get(sec.sector, 0.0), total=total, amount_ils=amount_ils,
            why_not=why_empty if not have_book else f"{sec.sector} is not capped (a broad bucket).",
        ),
        _exposure(
            dimension="country", name=sec.country, rule="max_country_pct",
            applies=have_book and sec.country.lower() not in country_skip,
            limit=risk.max_country_pct, source="risk_filter",
            existing=book.country.get(sec.country, 0.0), total=total, amount_ils=amount_ils,
            why_not=why_empty if not have_book else f"{sec.country} is not capped (a broad bucket).",
        ),
    ]  # fmt: skip
    broken: list[str] = [e.rule for e in exposures if e.breaks]

    # ---- the most that fits under the caps
    binding: tuple[float, str] | None = None
    for e in exposures:
        if (
            e.applies
            and e.headroom_ils is not None
            and (binding is None or e.headroom_ils < binding[0])
        ):
            binding = (e.headroom_ils, e.rule)
    max_size = MaxPositionSize(
        position_limit_pct=pos_cap,
        limit_source=pos_source,
        max_additional_ils=binding[0] if binding else None,
        max_additional_usd=round(binding[0] / usd_ils, 2) if binding and usd_ils else None,
        binding_rule=binding[1] if binding else None,
        reason=(
            f"At most {binding[0]:,.0f} ILS more fits under your limits; {binding[1]} binds."
            if binding
            else (
                "No concentration limit binds."
                if have_book
                else "The portfolio is empty, so concentration limits cannot be measured yet."
            )
        ),
    )

    # ---- price and levels
    reason_no_levels: str | None = None
    levels: ExitLevelsResult | None = None
    price_ils: float | None = None
    qty0 = 0.0
    if (
        quote is None
        or quote.price <= 0
        or quote.currency.strip().upper() not in SUPPORTED_CURRENCIES
    ):
        reason_no_levels = "No levels: there is no market price for this symbol."
    else:
        price_ils = to_ils(quote.price, quote.currency, usd_ils)
        if amount_ils is not None and price_ils > 0:
            spend = min([amount_ils] + ([binding[0]] if binding else []))
            qty0 = _floor_qty(spend / price_ils, sec.asset_type)
        probe_qty = qty0 if qty0 > 0 else (1e-6 if sec.asset_type == "crypto" else 1.0)
        base_ils = total + (amount_ils or 0.0)
        v = _valued(sec, quote, usd_ils, probe_qty, portfolio.id)
        levels = compute_exit_levels(
            v, horizon, risk, df, portfolio_value_ils=base_ils or None, now=now, settings=s
        )
        if levels.status != "levels":
            reason_no_levels = levels.reason
    ok_levels = levels is not None and levels.status == "levels"
    if horizon is None:
        reason_no_levels = (
            "How long do you plan to keep this? No levels are computed until you choose a horizon."
        )

    # ---- size, rules
    rules: list[RuleResult] = []
    for e in exposures:
        rules.append(
            RuleResult(
                rule=e.rule,
                status="not_evaluated" if not e.applies or e.breaks is None else ("fail" if e.breaks else "pass"),
                value=e.after_pct,
                limit=e.limit_pct,
                reason=e.reason,
            )
        )  # fmt: skip
    size: SizeOut | None = None
    status: FitStatus = "incomplete"
    stop_price: float | None = None
    best_rr: float | None = None
    vol: float | None = None
    if df is not None and len(df) > 12:
        periods = 365 if sec.asset_type == "crypto" or sec.market == "CRYPTO" else 252
        vol = annualised_volatility_pct(
            df["Close"].to_numpy(dtype=float), s.screener_vol_lookback_days, periods
        )
    vol_cap = s.screener_max_volatility_pct.get(risk.preset or "")
    if vol is not None and vol_cap is not None:
        rules.append(
            RuleResult(
                rule="volatility_cap",
                status="fail" if vol > vol_cap else "pass",
                value=round(vol, 1),
                limit=vol_cap,
                reason=f"Annualised volatility {vol:.0f}% against the {vol_cap:.0f}% cap of the {risk.preset} preset.",
            )
        )
    else:
        rules.append(
            RuleResult(rule="volatility_cap", status="not_evaluated", reason="Not enough price history to measure volatility.")
        )  # fmt: skip

    entry: float | None = None
    if ok_levels:
        assert levels is not None and quote is not None
        stop = levels.effective_stop
        entry = levels.price
        if stop is not None:
            stop_price = stop.price
        rrs = [t.rr for t in levels.take_profits if t.rr is not None]
        best_rr = max(rrs) if rrs else 0.0
        rules.append(
            RuleResult(
                rule="min_rr",
                status="pass" if best_rr >= risk.min_rr else "fail",
                value=round(best_rr, 2),
                limit=risk.min_rr,
                reason=f"Best reward-to-risk is {best_rr:.1f} (your minimum {risk.min_rr:g}).",
            )
        )
        if amount_ils is not None and price_ils:
            guide = levels.size_guidance
            keep = guide.keep_fraction if guide is not None else 1.0
            qty = _floor_qty(qty0 * keep, sec.asset_type)
            if qty > 0 and stop is not None and levels.price is not None:
                cost_native = qty * quote.price
                cost_ils = qty * price_ils
                limited: list[str] = []
                if binding and qty0 * price_ils < amount_ils - price_ils:
                    limited.append(f"limited by your {binding[1]} (room for {binding[0]:,.0f} ILS)")
                if guide is not None and guide.needed:
                    limited.extend(guide.rules)
                gap_ils = to_ils(max(0.0, levels.price - stop.price) * qty, quote.currency, usd_ils)
                after_total = total + cost_ils
                base_ils = total + amount_ils

                def pct(x: float, after_total: float = after_total) -> float | None:
                    return round(x / after_total * 100.0, 2) if total > 0 else None

                size = SizeOut(
                    quantity=qty,
                    cost_native=round(cost_native, 4),
                    currency=quote.currency,
                    cost_ils=round(cost_ils, 2),
                    cost_usd=round(cost_ils / usd_ils if usd_ils else 0.0, 2),
                    pct_of_amount=round(cost_ils / amount_ils * 100.0, 2) if amount_ils else 0.0,
                    position_pct_after=pct(existing + cost_ils),
                    sector_pct_after=pct(book.sector.get(sec.sector, 0.0) + cost_ils),
                    country_pct_after=pct(book.country.get(sec.country, 0.0) + cost_ils),
                    risk_ils=round(gap_ils, 2),
                    risk_pct_of_portfolio=round(gap_ils / base_ils * 100.0, 4) if base_ils else 0.0,
                    limited_by=[_cut(x) for x in limited][:MAX_ITEMS],
                )
                rules.append(
                    RuleResult(
                        rule="max_portfolio_risk_per_trade_pct",
                        status="pass"
                        if size.risk_pct_of_portfolio
                        <= risk.max_portfolio_risk_per_trade_pct + 1e-9
                        else "fail",
                        value=size.risk_pct_of_portfolio,
                        limit=risk.max_portfolio_risk_per_trade_pct,
                        reason=(
                            f"Losing {gap_ils:,.0f} ILS at the stop is {size.risk_pct_of_portfolio:.2f}% "
                            f"of the portfolio (limit {risk.max_portfolio_risk_per_trade_pct:g}% per trade)."
                        ),
                    )
                )
            elif qty <= 0:
                rules.append(
                    RuleResult(
                        rule="size",
                        status="fail",
                        reason=(
                            f"Not even one unit fits: {max_size.reason}"
                            if qty0 <= 0 and binding
                            else "Within your risk limits not even one unit fits at this stop distance."
                            if qty0 > 0
                            else f"Your amount does not cover one unit ({price_ils:,.2f} ILS)."
                        ),
                    )
                )
        failed = [r for r in rules if r.status == "fail" and r.rule not in set(broken)]
        if needs:
            status = "incomplete"
        elif size is None or failed:
            status = "does_not_fit"
        elif amount_ils is not None and (size.cost_ils < amount_ils - (price_ils or 0.0) or broken):
            status = "fits_smaller"
        else:
            status = "fits"
        if status == "does_not_fit":
            size = None  # no size is suggested for something that fails a rule
    else:
        status = "incomplete"
        rules.append(
            RuleResult(
                rule="min_rr", status="not_evaluated", reason=reason_no_levels or "No levels."
            )
        )

    summary = _summary(
        sec, status, needs, mode, size, broken, max_size, reason_no_levels, rules, amount, currency
    )
    sources: list[ExplanationSource] = []
    if quote is not None:
        sources.append(
            ExplanationSource(
                name="Quote",
                as_of=as_utc(quote.as_of),
                detail=f"{quote.source} ({quote.basis}); the entry price.",
            )
        )
    sources.append(
        ExplanationSource(
            name="Your portfolio valuation",
            as_of=as_utc(val.as_of) if val.as_of else None,
            detail="Cached quotes and the FX rate of the valuation.",
        )
    )
    explanation = Explanation(
        summary=_cut(summary, 1900),
        inputs=as_float_inputs(
            {
                "portfolio_value_ils": total,
                "amount_ils": amount_ils,
                "existing_ils": existing,
                "entry": entry,
                "stop": stop_price,
                "best_rr": best_rr,
                "volatility_pct": vol,
            }
        ),
        rules_applied=[
            "Caps are checked before and after the amount; an amount over a cap is shrunk to fit.",
            "Levels come from the exit-levels engine on a fresh market price only.",
            "The stop is never tightened to fit the filter: the size shrinks instead.",
        ],
        as_of=as_utc(quote.as_of) if quote is not None else None,
        annotations=list(levels.explanation.annotations) if ok_levels and levels else [],
        risk_rules_applied=[
            _cut(x)
            for x in [
                f"preset {risk.preset}: position {risk.max_position_pct:g}%, sector {risk.max_sector_pct:g}%, "
                f"country {risk.max_country_pct:g}%",
                f"risk per trade {risk.max_portfolio_risk_per_trade_pct:g}% of the portfolio, min reward-to-risk {risk.min_rr:g}",
                *(size.limited_by if size else []),
            ]
        ][:MAX_ITEMS],
        invalidation_risks=[
            "Scores are not backtested yet and can be wrong; a gap can pass the stop.",
            "Prices and your portfolio change; recompute before acting.",
        ],
        sources=sources,
    )
    return PortfolioFit(
        status=status,
        needs_input=needs,
        summary=_cut(summary, 1900),
        portfolio_id=portfolio.id,
        mode=mode,  # type: ignore[arg-type]
        held=held_out,
        risk_preset=risk.preset,
        risk_source=risk_source,
        horizon=horizon,
        horizon_source=horizon_source,  # type: ignore[arg-type]
        amount=amount,
        currency=currency,
        amount_ils=None if amount_ils is None else round(amount_ils, 2),
        portfolio_value_ils=round(total, 2),
        max_position_size=max_size,
        exposures=exposures,
        caps_broken_at_requested_amount=broken,
        rules=rules,
        rules_not_checked=list(NOT_CHECKED),
        suggested_size=size,
        entry=entry,
        levels=levels,
        levels_unavailable_reason=reason_no_levels,
        explanation=explanation,
    )


def _summary(
    sec: Security,
    status: FitStatus,
    needs: list[NeedsInput],
    mode: str,
    size: SizeOut | None,
    broken: list[str],
    max_size: MaxPositionSize,
    no_levels: str | None,
    rules: list[RuleResult],
    amount: float | None,
    currency: str | None,
) -> str:
    kind = (
        "an increase of your existing position" if mode == "increase_existing" else "a new position"
    )
    if status == "fits" and size is not None:
        return (
            f"{sec.symbol} as {kind}: the requested amount fits your limits. "
            f"{size.quantity:g} unit(s), about {size.cost_ils:,.0f} ILS, losing {size.risk_ils:,.0f} ILS if the stop is hit."
        )
    if status == "fits_smaller" and size is not None:
        why = f" Over a cap: {', '.join(broken)}." if broken else ""
        return (
            f"{sec.symbol} as {kind}: only a smaller size fits your limits.{why} "
            f"{size.quantity:g} unit(s), about {size.cost_ils:,.0f} ILS, losing {size.risk_ils:,.0f} ILS if the stop is hit."
        )
    if status == "does_not_fit":
        fails = [r.reason for r in rules if r.status == "fail"]
        return f"{sec.symbol} as {kind} does not fit your limits. " + " ".join(fails[:2])
    parts = []
    if needs:
        parts.append("Needed to finish the fit: " + ", ".join(needs) + ".")
    if no_levels and "horizon" not in no_levels:
        parts.append(no_levels)
    parts.append(max_size.reason)
    return f"{sec.symbol} as {kind}: " + " ".join(parts)

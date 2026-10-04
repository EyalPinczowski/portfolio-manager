"""Analyze a stock: GET /api/analyze/{symbol} and POST /api/analyze/{symbol}/ask.

Works for any US / TASE / crypto ticker, owned or not. Login is required and a `portfolio_id` that
is not the caller's is a 404 (checked before anything is fetched). The answer is the Scout and
Chartist reports (deterministic code), a portfolio-fit section (the headline for the user's own
portfolio) and neutral candidate facts. There is no buy/sell verdict here: while the launch gate is
closed none may exist, and once it opens a verdict can only come from `LaunchGate.release()` on a
gated route (not built yet). The summary is a template; no AI provider is called, and the user's
question and notes are never sent to one.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.analyze.fit import compute_fit, incomplete_fit
from app.analyze.public_facts import PublicFacts, public_facts, summarize, template_summary
from app.analyze.schemas import (
    AnalyzeOut,
    AskIn,
    AskOut,
    CandidateInfo,
    NeedsInput,
    PriceInfo,
)
from app.analyze.scout import build_scout_report
from app.analyze.service import MarketData, load_market, quote_row
from app.api.exit_levels import _risk_for
from app.api.schemas import BIG, Horizon
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.auth.ratelimit import committee_limiter, enforce_limit
from app.committee.roles import run_committee
from app.committee.schemas import CommitteeReport
from app.config import DISCLAIMER
from app.errors import ApiError
from app.importer.parse import SYMBOL_PATTERN, norm_symbol
from app.launchgate import GateDep
from app.llm.base import LLMProvider
from app.llm.providers import build_providers
from app.models import Security
from app.portfolio.freshness import price_is_fresh
from app.portfolio.valuation import value_portfolio
from app.providers.registry import get_providers
from app.repo import get_portfolio
from app.scoring.risk import PresetName
from app.scoring.screener import _valued
from app.securities import infer_security
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc, utcnow
from app.userlists import record_search
from app.verdict_words import verdict_words_in_text

router = APIRouter(tags=["analyze"], route_class=StrictJsonRoute)

AmountQuery = Annotated[float, Query(gt=0, le=BIG, allow_inf_nan=False)]


def _symbol(raw: str) -> str:
    sym = str(norm_symbol(raw))
    if not re.fullmatch(SYMBOL_PATTERN, sym):
        raise ApiError(422, "bad_symbol", "That is not a valid ticker symbol.")
    if sym.startswith("^") or "=" in sym:
        raise ApiError(422, "not_a_security", "Indexes and exchange rates cannot be analyzed.")
    return sym


def _security(db: DbDep, sym: str) -> tuple[Security, Security | None]:
    known = db.get(Security, sym)
    return (known or infer_security(sym)), known


def _market(db: DbDep, sym: str, settings: SettingsDep) -> tuple[Security, MarketData]:
    sec, known = _security(db, sym)
    md = load_market(db, sym, known, get_providers(), settings)
    if known is None and not md.has_any_data:
        raise ApiError(404, "symbol_not_found", f"No data provider knows the symbol {sym}.")
    return sec, md


@router.get("/analyze/{symbol}", response_model=AnalyzeOut)
def analyze(
    symbol: str,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    gate: GateDep,
    portfolio_id: int | None = None,
    amount: AmountQuery | None = None,
    currency: Literal["ILS", "USD"] | None = None,
    horizon: Horizon | None = None,
    risk: PresetName | None = None,
) -> AnalyzeOut:
    """Scout + Chartist reports and, with a portfolio, how this stock fits it. `amount` (with
    `currency`) and `horizon` have no defaults: missing ones come back in `needs_input`."""
    assert user.id is not None
    sym = _symbol(symbol)
    portfolio = get_portfolio(db, user.id, portfolio_id) if portfolio_id is not None else None
    sec, md = _market(db, sym, settings)
    now = utcnow()
    qrow = quote_row(md.quote)
    price: PriceInfo | None = None
    price_reason: str | None = None
    if qrow is not None and qrow.price > 0:
        fresh = price_is_fresh(_valued(sec, qrow, 1.0, 1.0, 0), now, settings)
        price = PriceInfo(
            price=qrow.price,
            currency=qrow.currency,
            as_of=as_utc(qrow.as_of),
            source=qrow.source,
            basis=qrow.basis,  # type: ignore[arg-type]
            flag=qrow.flag,
            change_pct=qrow.change_pct,
            is_fresh=fresh,
        )
    else:
        price_reason = "No provider returned a price for this symbol."
    scout = build_scout_report(
        sec,
        verified=True,
        price=price,
        price_reason=price_reason,
        chart=md.chart,
        history_as_of=md.history_as_of,
    )

    if portfolio is None:
        needs: list[NeedsInput] = ["portfolio_id"]
        fit = incomplete_fit(
            needs,
            None,
            "Choose a portfolio to see how this stock fits it and what size is allowed.",
        )
    else:
        assert portfolio.id is not None
        val = value_portfolio(db, portfolio, settings)
        held_row = next((v.holding for v in val.holdings if v.security.symbol == sym), None)
        rf = _risk_for(portfolio, held_row, risk, settings)
        risk_source = (
            "request"
            if risk is not None
            else "holding_override"
            if held_row is not None and held_row.risk_override
            else "portfolio"
        )
        hz = horizon or (held_row.horizon if held_row is not None else None)
        fit = compute_fit(
            portfolio,
            val,
            sec,
            qrow,
            md.df,
            rf,
            risk_source=risk_source,
            amount=amount,
            currency=currency,
            horizon=hz,
            horizon_source="request" if horizon else "holding" if hz else None,
            settings=settings,
            now=now,
        )
        needs = list(fit.needs_input)

    # Only the symbol is remembered (search history); nothing about the result is stored.
    record_search(db, user.id, sym, settings.max_search_history_per_user)
    gate_status = gate.evaluate()
    facts = public_facts(scout, md.chart)
    text, llm_used = summarize(facts, None)
    return AnalyzeOut(
        symbol=sym,
        generated_at=now,
        cached=md.cached,
        scout=scout,
        chart=md.chart,
        portfolio_fit=fit,
        candidate_info=CandidateInfo(
            score=md.chart.score,
            confidence=md.chart.confidence,
            score_available=md.chart.available,
            fit_passes=None
            if fit.status == "incomplete"
            else fit.status in ("fits", "fits_smaller"),
            launch_gate_open=gate_status.open,
            launch_gate_reasons=gate_status.reasons,
        ),
        summary=text,
        llm_used=llm_used,
        needs_input=needs,
    )


_TOPICS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("rsi", "momentum"), ("rsi14",), "RSI(14) is {rsi14:.1f}."),
    (("macd",), ("macd_hist",), "The MACD histogram is {macd_hist:+.3f}."),
    (
        ("trend", "average", "sma", "moving"),
        ("close", "sma50", "sma200"),
        "The last close is {close:.2f}; the 50-day average is {sma50:.2f} and the 200-day average is {sma200:.2f}.",
    ),
    (
        ("volatil", "atr", "swing", "risky"),
        ("atr14", "atr14_pct_of_price"),
        "The 14-day ATR is {atr14:.2f}, {atr14_pct_of_price:.1f}% of the price.",
    ),
)


def _answer(question: str, facts: PublicFacts) -> tuple[str, list[str], bool]:
    """A template answer quoting computed numbers. Returns (text, fields used, declined)."""
    q = question.lower()
    asked_for_instruction = bool(verdict_words_in_text(question)) or bool(
        re.search(r"\bshould i\b|\bworth it\b|\bgo (long|short)\b", q)
    )
    parts: list[str] = []
    used: list[str] = []
    for words, keys, template in _TOPICS:
        if any(w in q for w in words) and all(k in facts.indicators for k in keys):
            parts.append(template.format(**{k: facts.indicators[k] for k in keys}))
            used.extend(keys)
    if any(w in q for w in ("support", "resist", "level")) and facts.levels:
        parts.append("Nearby levels: " + ", ".join(facts.levels) + ".")
        used.append("levels")
    if asked_for_instruction:
        head = (
            "This tool does not give instructions to trade while its scores are unvalidated. "
            "Here is what the computed data shows. "
        )
        return head + (" ".join(parts) or template_summary(facts)), used or ["summary"], True
    if not parts:
        return template_summary(facts), ["summary"], False
    return " ".join(parts), used, False


@router.post("/analyze/{symbol}/ask", response_model=AskOut)
def ask(symbol: str, body: AskIn, user: UserDep, db: DbDep, settings: SettingsDep) -> AskOut:
    """Answer a question about a stock from the computed data with a template. The question and
    notes are not stored and are never sent to an AI provider (template-only for now)."""
    assert user.id is not None
    sym = _symbol(symbol)
    sec, md = _market(db, sym, settings)
    scout = build_scout_report(
        sec,
        verified=True,
        price=None,
        price_reason=None,
        chart=md.chart,
        history_as_of=md.history_as_of,
    )
    text, used, declined = _answer(body.question, public_facts(scout, md.chart))
    return AskOut(symbol=sym, answer=text, grounded_in=used, question_declined=declined)


def committee_providers(settings: SettingsDep) -> list[LLMProvider]:
    """The free providers in the configured order (they see `PublicFacts` and public passages only).
    Empty without keys: every role then uses its template. Override in tests."""
    return build_providers(settings)


class CommitteeOut(BaseModel):
    symbol: str
    generated_at: datetime
    cached: bool  # the chart data came from the analyze cache
    report: CommitteeReport
    llm_used: bool  # at least one role was answered by a model (or the response cache)
    launch_gate_open: bool
    launch_gate_reasons: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


@router.post("/analyze/{symbol}/committee", response_model=CommitteeOut)
def committee(
    symbol: str,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
    gate: GateDep,
    providers: list[LLMProvider] = Depends(committee_providers),
) -> CommitteeOut:
    """Run the Investment Committee (company profile, news, Bear, CIO) for any ticker.

    Public data only: the roles see `PublicFacts` and retrieved public passages, never the user's
    portfolio. There is no buy/sell verdict here: the CIO answers the Bear's risks and may nudge the
    chart score within the configured cap, nothing more. Every role has a template fallback, so
    this works with no AI key. Chart data is cached like the rest of Analyze, and model answers are
    cached by the LLM layer."""
    assert user.id is not None
    sym = _symbol(symbol)
    enforce_limit(
        committee_limiter, f"user:{user.id}", settings.committee_rate_limit_per_hour, 3600.0
    )
    sec, md = _market(db, sym, settings)
    scout = build_scout_report(
        sec,
        verified=True,
        price=None,
        price_reason=None,
        chart=md.chart,
        history_as_of=md.history_as_of,
    )
    facts = public_facts(scout, md.chart)
    now = utcnow()
    report = run_committee(db, facts, providers=providers, settings=settings)
    status = gate.evaluate()
    return CommitteeOut(
        symbol=sym,
        generated_at=now,
        cached=md.cached,
        report=report,
        llm_used=any(
            r.source != "template" for r in (report.profile, report.news, report.bear, report.cio)
        ),
        launch_gate_open=status.open,
        launch_gate_reasons=status.reasons,
    )

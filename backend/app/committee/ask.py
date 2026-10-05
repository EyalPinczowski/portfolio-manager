"""Ask-my-portfolio: read-only, user-scoped tools; grounded, cited answers.

- Tools (`PortfolioTools`) are bound to ONE user at construction. Every tool reads through
  `repo.list_portfolios` / `repo.get_portfolio` with that user id, so another user's portfolio is
  unreachable (a foreign `portfolio_id` is "not found"). No tool writes, trades or edits settings;
  the registry is a fixed allowlist and unknown tool names are refused.
- The default answer is a template built from the tool results, each sentence citing its tool as
  `[tool:<name>]`. A question that asks for a trade or a settings change is declined.
- An LLM may phrase the answer ONLY through a provider that declares `privacy == "no_training"` (a
  paid or no-training provider the user enabled; none exists by default). A free provider is never
  passed holdings, amounts or the question: `ask` refuses it and uses the template. The prompt is
  capped at the `ask_portfolio` role budget, the answer must cite only tools that were called, quote
  only numbers present in the tool results and contain no verdict words, else the template is used.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.committee.roles import numbers_grounded
from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.structured import structured_call
from app.llm.untrusted import UNTRUSTED_RULE, UntrustedText
from app.models import Portfolio
from app.providers.base import HistoryProvider
from app.rag.tokens import estimate_tokens
from app.repo import get_portfolio, list_portfolios
from app.verdict_words import verdict_words_in_text

TOOL_NAMES = (
    "get_summary",
    "get_holdings",
    "get_xray",
    "get_scorecard",
    "get_performance",
    "get_analysis",
    "get_exit_levels",
)
_SYMBOL_OK = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,23}")
DECLINE = (
    "I can only read your portfolio data; I do not place trades, change settings or tell you what "
    "to trade. Here is what the data shows."
)
_ACTION = re.compile(
    r"\b(?:place|execute|submit|cancel|set|change|update|delete|remove|disable|enable|"
    r"increase|raise|lower)\b.*\b(?:order|trade|stop|limit|setting|settings|risk|alert|filter)\b"
    r"|\b(?:buy|sell|trade)\s+\w+",
    re.IGNORECASE,
)
_SYMBOL = re.compile(r"\b[A-Z][A-Z0-9.\-]{1,9}\b")
_ROUTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("down", "up this", "week", "month", "profit", "loss", "return", "p&l", "pnl", "perform"),
        "get_performance",
    ),
    (("risk", "concentrat", "exposure", "sector", "country", "currency", "diversif"), "get_xray"),
    (("holding", "position", "own", "biggest", "largest", "stock"), "get_holdings"),
    (("score", "worry", "chart", "signal"), "get_scorecard"),
    (("value", "worth", "total", "summary", "how am i"), "get_summary"),
)


class ToolError(Exception):
    """A tool call was refused (unknown tool, bad argument, or not the caller's portfolio)."""


class ToolResult(BaseModel):
    tool: str
    data: dict[str, Any]


class AskAnswer(BaseModel):
    """What a no-training model returns: text plus the tools it relied on."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    cites: list[str] = Field(default_factory=list)


class NeedsHorizon(BaseModel):
    """A holding the answer needed exit levels for but that has no horizon set (the UI links to it)."""

    symbol: str
    holding_id: int
    portfolio_id: int


class AskResult(BaseModel):
    answer: str
    cites: list[str]
    tools_called: list[str]
    source: Literal["template", "llm", "cache"]
    declined: bool = False
    prompt_tokens: int = 0
    budget: int = 0
    notes: list[str] = Field(default_factory=list)
    needs_horizon: list[NeedsHorizon] = Field(default_factory=list)


class PortfolioTools:
    """Read-only tools for one user. Construct with the authenticated user's id only."""

    def __init__(
        self,
        db: Session,
        user_id: int,
        history: HistoryProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._uid = user_id
        self._history = history
        self._s = settings or get_settings()
        self._tools: dict[str, Callable[..., dict[str, Any]]] = {
            "get_summary": self.get_summary,
            "get_holdings": self.get_holdings,
            "get_xray": self.get_xray,
            "get_scorecard": self.get_scorecard,
            "get_performance": self.get_performance,
            "get_analysis": self.get_analysis,
            "get_exit_levels": self.get_exit_levels,
        }

    def call(self, name: str, **args: Any) -> ToolResult:
        fn = self._tools.get(name)
        if fn is None:
            raise ToolError(f"unknown tool {name!r}")
        try:
            return ToolResult(tool=name, data=fn(**args))
        except HTTPException as exc:  # a foreign or missing portfolio looks the same
            raise ToolError("portfolio not found") from exc
        except TypeError as exc:
            raise ToolError(f"bad arguments for {name}") from exc

    def _portfolios(self, portfolio_id: int | None) -> list[Portfolio]:
        if portfolio_id is None:
            return list_portfolios(self._db, self._uid)
        return [get_portfolio(self._db, self._uid, portfolio_id)]

    def get_summary(self, portfolio_id: int | None = None) -> dict[str, Any]:
        from app.portfolio.summary import build_summary

        ps = self._portfolios(portfolio_id)
        if not ps:
            return {"empty": True, "note": "no portfolio yet"}
        full = build_summary(self._db, ps, self._history, self._s)
        keep = ("value", "day_pnl", "week_pnl", "month_pnl", "since_start_pnl", "as_of")
        out: dict[str, Any] = json.loads(json.dumps({k: full[k] for k in keep}, default=str))
        return out

    def get_performance(self, portfolio_id: int | None = None) -> dict[str, Any]:
        s = self.get_summary(portfolio_id)
        return {k: s[k] for k in ("day_pnl", "week_pnl", "month_pnl", "since_start_pnl") if k in s}

    def get_holdings(self, portfolio_id: int | None = None) -> dict[str, Any]:
        from app.api.portfolios import holding_outs

        rows: list[dict[str, Any]] = []
        for p in self._portfolios(portfolio_id):
            outs, _ = holding_outs(self._db, p, self._s)
            rows += [
                {
                    "symbol": o.symbol,
                    "name": o.name_en,
                    "weight_pct": o.weight_pct,
                    "value_ils": o.value_ils,
                    "day_change_pct": o.day_change_pct,
                    "pnl_pct": o.pnl.pct if o.pnl else None,
                }
                for o in outs
            ]
        rows.sort(key=lambda r: -float(r["value_ils"]))
        return {"holdings": rows[: self._s.ask_max_holdings_rows], "count": len(rows)}

    def get_xray(self, portfolio_id: int | None = None) -> dict[str, Any]:
        from app.portfolio.xray import build_xray

        ps = self._portfolios(portfolio_id)
        if not ps:
            return {"empty": True}
        x = build_xray(self._db, ps[0], self._s)
        xr: dict[str, Any] = json.loads(
            json.dumps(
                {"concentration": x["concentration"], "breaches": x["breaches"],
                 "sector_exposure": x["sector_exposure"][:5],
                 "country_exposure": x["country_exposure"][:5]},
                default=str,
            )
        )  # fmt: skip
        return xr

    @staticmethod
    def _symbol(symbol: str) -> str:
        sym = symbol.strip().upper()
        if not _SYMBOL_OK.fullmatch(sym):
            raise ToolError("bad symbol")
        return sym

    def _held_or_watched(self, sym: str, portfolio_id: int | None = None) -> bool:
        """True when the symbol is a holding (of this user) or on this user's watchlist."""
        from app.models import Holding
        from app.userlists import watchlist_item

        for p in self._portfolios(portfolio_id):
            if self._db.exec(
                select(Holding.id).where(Holding.portfolio_id == p.id, Holding.symbol == sym)
            ).first():
                return True
        return watchlist_item(self._db, self._uid, sym) is not None

    def get_analysis(self, symbol: str) -> dict[str, Any]:
        """The existing chart analysis (score, confidence, reasons) for a symbol the user holds or
        watches. Public market data only; the same template summary the Analyze page shows."""
        from app.analyze.public_facts import public_facts, template_summary
        from app.analyze.scout import build_scout_report
        from app.analyze.service import load_market
        from app.models import Security
        from app.providers.registry import get_providers
        from app.securities import infer_security

        sym = self._symbol(symbol)
        if not self._held_or_watched(sym):
            return {"symbol": sym, "available": False, "reason": "not held or watched"}
        known = self._db.get(Security, sym)
        sec = known or infer_security(sym)
        md = load_market(self._db, sym, known, get_providers(), self._s)
        scout = build_scout_report(
            sec, verified=True, price=None, price_reason=None, chart=md.chart,
            history_as_of=md.history_as_of,
        )  # fmt: skip
        facts = public_facts(scout, md.chart)
        if facts.score is None:
            return {
                "symbol": sym,
                "available": False,
                "reason": "no chart signal has enough data yet",
            }
        out: dict[str, Any] = {
            "symbol": sym,
            "available": True,
            "score": round(facts.score, 1),
            "confidence": round(facts.confidence, 2),
            "reasons": facts.reasons[:5],
            "summary": template_summary(facts),
            "as_of": md.chart.data_as_of.isoformat() if md.chart.data_as_of else None,
        }
        return json.loads(json.dumps(out, default=str))  # type: ignore[no-any-return]

    def get_exit_levels(self, symbol: str, portfolio_id: int | None = None) -> dict[str, Any]:
        """Stop and take-profit levels for a holding, from `scoring/exit_levels.py`. With no
        horizon set on the holding the answer is `needs_horizon`: a horizon is never assumed."""
        from app.api.exit_levels import _history, _risk_for
        from app.portfolio.valuation import value_portfolio
        from app.scoring.exit_levels import compute_exit_levels

        sym = self._symbol(symbol)
        for p in self._portfolios(portfolio_id):
            val = value_portfolio(self._db, p, self._s)
            v = next((x for x in val.holdings if x.holding.symbol == sym), None)
            if v is None:
                continue
            h = v.holding
            if not h.horizon:
                return {
                    "symbol": sym,
                    "holding_id": h.id,
                    "portfolio_id": p.id,
                    "available": False,
                    "status": "needs_horizon",
                    "note": "No horizon is set for this holding, so no exit levels are shown. "
                    "Set a horizon on the holding first.",
                }
            res = compute_exit_levels(
                v,
                h.horizon,
                _risk_for(p, h, None, self._s),
                _history(v, self._s),
                portfolio_value_ils=val.total_ils,
                settings=self._s,
            )
            eff = res.effective_stop
            out: dict[str, Any] = {
                "symbol": sym,
                "available": res.status == "levels",
                "status": res.status,
                "horizon": res.horizon,
                "currency": res.currency,
                "price": res.price,
                "stop_price": eff.price if eff else None,
                "stop_distance_pct": eff.distance_pct if eff else None,
                "take_profit_prices": [t.price for t in res.take_profits],
                "reason": res.reason,
            }
            return json.loads(json.dumps(out, default=str))  # type: ignore[no-any-return]
        return {"symbol": sym, "available": False, "status": "not_held", "note": "not a holding"}

    def get_scorecard(self, symbol: str) -> dict[str, Any]:
        from app.scoring.scorecard import get_cached_scorecard

        sym = self._symbol(symbol)
        card = get_cached_scorecard(self._db, sym)  # public market data, not user data
        if card is None:
            return {"symbol": sym, "available": False}
        data: dict[str, Any] = json.loads(json.dumps(card, default=str))
        return {"symbol": sym, "available": True, **data}


_NOT_SYMBOLS = {"I", "ILS", "USD", "ETF", "AI", "US", "TASE", "TA"}
_EXIT_WORDS = ("stop", "exit level", "exit", "take profit", "take-profit", "horizon")
_ANALYSIS_WORDS = ("analy", "explain")


def _plan(question: str, portfolio_id: int | None = None) -> list[tuple[str, dict[str, Any]]]:
    q = question.lower()
    scope: dict[str, Any] = {} if portfolio_id is None else {"portfolio_id": portfolio_id}
    plan: list[tuple[str, dict[str, Any]]] = []
    for words, tool in _ROUTES:
        if any(w in q for w in words) and tool != "get_scorecard":
            plan.append((tool, dict(scope)))
    symbols = [sym for sym in _SYMBOL.findall(question) if sym not in _NOT_SYMBOLS]
    for sym in symbols:
        if any(w in q for w in ("worry", "score", "chart", "about")):
            plan.append(("get_scorecard", {"symbol": sym}))
        if any(w in q for w in _ANALYSIS_WORDS):
            plan.append(("get_analysis", {"symbol": sym}))
        if any(w in q for w in _EXIT_WORDS):
            plan.append(("get_exit_levels", {"symbol": sym, **scope}))
    if not plan:
        plan.append(("get_summary", dict(scope)))
    seen: set[str] = set()
    out: list[tuple[str, dict[str, Any]]] = []
    for t, a in plan:
        key = t + json.dumps(a, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append((t, a))
    return out


def _fmt_pnl(label: str, p: dict[str, Any]) -> str:
    return f"{label} {p['pct']:+.2f}% ({p['ils']:+,.0f} ILS)"


def _template(results: list[ToolResult]) -> tuple[str, list[str]]:
    parts: list[str] = []
    cites: list[str] = []
    for r in results:
        d, tag = r.data, f"[tool:{r.tool}]"
        if d.get("empty"):
            parts.append(f"There is no portfolio data yet. {tag}")
        elif r.tool == "get_summary":
            parts.append(
                f"Total value {d['value']['ils']:,.0f} ILS; "
                + _fmt_pnl("today", d["day_pnl"])
                + "; "
                + _fmt_pnl("this week", d["week_pnl"])
                + f". {tag}"
            )
        elif r.tool == "get_performance":
            parts.append(
                "; ".join(
                    _fmt_pnl(lbl, d[k])
                    for lbl, k in (
                        ("this week", "week_pnl"),
                        ("this month", "month_pnl"),
                        ("since start", "since_start_pnl"),
                    )
                    if k in d
                )
                + f". {tag}"
            )
        elif r.tool == "get_holdings":
            top = d["holdings"][:3]
            parts.append(
                (
                    "Largest holdings: "
                    + ", ".join(f"{h['symbol']} {h['weight_pct']:.1f}%" for h in top)
                    + f" (of {d['count']}). {tag}"
                )
                if top
                else f"No holdings are recorded. {tag}"
            )
        elif r.tool == "get_xray":
            b = d.get("breaches", [])
            parts.append(
                (f"{len(b)} risk-limit breach(es) found. " if b else "No risk-limit breaches. ")
                + f"{tag}"
            )
        elif r.tool == "get_scorecard":
            if d.get("available"):
                parts.append(
                    f"{d['symbol']} chart score {float(d.get('total', 0)):+.0f} "
                    f"(confidence {float(d.get('confidence', 0)):.0%}). {tag}"
                )
            else:
                parts.append(f"No score is available for {d['symbol']} yet. {tag}")
        elif r.tool == "get_analysis":
            if d.get("available"):
                parts.append(
                    f"{d['symbol']} analysis: score {float(d['score']):+.0f} "
                    f"(confidence {float(d['confidence']):.0%}). {' '.join(d.get('reasons', [])[:2])} "
                    f"{tag}".replace("  ", " ")
                )
            else:
                parts.append(
                    f"No analysis is available for {d['symbol']} ({d.get('reason')}). {tag}"
                )
        elif r.tool == "get_exit_levels":
            if d.get("status") == "needs_horizon":
                parts.append(
                    f"{d['symbol']} has no horizon set, so no exit levels are shown. {tag}"
                )
            elif d.get("available") and d.get("stop_price") is not None:
                tps = ", ".join(f"{p:g}" for p in d.get("take_profit_prices", [])[:3])
                parts.append(
                    f"{d['symbol']} ({d.get('horizon')}): stop {d['stop_price']:g} "
                    f"({d['stop_distance_pct']:+.1f}%)"
                    + (f", take-profit {tps}" if tps else "")
                    + f". {tag}"
                )
            else:
                parts.append(f"No exit levels are available for {d['symbol']}. {tag}")
        cites.append(f"tool:{r.tool}")
    return " ".join(parts), cites


def _fit_results(
    results: list[ToolResult], head: str, budget: int, s: Settings
) -> list[ToolResult] | None:
    """Shrink the tool outputs (halve lists) until head + data fits the budget; None if it cannot."""
    cur = results
    for _ in range(8):
        body = json.dumps([r.model_dump() for r in cur], default=str)
        if estimate_tokens(head + body, s) <= budget:
            return cur
        shrunk = False
        nxt: list[ToolResult] = []
        for r in cur:
            data = dict(r.data)
            for k, v in data.items():
                if isinstance(v, list) and len(v) > 1:
                    data[k] = v[: len(v) // 2]
                    shrunk = True
            nxt.append(ToolResult(tool=r.tool, data=data))
        if not shrunk:
            return None
        cur = nxt
    return None


def ask(
    db: Session,
    user_id: int,
    question: str,
    *,
    history: HistoryProvider | None = None,
    providers: list[LLMProvider] | None = None,
    settings: Settings | None = None,
    portfolio_id: int | None = None,
) -> AskResult:
    """Answer one question. `providers` is used only if every one declares `privacy == "no_training"`."""
    s = settings or get_settings()
    budget = s.rag_role_budgets["ask_portfolio"]
    tools = PortfolioTools(db, user_id, history, s)
    notes: list[str] = []
    declined = bool(_ACTION.search(question)) or bool(verdict_words_in_text(question))
    results: list[ToolResult] = []
    for name, args in _plan(question, portfolio_id)[: s.ask_max_tools_per_question]:
        try:
            results.append(tools.call(name, **args))
        except ToolError as exc:
            notes.append(f"{name}: {exc}")
    text, cites = _template(results)
    if not results:
        text, cites = "I could not read any portfolio data for that question.", []
    if declined:
        text = f"{DECLINE} {text}"
    called = [r.tool for r in results]
    needs = [
        NeedsHorizon(
            symbol=r.data["symbol"],
            holding_id=r.data["holding_id"],
            portfolio_id=r.data["portfolio_id"],
        )
        for r in results
        if r.tool == "get_exit_levels" and r.data.get("status") == "needs_horizon"
    ]
    base = AskResult(
        answer=text, cites=cites, tools_called=called, source="template", declined=declined,
        budget=budget, notes=notes, needs_horizon=needs,
    )  # fmt: skip

    private = [p for p in (providers or []) if getattr(p, "privacy", None) == "no_training"]
    if providers and len(private) != len(providers):
        base.notes.append("a provider that may train on prompts was refused: template used")
        return base
    if not private or not results or declined:
        return base

    head = (
        "Answer the question using only the tool results. Cite each tool you use as [tool:<name>]. "
        "Do not invent numbers. Do not tell the user to trade.\n" + UNTRUSTED_RULE + "\nResults:\n"
    )
    fitted = _fit_results(results, head, budget - estimate_tokens(question, s) - 20, s)
    if fitted is None:
        base.notes.append("tool results do not fit the role budget: template used")
        return base
    prompt = head + json.dumps([r.model_dump() for r in fitted], default=str)
    base.prompt_tokens = estimate_tokens(prompt, s) + estimate_tokens(question, s)
    out = structured_call(
        role="ask_portfolio",
        model_cls=AskAnswer,
        system="You answer questions about the user's own portfolio from tool results. JSON only.",
        prompt=prompt,
        untrusted=[UntrustedText(label="user question", text=question, source="user")],
        template=lambda: AskAnswer(text=text, cites=cites),
        cache_scope=f"user:{user_id}",
        user_id=user_id,
        priority="on_demand",
        providers=private,
        settings=s,
    )
    if out.source == "template":
        base.notes.extend(out.notes)
        return base
    ans = out.value
    allowed = {f"tool:{t}" for t in called}
    if (
        not ans.cites
        or not set(ans.cites) <= allowed
        or verdict_words_in_text(ans.text)
        or not numbers_grounded([ans.text], prompt + question)
    ):
        base.notes.append("answer failed the grounding check; template used")
        return base
    return base.model_copy(update={"answer": ans.text, "cites": ans.cites, "source": out.source})

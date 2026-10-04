"""Walk-forward replay: one window, one risk preset, an empty portfolio and fixed capital.

Day by day:
1. Orders decided on the previous close are filled at today's open (plus slippage).
2. Stops and take-profits are checked on today's bar. A stop fills at the stop, or at the open when
   the bar gaps through it (the worse price). If a bar touches both, the stop is assumed first.
3. The portfolio is marked at the close (in ILS; USD names at the day's USD/ILS).
4. Trailing stops are re-computed by `exit_levels` on as-of bars and only ever move up.
5. On a rebalance day the screener rules (`screen.evaluate_asof`) run on as-of bars and the picks
   become orders for tomorrow's open.

Look-ahead: decisions use `HistoryStore.asof` only. The executor reads a bar by its exact date,
which is today's data, never tomorrow's, except to fill an order at the NEXT day's open, which is
when that order could really trade.

Costs: commission and slippage (config, per side) on every fill. Positions still open on the last
day are sold at that close and counted as trades (reason "window_end").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from app.backtest.data import HistoryStore, as_timestamp
from app.backtest.screen import Book, BtCandidate, ScoreCache, evaluate_asof
from app.config import Settings, get_settings
from app.models import PriceQuote, Security
from app.providers.fx_provider import to_ils
from app.scoring.exit_levels import StopState, compute_exit_levels
from app.scoring.risk import RiskFilter, resolve_risk_filter
from app.scoring.screener import _valued
from app.timeutil import as_utc

PickMode = Literal["random", "top"]
ExitReason = Literal["stop", "gap_stop", "take_profit", "window_end"]


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    quantity: float
    entry_price: float  # native, after slippage
    exit_price: float  # native, after slippage
    currency: str
    cost_ils: float  # what was paid, commission included
    proceeds_ils: float  # what came back, commission deducted
    reason: ExitReason

    @property
    def pnl_ils(self) -> float:
        return self.proceeds_ils - self.cost_ils

    @property
    def return_pct(self) -> float:
        return self.pnl_ils / self.cost_ils * 100.0 if self.cost_ils else 0.0


@dataclass
class Decision:
    """What the screener decided on a day. Compared in the look-ahead tests."""

    day: pd.Timestamp
    symbol: str
    quantity: float
    stop: float
    take_profit: float
    score: float
    confidence: float


@dataclass
class _Position:
    sec: Security
    quantity: float
    entry_date: pd.Timestamp
    entry_price: float
    cost_ils: float
    stop: float
    take_profit: float
    highest_high: float | None = None
    last_close: float = 0.0


@dataclass
class _Order:
    cand: BtCandidate


@dataclass
class RunResult:
    preset: str
    mode: str
    start: pd.Timestamp
    end: pd.Timestamp
    start_capital: float
    end_equity: float
    return_pct: float
    max_drawdown_pct: float
    benchmark: str
    benchmark_return_pct: float
    excess_pct: float
    trades: list[Trade] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    equity: list[tuple[pd.Timestamp, float]] = field(default_factory=list)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def n_stop_outs(self) -> int:
        return sum(t.reason in ("stop", "gap_stop") for t in self.trades)

    @property
    def n_take_profits(self) -> int:
        return sum(t.reason == "take_profit" for t in self.trades)

    @property
    def hit_rate(self) -> float | None:
        return sum(t.pnl_ils > 0 for t in self.trades) / len(self.trades) if self.trades else None


class _Fx:
    """USD/ILS per day: a series from the store (last value on or before the day) or a fixed rate."""

    def __init__(self, store: HistoryStore, s: Settings) -> None:
        self.fixed = s.fx_fallback_usd_ils
        df = store.load_history(s.backtest_fx_series_symbol)
        self.series: pd.Series | None = None if df is None else df["Close"]

    def at(self, day: pd.Timestamp) -> float:
        if self.series is None:
            return self.fixed
        v = float(self.series.asof(day))  # type: ignore[arg-type]
        return v if math.isfinite(v) and v > 0 else self.fixed


class _Market:
    """The executor's view of one day's bar, by exact date. Decisions never go through this."""

    def __init__(self, store: HistoryStore, symbols: list[str]) -> None:
        self.frames: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            df = store.load_history(sym)
            if df is not None:
                self.frames[sym] = df

    def bar(self, symbol: str, day: pd.Timestamp) -> pd.Series | None:
        df = self.frames.get(symbol)
        if df is None or day not in df.index:
            return None
        row = df.loc[day]
        return row.iloc[-1] if isinstance(row, pd.DataFrame) else row


def max_drawdown_pct(values: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100.0)
    return worst


def simulate(
    store: HistoryStore,
    securities: list[Security],
    preset: str,
    start: pd.Timestamp | str,
    end: pd.Timestamp | str,
    *,
    mode: PickMode = "top",
    rng: np.random.Generator | None = None,
    settings: Settings | None = None,
    benchmark: str | None = None,
    score_cache: ScoreCache | None = None,
) -> RunResult:
    s = settings or get_settings()
    bench = benchmark or s.backtest_benchmark
    bench_df = store.load_history(bench)
    if bench_df is None:
        raise ValueError(f"benchmark {bench} is not in the history store")
    t0, t1 = as_timestamp(start), as_timestamp(end)
    days = [pd.Timestamp(d) for d in bench_df.index if t0 <= d <= t1]
    if len(days) < 2:
        raise ValueError("the window has fewer than two trading days in the benchmark history")
    if mode == "random" and rng is None:
        raise ValueError("random mode needs a seeded numpy Generator")
    risk = resolve_risk_filter({"preset": preset}, s)
    fx = _Fx(store, s)
    secs = {x.symbol: x for x in securities if store.has(x.symbol)}
    market = _Market(store, sorted(secs))
    cache: ScoreCache = score_cache if score_cache is not None else {}
    fee = s.backtest_commission_bps / 10_000.0
    slip = s.backtest_slippage_bps / 10_000.0

    cash = s.backtest_capital_ils
    positions: dict[str, _Position] = {}
    pending: list[_Order] = []
    trades: list[Trade] = []
    decisions: list[Decision] = []
    equity: list[tuple[pd.Timestamp, float]] = []

    def close_position(
        pos: _Position, day: pd.Timestamp, native_px: float, reason: ExitReason
    ) -> float:
        px = native_px * (1.0 - slip)
        gross = to_ils(px * pos.quantity, pos.sec.currency, fx.at(day))
        proceeds = gross * (1.0 - fee)
        trades.append(
            Trade(
                symbol=pos.sec.symbol,
                entry_date=pos.entry_date,
                exit_date=day,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                exit_price=px,
                currency=pos.sec.currency,
                cost_ils=pos.cost_ils,
                proceeds_ils=proceeds,
                reason=reason,
            )
        )
        del positions[pos.sec.symbol]
        return proceeds

    for i, day in enumerate(days):
        usd = fx.at(day)
        last_day = i == len(days) - 1

        # 1. fill yesterday's orders at today's open
        for order in pending:
            c = order.cand
            bar = market.bar(c.sec.symbol, day)
            if bar is None or c.sec.symbol in positions:
                continue
            fill = float(bar["Open"]) * (1.0 + slip)
            unit_ils = to_ils(fill, c.sec.currency, usd) * (1.0 + fee)
            qty = min(c.quantity, _floor(cash / unit_ils, c.sec))
            if qty <= 0 or fill <= 0:
                continue
            cost = unit_ils * qty
            cash -= cost
            positions[c.sec.symbol] = _Position(
                sec=c.sec,
                quantity=qty,
                entry_date=day,
                entry_price=fill,
                cost_ils=cost,
                stop=c.stop,
                take_profit=c.take_profit,
                highest_high=None,
                last_close=fill,
            )
        pending = []

        # 2. stops and take-profits on today's bar
        for pos in list(positions.values()):
            bar = market.bar(pos.sec.symbol, day)
            if bar is None:
                continue
            o, h, lo, cl = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
            pos.last_close = cl
            pos.highest_high = max(pos.highest_high or 0.0, h)
            if o <= pos.stop:
                cash += close_position(pos, day, o, "gap_stop")
            elif lo <= pos.stop:
                cash += close_position(pos, day, pos.stop, "stop")
            elif o >= pos.take_profit:
                cash += close_position(pos, day, o, "take_profit")
            elif h >= pos.take_profit:
                cash += close_position(pos, day, pos.take_profit, "take_profit")

        # 3. mark to market (a symbol with no bar today keeps its last close)
        if last_day:
            for pos in list(positions.values()):
                cash += close_position(pos, day, pos.last_close, "window_end")
        held = {
            p.sec.symbol: to_ils(p.last_close * p.quantity, p.sec.currency, usd)
            for p in positions.values()
        }
        total = cash + sum(held.values())
        equity.append((day, total))
        if last_day:
            break

        # 4. trailing stops: recomputed by exit_levels, ratchet only
        if risk.stop_type != "fixed" and i % s.backtest_trailing_update_every_days == 0:
            for pos in positions.values():
                _ratchet(store, pos, day, usd, total, risk, s)

        # 5. rebalance: screen on as-of data, queue orders for tomorrow's open
        if (
            i % s.backtest_rebalance_every_days == 0
            and len(positions) < s.backtest_max_open_positions
        ):
            book = Book(total=total)
            for sym, v in held.items():
                book.add(secs[sym], v)
            picks = _screen(
                store, list(secs.values()), day, risk, book, cash, usd, s, set(positions), cache
            )
            budget = s.backtest_max_open_positions - len(positions)
            chosen = _choose(picks, mode, rng, min(s.backtest_max_new_per_rebalance, budget))
            reserved = 0.0
            for cand in chosen:
                # re-check the caps against what the earlier picks of this day already reserved
                again = evaluate_asof(
                    cand.sec,
                    _asof(store, cand.sec.symbol, day, s),
                    day,
                    risk=risk,
                    horizon=s.backtest_horizon,
                    book=book,
                    cash_ils=cash - reserved,
                    usd_ils=usd,
                    settings=s,
                    score_cache=cache,
                )
                if again is None:
                    continue
                cost = to_ils(again.price * again.quantity, again.sec.currency, usd)
                reserved += cost * (1.0 + fee + slip)
                book.add(again.sec, cost)
                pending.append(_Order(again))
                decisions.append(
                    Decision(
                        day=day,
                        symbol=again.sec.symbol,
                        quantity=again.quantity,
                        stop=round(again.stop, 6),
                        take_profit=round(again.take_profit, 6),
                        score=again.score,
                        confidence=again.confidence,
                    )
                )

    start_eq = s.backtest_capital_ils
    end_eq = equity[-1][1]
    closes = bench_df["Close"]
    b0, b1 = float(closes.loc[days[0]]), float(closes.loc[days[-1]])
    b_ret = (b1 / b0 - 1.0) * 100.0
    ret = (end_eq / start_eq - 1.0) * 100.0
    return RunResult(
        preset=preset,
        mode=mode,
        start=days[0],
        end=days[-1],
        start_capital=start_eq,
        end_equity=end_eq,
        return_pct=ret,
        max_drawdown_pct=max_drawdown_pct([start_eq, *[v for _, v in equity]]),
        benchmark=bench,
        benchmark_return_pct=b_ret,
        excess_pct=ret - b_ret,
        trades=trades,
        decisions=decisions,
        equity=equity,
    )


def _floor(qty: float, sec: Security) -> float:
    if sec.asset_type == "crypto":
        return math.floor(qty * 1e6) / 1e6
    return float(math.floor(qty))


def _asof(store: HistoryStore, symbol: str, day: pd.Timestamp, s: Settings) -> pd.DataFrame:
    df = store.asof(symbol, day, s.history_days)
    return df if df is not None else pd.DataFrame()


def _screen(
    store: HistoryStore,
    secs: list[Security],
    day: pd.Timestamp,
    risk: RiskFilter,
    book: Book,
    cash: float,
    usd: float,
    s: Settings,
    held: set[str],
    cache: ScoreCache,
) -> list[BtCandidate]:
    rf = risk
    held_groups = {x.dual_listing_group for x in secs if x.symbol in held and x.dual_listing_group}
    out: list[BtCandidate] = []
    for sec in secs:
        if sec.symbol in held or (sec.dual_listing_group and sec.dual_listing_group in held_groups):
            continue
        df = _asof(store, sec.symbol, day, s)
        cand = evaluate_asof(
            sec,
            df,
            day,
            risk=rf,
            horizon=s.backtest_horizon,
            book=book,
            cash_ils=cash,
            usd_ils=usd,
            settings=s,
            score_cache=cache,
        )
        if cand is not None:
            out.append(cand)
    out.sort(key=lambda c: (-c.rank_score, c.sec.symbol))
    # one listing per company (the better ranked one)
    seen: set[str] = set()
    kept: list[BtCandidate] = []
    for c in out:
        g = c.sec.dual_listing_group
        if g and g in seen:
            continue
        if g:
            seen.add(g)
        kept.append(c)
    return kept


def _choose(
    picks: list[BtCandidate], mode: PickMode, rng: np.random.Generator | None, n: int
) -> list[BtCandidate]:
    if n <= 0 or not picks:
        return []
    if mode == "top":
        return picks[:n]
    assert rng is not None
    order = rng.permutation(len(picks))[:n]  # `picks` is sorted, so the draw is reproducible
    return [picks[int(j)] for j in order]


def _ratchet(
    store: HistoryStore,
    pos: _Position,
    day: pd.Timestamp,
    usd: float,
    total: float,
    risk: RiskFilter,
    s: Settings,
) -> None:
    rf = risk
    df = _asof(store, pos.sec.symbol, day, s)
    if df.empty or pd.Timestamp(df.index[-1]) != day:
        return
    price = float(df["Close"].iloc[-1])
    at = as_utc(pd.Timestamp(day).to_pydatetime().replace(hour=23))
    quote = PriceQuote(
        symbol=pos.sec.symbol, price=price, currency=pos.sec.currency, as_of=at, source="backtest"
    )
    valued = _valued(pos.sec, quote, usd, pos.quantity, 0)
    res = compute_exit_levels(
        valued,
        s.backtest_horizon,
        rf,
        df,
        state=StopState(stop=pos.stop, highest_high=pos.highest_high),
        portfolio_value_ils=total,
        now=at,
        settings=s,
    )
    if res.status != "levels" or res.state is None or res.state.stop is None:
        return
    pos.stop = max(pos.stop, res.state.stop)  # a stop only moves up

"""The markdown report of one experiment. The caveats are part of the report, never optional."""

from __future__ import annotations

from datetime import date

from app.backtest.data import HistoryStore
from app.backtest.experiment import ExperimentResult, Summary, heldout_passes
from app.config import Settings
from app.launchgate import weights_fingerprint

SYNTHETIC_BANNER = (
    "> **SYNTHETIC DATA. NOT EVIDENCE OF REAL PERFORMANCE.** These prices are random walks made by "
    "code. Every number below only shows that the machinery runs. Do not read any success rate here "
    "as a statement about the strategy, and never record it to the launch gate."
)
UNKNOWN_BANNER = (
    "> **Data provenance unknown.** The history store has no manifest, so it is not known whether "
    "the prices are real or synthetic. Treat every number below as unverified."
)


def _pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}%"


def _rate(sm: Summary) -> str:
    if sm.rate is None:
        return "n/a"
    lo = _pct(sm.ci_low * 100, 0) if sm.ci_low is not None else "n/a"
    hi = _pct(sm.ci_high * 100, 0) if sm.ci_high is not None else "n/a"
    return f"**{sm.rate * 100:.0f}%** ({sm.successes}/{sm.n_runs}; 95% CI {lo} to {hi})"


def _table(result: ExperimentResult, split: str, s: Settings) -> list[str]:
    lines = [
        "| Profile | Success rate | Windows | Independent windows | Mean return | Mean excess | Mean max drawdown | Stop-out share | Hit rate | Target (return / max drawdown) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p in result.config.profiles:
        sm = result.summary(p, split)
        t = s.backtest_targets[p]
        lines.append(
            f"| {p} | {_rate(sm)} | {sm.n_windows} | {sm.independent_windows:.1f} "
            f"| {_pct(sm.mean_return_pct, 2)} | {_pct(sm.mean_excess_pct, 2)} "
            f"| {_pct(sm.mean_drawdown_pct, 2)} "
            f"| {_pct(sm.stop_out_share * 100 if sm.stop_out_share is not None else None, 0)} "
            f"| {_pct(sm.hit_rate * 100 if sm.hit_rate is not None else None, 0)} "
            f"| {t.min_return_pct:g}% / {t.max_drawdown_pct:g}% |"
        )
    return lines


def render_report(
    result: ExperimentResult,
    store: HistoryStore,
    s: Settings,
    *,
    day: date,
    recorded: str | None = None,
) -> str:
    cfg = result.config
    manifest = store.manifest()
    synthetic = store.is_synthetic
    ok = heldout_passes(result, s.backtest_success_threshold)
    out: list[str] = [f"# Walk-forward backtest, {day.isoformat()}", ""]
    if synthetic:
        out += [SYNTHETIC_BANNER, ""]
    elif not store.provenance_known:
        out += [UNKNOWN_BANNER, ""]
    out += [
        "## Headline: held-out success rate",
        "",
        f"Held-out = windows that start after **{result.train_until.date()}**"
        + (" (picked automatically from the data range)" if result.train_until_automatic else "")
        + ". Nothing was tuned on them. A run succeeds when its return reaches the profile's target, "
        "it beats the benchmark, and its max drawdown stays under the cap.",
        "",
        *_table(result, "heldout", s),
        "",
        f"Required by config for every profile: {s.backtest_success_threshold:.0%}. "
        f"Result: **{'MET' if ok else 'NOT MET'}**"
        + (
            " (but see the data warning above)." if synthetic or not store.provenance_known else "."
        ),
        "",
        f"Launch gate: {recorded or 'nothing was recorded (run with --record to try).'}",
        "",
        "## Train windows (for comparison only)",
        "",
        "If the train rate is far above the held-out rate, the setup is fitting the past.",
        "",
        *_table(result, "train", s),
        "",
        "## Targets and caps (PROPOSALS, need your approval)",
        "",
        "| Profile | Min return per window | Max drawdown |",
        "|---|---|---|",
        *(
            f"| {p} | {s.backtest_targets[p].min_return_pct:g}% | {s.backtest_targets[p].max_drawdown_pct:g}% |"
            for p in cfg.profiles
        ),
        "",
        f"Approved by the user: **{'yes' if s.backtest_targets_approved else 'NO'}**. "
        "`--record` is refused until `BACKTEST_TARGETS_APPROVED=true`.",
        "",
        "## Setup",
        "",
        f"- Profiles: {', '.join(cfg.profiles)}; window {cfg.window_months} months, step {cfg.step_months} month(s); "
        f"pick mode `{cfg.mode}`"
        + (
            f" with {cfg.runs} seeded runs per window, seed {cfg.seed}"
            if cfg.mode == "random"
            else " (deterministic)"
        ),
        f"- Windows from {result.first_start.date()} to {result.last_end.date()}; "
        f"{result.dropped_straddling} window(s) straddling the split were dropped",
        f"- Universe in the store: {result.n_symbols} securities; benchmark {cfg.benchmark or s.backtest_benchmark}",
        f"- Start capital {s.backtest_capital_ils:,.0f} ILS, empty portfolio; horizon {s.backtest_horizon}; "
        f"screen every {s.backtest_rebalance_every_days} trading days; up to {s.backtest_max_new_per_rebalance} new buys each time",
        f"- Costs per side: commission {s.backtest_commission_bps:g} bps, slippage {s.backtest_slippage_bps:g} bps; "
        f"FX {'series ' + s.backtest_fx_series_symbol if store.has(s.backtest_fx_series_symbol) else 'fixed ' + str(s.fx_fallback_usd_ils)}",
        f"- Weights config tested: `{weights_fingerprint(s.signal_weights)}` (only technical + patterns carry data here)",
        f"- Data: source `{manifest.get('source', 'unknown')}`, fetched {manifest.get('fetched_at', 'unknown')}, synthetic: {manifest.get('synthetic', 'unknown')}",
        "",
        "## Caveats (read before trusting any number)",
        "",
        "1. **Only the technical and patterns signals are backtested.** Fundamentals, analysts, "
        "news and sentiment have no point-in-time history here, so the tested score is not the live score. "
        "The LLM layer is validated by forward paper trading, never by this backtest.",
        "2. **Survivorship bias.** The universe is today's seed list. Companies that were delisted, "
        "merged or dropped out of the indices are missing, which flatters every result.",
        "3. **Overlapping windows are not independent.** A 6-month window every month means each month "
        'is in several windows. The column "Independent windows" is the honest sample size; the confidence '
        "interval above treats runs as independent and is therefore too narrow.",
        "4. **Synthetic data proves nothing.** Any result computed from a store whose manifest says "
        "`synthetic: true` is NOT evidence of real performance.",
        "5. **The held-out figure is the headline.** The train figure is shown only to expose overfitting. "
        "Re-running with a different `--train-until` after seeing the held-out result turns it back into a train figure.",
        "6. **Simplified execution.** Fills at the next open plus fixed slippage; a bar touching both stop and "
        "target counts as the stop; no dividends (prices are not dividend-adjusted), no tax, no liquidity limit, "
        "no halts. TASE history before 2026 follows a Sunday to Thursday week; the replay uses the benchmark's calendar.",
        "7. **One benchmark for a mixed universe.** The excess return is measured against a single index "
        f"({cfg.benchmark or s.backtest_benchmark}) even when the portfolio holds TASE names.",
        "8. **Random mode is a null model, not a strategy.** It picks at random among candidates that already passed "
        "the screener's filters, so a good rate says the filters (stops, caps, volatility cap) do the work, not the ranking.",
        "9. **Targets are proposals.** They were not chosen from the data, but a pass only means the proposed bar was met.",
        "",
        "Not financial advice.",
        "",
    ]
    return "\n".join(out)

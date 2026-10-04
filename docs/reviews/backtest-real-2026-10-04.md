# Real-history backtest and tuning (2026-10-04)

## Verdict
**The 80% held-out success bar is not reachable with the approved targets, for any profile.** Tuning the non-risk parameters did not help: no setting beat the defaults on the train windows, so the defaults stay. Nothing was recorded to the launch gate. A technical+patterns-only backtest cannot open it anyway.

## Data
- Yahoo through `fetch-history` (yfinance `HistoryProvider`). 86 symbols with nothing missing: the universe plus `^GSPC`, `^TA125.TA` and `ILS=X`. Daily bars from 2015 to 2026-10-02. Manifest `synthetic: false`. TASE prices are already in ILS.
- Stooq answered 200 but drops connections; it is not used.
- The data sits in `backend/data/history/` (git-ignored). Re-create it with `python -m app.cli fetch-history --symbols-from universe --years 8`.

## Baseline: full run, defaults, `top` mode, 1-month step
Report: `docs/reviews/backtest-real-2026-10-04-baseline-top.md` (39 min on 4 workers). Held-out windows start after 2023-07-22.

| Profile | Held-out success | Train success | Held-out mean return | Held-out mean vs ^GSPC | Held-out mean max DD | Stop-out share |
|---|---|---|---|---|---|---|
| conservative (3% / 4%) | 3% (1/32) | 6% | 3.5% | −6.6% | 5.8% | 47% |
| balanced (5% / 7%) | 6% (2/32) | 2% | 1.8% | −8.3% | 5.3% | 75% |
| balanced_aggressive (8% / 10%) | 9% (3/32) | 6% | 2.5% | −7.7% | 6.7% | 72% |
| aggressive (12% / 15%) | 9% (3/32) | 4% | 3.3% | −6.8% | 7.9% | 66% |

## How hard the targets are
The share of 6-month windows (2016–2026, monthly starts) in which simply holding the index reaches the return target and stays under the drawdown cap:

| Profile | ^GSPC all / since 2023-07 | ^TA125.TA all / since 2023-07 |
|---|---|---|
| conservative | 11% / 3% | 2% / 0% |
| balanced | 35% / 39% | 32% / 36% |
| balanced_aggressive | 45% / 58% | 37% / 67% |
| aggressive | 24% / 33% | 29% / 64% |

A successful run must also *beat* `^GSPC`. The 4% cap on the conservative profile is tighter than the index's usual 6-month drawdown, which averages 10.3%.

## Tuning: one change at a time, `top` mode, 3-month step
Only non-risk parameters were changed. The decision was made on **train** windows only (27 per profile). Held-out has 11 windows per profile and is shown for transparency. Each cell reads train / held-out success.

| Change | conservative | balanced | balanced_aggressive | aggressive | Kept? |
|---|---|---|---|---|---|
| defaults (screen every 5 days, 3 new buys, trail update every 5 days) | 11% / 0% | 4% / 9% | 4% / 27% | 0% / 27% | yes |
| screen every 10 days | 4% / 0% | 0% / 0% | 4% / 0% | 7% / 18% | no: worse on train overall |
| screen every 20 days | 0% / 0% | 0% / 0% | 0% / 0% | 0% / 0% | no |
| 1 new buy per screening | 7% / 0% | 0% / 0% | 0% / 9% | 4% / 9% | no |
| 5 new buys per screening | 7% / 9% | 4% / 9% | 4% / 27% | 0% / 18% | no: no train gain |
| trail update every day | 11% / 0% | 4% / 9% | 0% / 27% | 0% / 27% | no |
| trail update every 10 days | 11% / 0% | 4% / 9% | 0% / 27% | 4% / 27% | no |

Every run trails `^GSPC` on average, by 2–9% per 6 months. 42–81% of closed trades are stop-outs.

## What would have to change (all are your decisions)
1. **Targets or caps.** For example, drop "beat the benchmark" for the conservative profile, or relax the 4% / 7% drawdown caps. Today even the index fails them most of the time.
2. **Stop rules** (risk logic). Most trades end on a stop, which suggests the stops are too tight for daily noise.
3. **Signals.** Only technical + patterns are tested. Fundamentals, analysts, news and sentiment have no point-in-time history here.
4. **Accept the result.** Leave the technical-only screener as an idea list, with no verdicts, and let paper trading be the real test.

## Caveats
Survivorship bias (today's universe), overlapping windows (the CIs are too narrow), no dividends, one benchmark for a mixed US/TASE book, simplified fills. See the caveats list in the baseline report. Not financial advice.

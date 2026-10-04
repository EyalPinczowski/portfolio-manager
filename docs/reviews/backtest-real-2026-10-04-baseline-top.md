# Walk-forward backtest, 2026-10-04

## Headline: held-out success rate

Held-out = windows that start after **2023-07-22** (picked automatically from the data range). Nothing was tuned on them. A run succeeds when its return reaches the profile's target, it beats the benchmark, and its max drawdown stays under the cap.

| Profile | Success rate | Windows | Independent windows | Mean return | Mean excess | Mean max drawdown | Stop-out share | Hit rate | Target (return / max drawdown) |
|---|---|---|---|---|---|---|---|---|---|
| conservative | **3%** (1/32; 95% CI 1% to 16%) | 32 | 5.3 | 3.53% | -6.60% | 5.84% | 47% | 45% | 3% / 4% |
| balanced | **6%** (2/32; 95% CI 2% to 20%) | 32 | 5.3 | 1.81% | -8.32% | 5.30% | 75% | 37% | 5% / 7% |
| balanced_aggressive | **9%** (3/32; 95% CI 3% to 24%) | 32 | 5.3 | 2.48% | -7.65% | 6.68% | 72% | 37% | 8% / 10% |
| aggressive | **9%** (3/32; 95% CI 3% to 24%) | 32 | 5.3 | 3.29% | -6.84% | 7.88% | 66% | 41% | 12% / 15% |

Required by config for every profile: 80%. Result: **NOT MET**.

Launch gate: nothing was recorded (--record not given).

## Train windows (for comparison only)

If the train rate is far above the held-out rate, the setup is fitting the past.

| Profile | Success rate | Windows | Independent windows | Mean return | Mean excess | Mean max drawdown | Stop-out share | Hit rate | Target (return / max drawdown) |
|---|---|---|---|---|---|---|---|---|---|
| conservative | **6%** (5/82; 95% CI 3% to 13%) | 82 | 13.7 | 2.89% | -2.62% | 5.65% | 48% | 44% | 3% / 4% |
| balanced | **2%** (2/82; 95% CI 1% to 8%) | 82 | 13.7 | -0.09% | -5.60% | 5.56% | 78% | 35% | 5% / 7% |
| balanced_aggressive | **6%** (5/82; 95% CI 3% to 13%) | 82 | 13.7 | 1.52% | -3.98% | 6.53% | 74% | 38% | 8% / 10% |
| aggressive | **4%** (3/82; 95% CI 1% to 10%) | 82 | 13.7 | 2.89% | -2.61% | 7.19% | 68% | 40% | 12% / 15% |

## Targets and caps (PROPOSALS, need your approval)

| Profile | Min return per window | Max drawdown |
|---|---|---|
| conservative | 3% | 4% |
| balanced | 5% | 7% |
| balanced_aggressive | 8% | 10% |
| aggressive | 12% | 15% |

Approved by the user: **NO**. `--record` is refused until `BACKTEST_TARGETS_APPROVED=true`.

## Setup

- Profiles: conservative, balanced, balanced_aggressive, aggressive; window 6 months, step 1 month(s); pick mode `top` (deterministic)
- Windows from 2016-04-06 to 2026-09-06; 6 window(s) straddling the split were dropped
- Universe in the store: 83 securities; benchmark ^GSPC
- Start capital 100,000 ILS, empty portfolio; horizon 3m; screen every 5 trading days; up to 3 new buys each time
- Costs per side: commission 10 bps, slippage 5 bps; FX series ILS=X
- Weights config tested: `a8f622eb633471ed` (only technical + patterns carry data here)
- Data: source `HistoryProvider (yfinance)`, fetched 2026-10-04T15:58:41.941212+00:00, synthetic: False

## Caveats (read before trusting any number)

1. **Only the technical and patterns signals are backtested.** Fundamentals, analysts, news and sentiment have no point-in-time history here, so the tested score is not the live score. The LLM layer is validated by forward paper trading, never by this backtest.
2. **Survivorship bias.** The universe is today's seed list. Companies that were delisted, merged or dropped out of the indices are missing, which flatters every result.
3. **Overlapping windows are not independent.** A 6-month window every month means each month is in several windows. The column "Independent windows" is the honest sample size; the confidence interval above treats runs as independent and is therefore too narrow.
4. **Synthetic data proves nothing.** Any result computed from a store whose manifest says `synthetic: true` is NOT evidence of real performance.
5. **The held-out figure is the headline.** The train figure is shown only to expose overfitting. Re-running with a different `--train-until` after seeing the held-out result turns it back into a train figure.
6. **Simplified execution.** Fills at the next open plus fixed slippage; a bar touching both stop and target counts as the stop; no dividends (prices are not dividend-adjusted), no tax, no liquidity limit, no halts. TASE history before 2026 follows a Sunday to Thursday week; the replay uses the benchmark's calendar.
7. **One benchmark for a mixed universe.** The excess return is measured against a single index (^GSPC) even when the portfolio holds TASE names.
8. **Random mode is a null model, not a strategy.** It picks at random among candidates that already passed the screener's filters, so a good rate says the filters (stops, caps, volatility cap) do the work, not the ranking.
9. **Targets are proposals.** They were not chosen from the data, but a pass only means the proposed bar was met.

Not financial advice.

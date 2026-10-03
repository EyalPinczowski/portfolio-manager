# Analysis design: the "Investment Committee"

Status: **proposed** (user input, 2026-10-03). Built in Phase 2 ("Analyze a stock"), and reused by the full portfolio review and the new-stock suggestions.

## Why
Asking one LLM "should I buy X?" gives vague and over-optimistic answers. Instead, the analysis is split into specialised roles:
- The numbers come from **deterministic code**: indicators, fundamentals and consensus.
- An adversarial **Bear** argues against buying.
- A **CIO** weighs everything and outputs a **structured** verdict.

Every hand-off between roles is a validated Pydantic model, never free text.

## Roles

| Role | Type | Input | Output model | Notes |
|---|---|---|---|---|
| **Data Scout** | Code (no LLM) | provider layer | `ScoutReport` | Price, market cap, P/E (trailing + forward), EPS consensus and surprises, FCF yield, ROIC, debt/equity, margins trend, analyst consensus and targets, insiders, earnings date. Each field has `value`, `source`, `as_of`, or `missing`. Sources: yfinance (free), Finnhub free (US), SEC EDGAR XBRL "companyfacts" (free, US fundamentals), MAYA scraping (TASE, best-effort). |
| **Chartist** | Code (no LLM) | OHLCV | `ChartReport` | The existing `signals/technical.py` + `patterns.py`: RSI, MA 20/50/200, MACD, Bollinger, ATR, support/resistance, patterns. **All numbers are pre-computed.** The LLM never calculates indicators. |
| **News & Macro analyst** | Code + LLM | news/filings search results, GDELT tone, F&G, VIX | `NewsReport` | The LLM only *classifies and summarises* retrieved items, and every claim must cite an item id. Sources: Finnhub company news, SEC EDGAR full-text search (free), RSS (Reuters, Globes, Calcalist, TheMarker), and optionally **Exa** semantic search restricted to primary sources (Reuters, Bloomberg, SEC, TASE/MAYA) when `EXA_API_KEY` is set. |
| **Bear** | LLM | Scout + Chartist + News reports | `BearCase` | The system prompt is hard-wired to argue **against** buying: overvaluation, falling margins, debt, regulation, geopolitics (Israel/region), dilution, insider selling, technical breakdown. It must cite fields from the reports and may not invent numbers. It outputs ranked risks, each with `severity` (1–5), `evidence_refs[]` and `what_would_invalidate`. |
| **CIO** | LLM | all reports + BearCase + user's risk filter, horizon and portfolio fit | `CIOVerdict` | Weighs the bull evidence (signal scores) against the Bear. It **can only adjust the deterministic score within ±15 points**, and must give a reason for every adjustment and answer each Bear risk with `rebutted` / `accepted` / `unresolved`. The final verdict then passes through `scoring/risk.py`, which can still block or downgrade. |

The **"Why?" button** shows this committee view: each role's report, the Bear's case, and how the CIO resolved each point.

## Structured outputs (Pydantic)
- Every role output is a Pydantic v2 model in `backend/app/committee/schemas.py`. The LLM is called with a JSON response schema (Gemini `response_schema`, Groq JSON mode), then the result is validated with `Model.model_validate_json`.
- **Validation chain:**
  1. If the schema check fails, retry once with the validation error attached.
  2. If it fails again, fall back to a rule-based template, for example a Bear case generated from the signal thresholds.
- **Grounding check:** every number in the LLM text must appear in the input reports, within rounding. Any claim that cites a missing `evidence_ref` is dropped and logged.
- Enums are fixed and never free text:
  - `verdict ∈ {strong_buy, buy, hold, trim, sell}` (`add` = buy when the stock is already held)
  - `confidence ∈ [0, 1]`
  - `severity ∈ 1..5`

## Cost and limits (free tier)
- One full analysis = **3 LLM calls** (News, Bear, CIO). Everything else is code.
- Gemini free (~10 RPM) is fine for on-demand "Analyze a stock".
- The **screener does NOT run the committee on the whole universe**. Deterministic scores rank ~800 symbols, and only the **top ~10 finalists** go through the committee, from a queue at no more than 8 RPM.
- Results are cached for each symbol per data refresh. Groq is the fallback provider, and templates are the last resort.

## Evaluation loop (`backend/evals/`)
- **Scenario fixtures (≥15)**, each frozen data for `ScoutReport`, `ChartReport` and `NewsReport`:
  - Covid crash in Mar 2020
  - Oct 2023 TASE shock
  - 2022 tech drawdown
  - a crypto −40% week
  - a mixed earnings beat with a guidance cut
  - a strong uptrend with extreme valuation
  - a TASE small-cap with no analyst coverage
  - a fully data-empty ticker
  - conflicting signals (bullish chart, bearish fundamentals)
  - an insider selling cluster
  - an earnings date inside the horizon
  - a dual-listed stock
  - an ETF
  - a stablecoin
  - a halted or illiquid stock
- **3–5 trials per scenario.** Record the *distribution* of verdicts and confidence.
- **Metrics:**
  - schema-valid rate (target 100% after the fallback)
  - grounding violations (target 0)
  - verdict stability (the share of trials with the modal verdict; target ≥ 80%)
  - whether the Bear raised the scenario's known key risk (recall)
  - CIO adjustments stay within ±15
  - data-empty cases give `hold` with low confidence (never a confident buy)
- CI runs the evals with a **recorded/mock LLM**, so they are deterministic. `make eval-live` runs them against the real free-tier models on demand, and the results are saved to `docs/evals/<date>.md`.

## Paper trading before any real use
- A **paper portfolio** per user automatically follows every committee verdict that passes the risk filter: simulated entries and exits at the next available price, with the suggested stops and targets.
- It tracks P&L vs the S&P 500 / TA-125, hit rate, and stop/target outcomes. It also tracks **operational metrics**: provider errors, rate-limit hits, LLM fallbacks, and latency per role.
- **Important:** LLMs have "seen the future" relative to historical dates, so the LLM layer **cannot be honestly backtested** on past data. The Phase 6 historical backtest validates only the deterministic score. The committee layer is validated **forward**, by paper trading.
- **Launch gate (proposed):** live verdicts are shown only when *both* of these hold:
  - the deterministic backtest passes
  - paper trading has run for at least N weeks with acceptable metrics
- No connection to a real brokerage at any point. The app only suggests trades.

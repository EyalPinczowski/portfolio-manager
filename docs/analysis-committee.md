# Analysis design: the "Investment Committee"

Status: **accepted** (user input, 2026-10-03).
- Decisions: fundamentals become a **6th scored signal**. Weights: technical 25, patterns 10, fundamentals 20, analysts 20, geo/news 12.5, sentiment 12.5.
- News uses **free sources only**. Exa is optional and used only if a key is set.
- Paper-test gate: **goal-based** (see below).

Built in Phase 2 ("Analyze a stock"), and reused by the full portfolio review and the new-stock suggestions.

## Why
Asking one LLM "should I buy X?" gives vague and over-optimistic answers. Instead, the analysis is split into specialised roles:
- The numbers come from **deterministic code**: indicators, fundamentals and consensus.
- An adversarial **Bear** argues against buying.
- A **CIO** weighs everything and outputs a **structured** verdict.

Every hand-off between roles is a validated Pydantic model, never free text.

## Roles

| Role | Type | Input | Output model | Notes |
|---|---|---|---|---|
| **Data Scout** | Code (no LLM) | provider layer | `ScoutReport` | Price, market cap, P/E (trailing + forward), EPS consensus and surprises, FCF yield, ROIC, debt/equity, margins trend, analyst consensus and targets, insiders, earnings date. Each field has `value`, `source`, `as_of`, or `missing`. Sources:
- **defeatbeta-api** (free, Apache-2.0, no API key, no rate limits; a Yahoo-derived dataset on Hugging Face queried through DuckDB) for TTM EPS/PE, PS/PB/PEG, ROE/ROIC/WACC, statements, revenue by segment, SEC filings and earnings-call transcripts.
- yfinance for live prices and analyst targets
- Finnhub free (US)
- SEC EDGAR XBRL as a cross-check
- MAYA scraping (TASE, best-effort)

**Verified in the Phase 2 review (2026-10-03): defeatbeta is US-only (~12.3k symbols), has no `.TA` symbols, and refreshes about weekly.** So it supplies US fundamentals and transcripts only. For TASE names the Scout uses yfinance plus best-effort MAYA, and returns `confidence=0` where there is nothing. Don't use defeatbeta for fresh earnings data. |
| **Chartist** | Code (no LLM) | OHLCV | `ChartReport` | The existing `signals/technical.py` + `patterns.py`: RSI, MA 20/50/200, MACD, Bollinger, ATR, support/resistance, patterns. **All numbers are pre-computed.** The LLM never calculates indicators. |
| **Company Profile analyst** | Code + LLM | defeatbeta revenue by segment/geography, filings (10-K/20-F business section), profile data, officer list | `CompanyProfile` | A business overview, revenue mix by segment and country, **management and key-people changes** (new CEO/CFO, departures, from 8-K item 5.02 / MAYA), and **3–5 competitors** chosen from sector + industry + market cap. Each claim cites a source. |
| **Peer Comparator** | Code (no LLM) | Scout data for the stock + its peers | `PeerTable` | The stock vs. 3–5 peers on P/E, forward P/E, revenue growth, gross/operating margin, ROIC, FCF yield, and 6-month performance. Each value is shown as a percentile within the peer group. |
| **News & Macro analyst** | Code + LLM | news/filings search results, **earnings-call transcripts (defeatbeta) with a quarter-over-quarter tone diff**, GDELT tone, F&G, VIX | `NewsReport` | The LLM only *classifies and summarises* retrieved items, and every claim must cite an item id. Sources: Finnhub company news, SEC EDGAR full-text search (free), RSS (Reuters, Globes, Calcalist, TheMarker), and optionally **Exa** semantic search restricted to primary sources (Reuters, Bloomberg, SEC, TASE/MAYA) when `EXA_API_KEY` is set. |
| **Bear** | LLM | Scout + Chartist + News reports | `BearCase` | The system prompt is hard-wired to argue **against** buying: overvaluation, falling margins, debt, regulation, geopolitics (Israel/region), dilution, insider selling, technical breakdown. It must cite fields from the reports and may not invent numbers. It outputs ranked risks, each with `severity` (1–5), `evidence_refs[]` and `what_would_invalidate`. |
| **CIO** | LLM | all reports + BearCase + user's risk filter, horizon and portfolio fit | `CIOVerdict` | Weighs the bull evidence (signal scores) against the Bear. It **can only adjust the deterministic score within ±15 points**, and must give a reason for every adjustment and answer each Bear risk with `rebutted` / `accepted` / `unresolved`. The final verdict then passes through `scoring/risk.py`, which can still block or downgrade. |

The **"Why?" button** shows this committee view: each role's report, the Bear's case, and how the CIO resolved each point.

## "No hand-written API integrations" (user-shared article, 2026-10-03)
The idea from the article: give the agents **ready-made data tools** instead of hand-coding an integration for each API. How we apply it:
- Use maintained Python data packages (`defeatbeta-api`, `yfinance`, `edgartools`) behind thin provider adapters. We don't write raw HTTP clients unless no package exists, as with MAYA or the CNN F&G endpoint.
- The committee's LLM roles never fetch data themselves. Our code fetches it first and passes it in as structured reports. This keeps every number grounded and testable.
- **Nice-to-have:** expose our own provider layer and portfolio as an **MCP server** (`backend/app/mcp/`). The user can then ask Claude Desktop or another MCP client questions about their portfolio. defeatbeta ships its own MCP server, which can sit next to ours.

## Earnings-call tone tracking
- For each of the last 2–4 quarters, the LLM extracts a `CallTone` object from the transcript:
  - tone (−2..+2)
  - guidance direction: raised / maintained / cut / none
  - hedging-language count
  - top 3 themes
  - quoted evidence lines
- Code then diffs the quarters into `ToneShift`, e.g. "guidance raised → maintained; tone +1 → −1; new theme: 'pricing pressure'". The Bear must consider any negative shift.
- Extraction runs once per new transcript and is cached, so it costs few free-tier calls.
- US coverage only via defeatbeta. TASE companies without transcripts get `confidence=0`.

## Ask-my-portfolio chat
- A chat box on the main page plus Telegram free text: "why am I down this week?", "what's my biggest risk?", "should I worry about NICE?".
- **Tool-use over our own API**, never free-form guessing. The LLM gets read-only tools scoped to the current user: `get_summary`, `get_holdings`, `get_xray`, `get_scorecard(symbol)`, `get_analysis(symbol)`, `get_exit_levels(holding)`, `get_performance(period)`.
- Answers must cite the tool results (the grounding check applies) and end with "Why?" links to the source cards.
- It never places trades and never changes settings. It only suggests or links to the relevant page.
- The chat history is stored per user, can be deleted, and is included in the data export.

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
- One full analysis = **4 LLM calls** (Company Profile, News, Bear, CIO); the profile is cached for weeks and the transcript tone per quarter. Everything else is code.
- Gemini free (~10 RPM) is fine for on-demand "Analyze a stock".
- The **screener does NOT run the committee on the whole universe**. Deterministic scores rank ~800 symbols, and only the **top ~10 finalists** go through the committee, from a queue at no more than 8 RPM.
- Results are cached for each symbol per data refresh. Groq is the fallback provider, and templates are the last resort.
- **Real numbers (config in `backend/app/config.py`, free tier only):**
  - Per provider: 8 requests a minute (`llm_requests_per_minute`) and 900 a day (`llm_daily_budget`), shared by everyone.
  - Per user: 3 calls a minute and 60 a day (`llm_user_rpm`, `llm_user_daily_budget`), so a user could do about 15 full runs a day on quota alone.
  - **Committee run caps:** 5 runs an hour and 10 a day per user (`committee_rate_limit_per_hour`, `committee_runs_per_user_per_day`). Every tap counts, including ones answered from saved results. The screen shows the runs left today and the 429 message says the count resets within 24 hours.
  - **Cache:** role prompts are built from stable facts (profile and news get identity fields only; Bear and CIO get rounded price and score and no timestamps), so a price tick no longer changes the cache key and a repeat run costs no model calls.
  - **Ask my portfolio costs 0 AI tokens:** no free provider is allowed to see portfolio data, so it always uses templates.
  - **Visibility:** admins see today's requests, tokens and fallbacks per provider in Settings → Admin ("AI usage today"). Input/output tokens and cache hits are not recorded.

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
- **Launch gate (decided, goal-based):** live verdicts are shown only when *all* of these hold:
  1. the deterministic backtest passes
  2. paper trading has run for at least **4 weeks** with no critical operational errors
  3. at least **50 paper calls** have completed their 1-month evaluation window
  4. the paper calls beat buy-and-hold of the S&P 500 / TA-125 over the same period

  Reasons:
  - a call can only be graded after its horizon ends
  - with fewer than ~50 resolved calls, the margin of error on the hit rate is more than ±14%
  - a short window covers only one market regime

  Expected wait: about 6–10 weeks. Until then, the app shows the live paper track record. The thresholds live in config.
- No connection to a real brokerage at any point. The app only suggests trades.

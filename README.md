# Portfolio Manager

A personal stock portfolio analysis and recommendation assistant for the **US** (NYSE / NASDAQ) and **Israeli** (TASE) markets.
It watches your holdings and a watchlist, scores each one from several independent signals, and shows **buy / sell / hold** suggestions in an app that refreshes all day. Every suggestion is checked against the **risk profile you set**.

> ⚠️ **Not financial advice.** This is a decision-support tool for personal use. It can be wrong, data can be delayed or missing, and past patterns don't predict future prices. You make every trade yourself.

---

## What it does

| Signal | What it looks at | Default weight |
|---|---|---|
| **Technical analysis** | Trend (SMA/EMA 20/50/200), momentum (RSI, MACD, Stochastic), volatility (Bollinger, ATR), volume (OBV, volume spikes) | 25% |
| **Chart patterns** | Support/resistance, breakouts, golden/death cross, higher-highs/lower-lows, gaps, simple pattern detection (double top/bottom, head & shoulders) | 10% |
| **Fundamentals** | P/E (trailing/forward), EPS consensus & surprises, free-cash-flow yield, ROIC, margins trend, debt/equity, vs. sector peers | 20% |
| **Analyst consensus** | Rating distribution (strong buy → sell), consensus price target vs. current price, recent upgrades/downgrades, earnings surprises | 20% |
| **Geopolitics & macro** | News and event tone by region and sector (US, Israel, Middle East, China, energy, defense, rates), with extra weight on Israel-specific risk for TASE stocks | 12.5% |
| **Market sentiment** | CNN Fear & Greed Index, VIX, and a TASE sentiment proxy (TA-35 vs. its moving averages, USD/ILS) | 12.5% |

Each signal returns a score from **-100 (strong sell) to +100 (strong buy)** with a short explanation. The weighted total goes through the **risk engine** before it becomes a recommendation.

### Risk engine (user-controlled)

You set these in the app's settings:

- **Risk profile**: Conservative / Balanced / Aggressive. This sets the score thresholds for acting.
- **Max position size**: the most any single stock can be of the portfolio (e.g. 10%).
- **Max sector / country exposure**: e.g. no more than 30% in tech, or no more than 50% in Israel.
- **Stop-loss / take-profit rules**: a fixed %, or based on ATR.
- **Max drawdown tolerance**: when the portfolio falls past this, the app turns defensive.
- **Volatility cap**: skip or down-weight buys whose beta or volatility is above your limit.
- **Blacklist / whitelist**: tickers or sectors you never want suggested.

A recommendation is shown only if it passes every rule. When a rule blocks a strong signal, the app tells you so (for example: "Strong buy signal on NVDA, blocked because tech exposure is already 34% and your cap is 30%").

### Main page: My Portfolio

The first screen after you log in:

1. **Live header**: total value in ₪ and $, today's P&L, and P&L **since you started using the app**. It updates automatically (about every 5 minutes) and shows when it was last updated and whether TASE / US markets are open.
2. **P&L strip**:
   - this week's and this month's profit/loss
   - small weekly and monthly bar charts
   - a since-start line compared with the S&P 500 and TA-125

   Tap it for the full Performance page.
3. **Three buttons:**
   - **Review my portfolio**: the full portfolio review, with a stop-loss / take-profit table and the total risk.
   - **Suggest new stocks**: new buy ideas (see below).
   - **Analyze a stock**: an analysis of any ticker.
4. **Your holdings**: price, day change, P&L, weight, the current verdict, and the stop/TP status (set / close to being hit / missing / needs holding period). **Tap any holding** for its stop-loss and take-profit recommendations.

You can switch between portfolios or see all of them combined. On a phone, the three buttons stay pinned at the bottom.

### Suggest new stocks

The app scans the S&P 500, NASDAQ-100, TA-125, main US and TASE ETFs and the top cryptocurrencies, and scores each one with the same six signals.

- **The app asks you each time; nothing is assumed:**
  - how much to invest (₪/$)
  - holding period
  - risk filter
  - which markets and asset types
- **You get the top 5–10 ideas.** Each shows:
  - verdict, confidence and reasons
  - a suggested amount within your budget and risk rules
  - an entry zone, stop-loss and take-profits for your holding period
  - **Why?**, **Analyze** and **Add to watchlist / portfolio** buttons
- **Diversification-aware:** ideas that fill gaps in your portfolio rank higher, and ideas that would break your sector or country limits are left out.

### "Why?" on every suggestion

Every suggestion the app makes has a **Why?** button. That includes buy/sell verdicts, new buy ideas, stop-losses, take-profits, stop raises, "reduce size" advice and review items. Tapping it shows:

1. a plain-language explanation (Hebrew or English)
2. each signal's score and weight, and the exact data behind it (e.g. "RSI 28 on 2 Oct", "3% above support at ₪41.2", "32 analysts, mean target $185")
3. the chart with the relevant levels and patterns highlighted
4. which of your risk rules were applied and what they changed
5. the main risks: what would make this suggestion wrong
6. the data sources and when they were last updated

Telegram messages have a **Why?** button too.

### Push notifications for buy opportunities

When the background scan finds a **new** buy that matches your **Buy alerts** filter, you get a Telegram message and a phone push from the installed app, e.g. "🟢 Buy idea: NICE.TA, confidence 78%, entry ₪…, stop ₪…, target ₪…", with **Why?** and **Analyze** buttons.

You set the Buy alerts filter once in settings:
- minimum confidence
- holding period
- risk filter
- markets
- max alerts per day
- quiet hours

Until you set it, no buy alerts are sent. Each idea is sent once and isn't repeated every refresh.

### The app

- **Dashboard**: portfolio value in ILS and USD, P&L, allocation by market, sector and currency, and the risk gauges.
- **Performance (P&L)**, with three views:
  - **Weekly**: a bar chart of profit/loss per week, plus this week so far.
  - **Monthly**: a bar chart per month, plus a calendar heat-map (green or red by month).
  - **Since start**: a cumulative P&L line from **the day you first started using the app** (your first import). Gains made before that aren't counted here; each holding still shows its own profit vs. your purchase price.

  Each view shows:
  - ₪ and $ amounts and %
  - realized vs. unrealized P&L
  - a comparison with the S&P 500 and TA-125 over the same period
  - which stocks contributed most and least
  - the best and worst week/month
  - the maximum drawdown

  It's available per portfolio and as a combined view. Deposits and withdrawals are not counted as profit: returns are time-weighted.
- **Recommendations feed**: ranked buy / sell / trim / add / hold cards. Each card shows its confidence, the signal breakdown and a plain-language reason.
- **Watchlist**: candidates to buy, scored the same way.
- **Market pulse**: the Fear & Greed meter, VIX, TA-35 / S&P 500 / NASDAQ, USD/ILS, and the top geopolitical headlines with their tone.
- **Alerts**: notifications when a recommendation changes or a stop-loss or target is hit.
- **Auto-refresh**: prices every few minutes during market hours, and a full re-analysis on a schedule (see below).

### Analyze a stock

Type any ticker or company name, in English or Hebrew (`NVDA`, `טבע`, `TEVA.TA`, `BTC`), and get a full analysis on demand. The stock doesn't have to be in your portfolio.

- **Your input** (optional):
  - which portfolio to check it against
  - a risk profile just for this stock
  - your own thesis or notes
  - a free-text question, e.g. "is this a good entry before earnings?"
- **What you get**:
  - A buy / hold / sell verdict (add / trim if you already own it) with a confidence level.
  - The breakdown of all six signals with the reasons behind each.
  - An interactive chart with moving averages, Bollinger bands, support/resistance and detected patterns, plus RSI/MACD.
  - Analyst consensus and price targets, recent insider buys and sells, and the latest news with its tone.
  - **Portfolio fit**: the largest position your risk rules allow, your sector/country exposure after buying, and a suggested entry, stop-loss and target.
  - An AI-written answer to your question, saved with your notes.
- **Actions**: add to watchlist, add to portfolio, or set a Telegram alert. In Telegram, `/analyze TICKER` returns a short version.

#### How the analysis is made: an "Investment Committee"

Instead of asking one AI "buy or sell?", the analysis is split into specialised roles. Each role hands a strict, validated data structure to the next ([design](docs/analysis-committee.md)):

1. **Data Scout** (code): price, P/E, EPS consensus, free-cash-flow yield, ROIC, margins, analyst targets, insiders, earnings date.
2. **Chartist** (code): RSI, MA 20/50/200, MACD, Bollinger, ATR, support/resistance, patterns. Every number is pre-computed; the AI never does the maths.
3. **News & Macro analyst** (AI): summarises news and filings from primary sources (SEC, Reuters, Globes, MAYA…), with every claim linked to its source.
4. **The Bear** (AI): its only job is to find reasons **not** to buy, such as overvaluation, falling margins, debt, regulation or geopolitics.
5. **CIO** (AI): weighs the evidence against the Bear's case and gives the final structured verdict. It can move the score by at most ±15 points and must answer every Bear point. Your risk filter is applied after that.

The **Why?** button shows the whole committee: each report, the Bear's case, and how the CIO answered it.

Before any verdict goes live, the committee is checked in three ways:
- an **evaluation suite**: 15+ tricky scenarios such as crashes, mixed earnings and missing data, each run 3–5 times
- **paper trading**: a simulated portfolio follows its calls
- the historical backtest

### Stop-loss & take-profit recommendations (My Portfolio)

There are two ways to get them, both from the **My Portfolio** page:

- **One stock**: tap a holding to open its detail page. The **Exit levels** panel is at the top.
- **Full portfolio review**: a **"Review whole portfolio"** button checks every holding in one pass. You get a sortable table:

  | Stock | Price | Your cost | Suggested stop | TP1 / TP2 | Risk to stop (₪/$, % of portfolio) | Action |
  |---|---|---|---|---|---|---|

  Above the table are portfolio-level totals:
  - the total amount at risk if every stop is hit
  - the biggest risk contributors
  - positions with no stop
  - stops that are too tight or too loose for the chosen horizon

  From the table you can **accept all**, accept selected rows, or edit any row. The review also runs automatically every week (see below).

#### Weekly review in Telegram (on by default)

- **When:** **Sunday at 20:00 Israel time** by default, and you can change it in settings. Since January 2026 both TASE and the US markets are closed on Sunday, so by then the week's prices are final, the weekend's news is known, and you have the evening to decide before both markets open on Monday.
- **Contents:**
  - last week's P&L
  - stops and take-profits that were hit or are close
  - suggested stop raises (trailing)
  - new sell / trim warnings
  - holdings that still have no holding period
  - total portfolio risk vs. your limit
  - **new buy ideas**: the top 3, using your last-used amount, holding period and risk filter. If you haven't set them yet, the bot asks you first.
  - a link to the full review in the app
- **Urgent alerts don't wait for Sunday:** a stop hit, or a sharp move, still triggers an immediate Telegram message.

#### Filters (apply to both modes)

The panel and the review share a filter bar. Changing a filter recalculates the levels immediately.

**1. Holding period.** Levels are calculated for how long you plan to hold:

| Horizon | Chart used for levels | Stop distance (typical) | Take-profit sources |
|---|---|---|---|
| **1 week** | Daily / 4h, ATR(14) daily | ~1–1.5× ATR, nearest minor support | Next resistance, 1.5R–2R |
| **1 month** | Daily, ATR(14) | ~2× ATR, swing low / SMA 20 | Daily resistance, 2R, upper Bollinger |
| **3 months** | Daily + weekly | ~2.5–3× ATR, SMA 50 / major support | Weekly resistance, 2R–3R, analyst mean target |
| **6 months** | Weekly, weekly ATR | ~2× weekly ATR, SMA 100 / weekly swing low | Analyst mean/high target, 3R, Fibonacci extensions |
| **1 year+** | Weekly / monthly | Below SMA 200 / major multi-month support | Analyst high target, long-term resistance, trailing only |

**There is no default holding period. The app always asks.** When you add a holding (or confirm it from a screenshot), the app asks "How long do you plan to hold this?". No stop-loss or take-profit is calculated until you answer. In the full portfolio review, holdings without an answer are listed first with a quick picker. Each holding keeps its own answer, e.g. a long-term core position next to a 1-month trade, and you can change it at any time. A filter can override all of them at once for a "what if" view.

**2. Risk level.** You choose how much risk you accept, and there are several ways to set it:
- **Presets**: Very Conservative / Conservative / Balanced / Balanced-Aggressive / Aggressive / Very Aggressive.
- **Max loss per position**: a % of the position, e.g. 5%, 8%, 12%, 20% or custom.
- **Max loss per position as % of the whole portfolio**: e.g. 0.5%, 1%, 2% or custom. This is the classic "risk 1% per trade" rule.
- **Max total portfolio risk**: the sum of all distances to the stops, e.g. 5%, 10% or 15%.
- **Minimum risk/reward**: take-profits must be at least 1.5R, 2R or 3R.
- **Stop type**: fixed, trailing, or both.

Presets fill in the detailed options, and you can change any of them. You can save a combination as a named filter, e.g. "My swing setup".

The selected horizon and risk filter are applied together. If a stop that fits the chart is wider than your risk filter allows, the app doesn't silently tighten it. It tells you to **reduce the position size** and shows by how much. Example: "a stop at the 6-month support is 14% away; your 8% limit means holding ~57% of the current shares".

#### What each suggestion shows

- **Suggested stop-loss**, picked for the chosen horizon and risk from these candidates:
  - ATR-based (wider for riskier settings, longer horizons and crypto)
  - just below the nearest support or swing low on the horizon's chart
  - below the moving average that matches the horizon
  - the max-loss limit from your risk filter
- **Trailing stop**: once the position is in profit, the app suggests raising the stop, and it never suggests lowering it. It also tells you when to move the stop to breakeven.
- **Suggested take-profits**, picked from:
  - resistance levels on the horizon's chart
  - analyst price targets (for 3+ months)
  - R-multiples (multiples of the distance to the stop) that meet your minimum risk/reward
  - breakout and Fibonacci extensions
- **Scale-out plan**: e.g. sell ⅓ at TP1, ⅓ at TP2, and trail the rest.
- **For each level**: the price, its distance from the current price, your P&L at that level in ILS and USD, its risk/reward ratio, and a one-line reason.
- **Market context**: stops tighten during Extreme Greed or sharp geopolitical risk, and the app warns you before earnings or ex-dividend dates that fall within the horizon.
- **Accept or edit** the levels to save them together with the horizon and risk settings used. Saved levels drive Telegram alerts ("TEVA.TA hit your stop ₪41.0") and update automatically when trailing.

---

## Decisions so far

| Topic | Choice |
|---|---|
| App | Web app that works on phones and can be installed to the home screen (PWA); Hebrew + English with right-to-left layout |
| Users | You plus family/friends, each with their own login and multiple portfolios, plus a combined view |
| Hosting | **Free**: Oracle Cloud Always Free (ARM VM), running 24/7 |
| Alerts | Telegram bot |
| Holdings input | **Broker screenshots**, read by AI (Gemini free tier; Tesseract offline as a fallback). You always review the result before it's saved. |
| Horizon | Swing trading (weeks to months) |
| Risk | A **risk filter** with presets (Very Conservative → Very Aggressive, default Balanced-Aggressive) and detailed options (max loss per position, % of portfolio per trade, total portfolio risk, min risk/reward). Set per portfolio, changeable per stock. |
| Exit levels | Based on **holding period** (1 week / 1 month / 3 months / 6 months / 1 year+), for **one stock or a full portfolio review** |
| Assets | US + TASE stocks, ETFs, crypto |
| Analysts | Wall Street consensus + insider trading (SEC Form 4 for US; free best-effort MAYA scraping for TASE) |
| AI text | Free LLM tier (Gemini / Groq), with fixed-template explanations as a fallback |
| Refresh | About every 5 minutes, free data |
| Holding period | **No default**: the app asks for each holding before suggesting exit levels |
| Weekly review | On by default, Telegram, **Sunday 20:00 Israel time** (configurable) |
| P&L view | Weekly, monthly and since start, compared with S&P 500 / TA-125 |
| Main page | My Portfolio: live value + P&L, weekly/monthly strip, buttons for **Review**, **Suggest new stocks** and **Analyze a stock**; tap a holding for stop/TP |
| New buys | Screener across US + TASE + ETFs + crypto; asks amount, holding period, risk and markets every time |
| Explanations | A **Why?** button on every suggestion |
| Buy alerts | Telegram + phone push when a new buy matches your Buy alerts filter (no alerts until you set it) |
| Tax | Not considered |
| Backtesting | **Required before launch**: recommendations stay hidden until the scoring has been tested on historical data |
| Paper trading | **Goal-based gate**: ≥4 weeks with no errors, ≥50 calls finished their 1-month window, and beating the S&P 500 / TA-125 (about 6–10 weeks). The live track record is shown meanwhile. |

---

## Data sources (free first)

| Need | Free source | Paid upgrade if needed |
|---|---|---|
| Prices, history, US + TASE (`.TA` suffix) | `yfinance` (Yahoo Finance) | Polygon.io, EODHD (good TASE coverage), Twelve Data |
| Technical indicators | Computed locally (in-house `signals/indicators.py`; pandas-ta is abandoned upstream) | — |
| Fundamentals, SEC filings, earnings-call transcripts | `defeatbeta-api` (free, no key, no rate limits), SEC EDGAR | FMP, EODHD |
| Analyst ratings & price targets | `yfinance` recommendations/targets, Finnhub free tier | Finnhub premium, FMP, TipRanks (no official public API) |
| Fear & Greed | CNN Fear & Greed endpoint (unofficial) + VIX from Yahoo | — |
| Geopolitics & news | GDELT (free, global events + tone), RSS feeds (Reuters, Globes, Calcalist, TheMarker, Times of Israel business), Finnhub news | NewsAPI, Marketaux |
| Macro | FRED (free API key), Bank of Israel series | — |
| Narrative summaries (optional) | — | Claude API for summarizing news and writing recommendation explanations |

The system runs fully on free sources. Paid APIs plug in through the same provider interface and are turned on by setting an API key.

**TASE caveats:** Yahoo quotes `.TA` tickers in **agorot** (1/100 ILS), so the app normalizes them to ILS. Since January 2026 TASE trades **Monday to Friday**, like US markets. Analyst coverage of Israeli small/mid caps is thin, so for those the analyst signal's weight is automatically shifted to the other signals.

---

## Architecture

```
┌──────────────┐   ┌──────────────────┐   ┌───────────────┐   ┌──────────────┐
│ Data         │──▶│ Signal engines   │──▶│ Scoring +     │──▶│ API (FastAPI)│──▶ Web app (Next.js)
│ providers    │   │ technical, chart,│   │ risk engine   │   │ + WebSocket  │──▶ Alerts (Telegram / push / email)
│ (pluggable)  │   │ analyst, geo,    │   │               │   │              │
└──────────────┘   │ sentiment        │   └───────────────┘   └──────────────┘
        ▲          └──────────────────┘           │
        │                                         ▼
   Scheduler (APScheduler) ────────────────▶ Storage (SQLite → Postgres)
```

### Refresh schedule (defaults)

| Job | When |
|---|---|
| Prices / quotes | Every 5 min during US or TASE hours |
| Technical + chart signals | Every 15 min during market hours, plus at the close |
| Fear & Greed, VIX | Every 30 min |
| News / geopolitics | Every 30 min, 24/7 |
| Analyst ratings | Daily, before the open |
| Full portfolio re-scoring | After every signal update; alerts fire only when a recommendation changes |

---

## Planned project layout

```
portfolio-manager/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routes + websocket
│   │   ├── providers/      # data sources (yfinance, finnhub, gdelt, cnn_fng, fred, rss)
│   │   ├── signals/        # technical.py, patterns.py, analysts.py, geopolitics.py, sentiment.py
│   │   ├── scoring/        # weighting, risk engine, recommendation builder
│   │   ├── portfolio/      # holdings, transactions, FX, P&L
│   │   ├── scheduler/      # refresh jobs
│   │   └── models/         # DB models (SQLModel)
│   └── tests/
├── frontend/               # Next.js + Tailwind + TradingView lightweight-charts
├── docs/
├── CLAUDE.md
└── README.md
```

## Getting started (planned)

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # optional API keys
uvicorn app.main:app --reload

# frontend
cd frontend
npm install
npm run dev                   # http://localhost:3000
```

### Optional API keys (`.env`)

```
FINNHUB_API_KEY=       # free tier: analyst ratings + news
FRED_API_KEY=          # free: macro data
GEMINI_API_KEY=        # free tier: screenshot reading + AI explanations
GROQ_API_KEY=          # free tier: fallback LLM
TELEGRAM_BOT_TOKEN=    # alerts, weekly review, buy ideas
VAPID_PUBLIC_KEY=      # web push to the installed app (generate for free)
VAPID_PRIVATE_KEY=
SECRET_KEY=            # session signing
```

---

## Roadmap

1. **Core + main page shell**: logins and portfolios, screenshot import with a review screen, US + TASE + crypto prices, the My Portfolio main page with live value and P&L, technical and chart signals, risk filters.
2. **Analyze a stock**, **Exit levels** (one stock + full portfolio review), and **Why?** explanations.
3. **Suggest new stocks** (screener) + analyst consensus, insider trading, Fear & Greed (stocks + crypto), VIX.
4. Geopolitical/news signal (GDELT + RSS, Hebrew + English) and free-LLM summaries.
5. Performance page (weekly / monthly / since start), weekly Telegram review, buy-idea push notifications.
6. **Backtesting** of the scoring and the exit levels. This must pass before live recommendations are shown.
7. Auto-refresh over WebSocket, PWA install, deployment to a cloud server.

Before each phase, an independent review checks the code built so far and compares the app with similar tools on the web. Reports are saved in `docs/reviews/`.

## License

Private / personal use.

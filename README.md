# Portfolio Manager

A personal stock portfolio analysis and recommendation assistant for the **US** (NYSE / NASDAQ) and **Israeli** (TASE) markets.
It watches your holdings and a watchlist, scores each one from several independent signals, and shows **buy / sell / hold** suggestions in an app that refreshes all day. Every suggestion is checked against the **risk profile you set**.

> ⚠️ **Not financial advice.** This is a decision-support tool for personal use. It can be wrong, data can be delayed or missing, and past patterns don't predict future prices. You make every trade yourself.

---

## What it does

| Signal | What it looks at | Default weight |
|---|---|---|
| **Technical analysis** | Trend (SMA/EMA 20/50/200), momentum (RSI, MACD, Stochastic), volatility (Bollinger, ATR), volume (OBV, volume spikes) | 30% |
| **Chart patterns** | Support/resistance, breakouts, golden/death cross, higher-highs/lower-lows, gaps, simple pattern detection (double top/bottom, head & shoulders) | 15% |
| **Analyst consensus** | Rating distribution (strong buy → sell), consensus price target vs. current price, recent upgrades/downgrades, earnings surprises | 25% |
| **Geopolitics & macro** | News and event tone by region and sector (US, Israel, Middle East, China, energy, defense, rates), with extra weight on Israel-specific risk for TASE stocks | 15% |
| **Market sentiment** | CNN Fear & Greed Index, VIX, and a TASE sentiment proxy (TA-35 vs. its moving averages, USD/ILS) | 15% |

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

### The app

- **Dashboard**: portfolio value in ILS and USD, P&L, allocation by market, sector and currency, and the risk gauges.
- **Recommendations feed**: ranked buy / sell / trim / add / hold cards. Each card shows its confidence, the signal breakdown and a plain-language reason.
- **Watchlist**: candidates to buy, scored the same way.
- **Market pulse**: the Fear & Greed meter, VIX, TA-35 / S&P 500 / NASDAQ, USD/ILS, and the top geopolitical headlines with their tone.
- **Alerts**: notifications when a recommendation changes or a stop-loss or target is hit.
- **Auto-refresh**: prices every few minutes during market hours, and a full re-analysis on a schedule (see below).

---

## Data sources (free first)

| Need | Free source | Paid upgrade if needed |
|---|---|---|
| Prices, history, US + TASE (`.TA` suffix) | `yfinance` (Yahoo Finance) | Polygon.io, EODHD (good TASE coverage), Twelve Data |
| Technical indicators | Computed locally (`pandas-ta` / `ta`) | — |
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
ANTHROPIC_API_KEY=     # optional: AI-written summaries
TELEGRAM_BOT_TOKEN=    # optional: alerts to your phone
```

---

## Roadmap

1. **MVP**: portfolio import (manual / CSV), yfinance prices for US + TASE, technical signals, risk settings, recommendations dashboard.
2. Analyst consensus + Fear & Greed + VIX.
3. Geopolitical/news signal (GDELT + RSS, Hebrew + English sources).
4. Alerts (Telegram / web push), auto-refresh over WebSocket.
5. Backtesting the scoring model on history, then tuning the weights.
6. Optional: broker import (Interactive Brokers, Israeli brokers via CSV), tax-aware sell suggestions (Israeli 25% capital gains tax).

## License

Private / personal use.

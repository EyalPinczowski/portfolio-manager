"""API server for the real-backend Playwright pass (`frontend/e2e/real/`). Never used in production.

It runs the normal app (`create_app`) on a throwaway SQLite database with fixed, offline market
data, so the browser test is deterministic and makes no outside calls:

* quotes: only the symbols in `QUOTES` have a price; everything else has none (the null-price path);
* history: a fixed daily series for the symbols in `QUOTES`, nothing for the rest (`no_levels`);
* funds, dividends and OCR keep their defaults but the test never reaches them.

The caller sets the environment (DATABASE_URL, ENV=dev, COOKIE_SECURE=false, TURNSTILE_*, ...),
see `frontend/e2e/serve-real.mjs`. Before serving, it creates the admin from E2E_ADMIN_EMAIL /
E2E_ADMIN_PASSWORD so the test can sign in.

    uv run python scripts/e2e_server.py --port 8765
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd
import uvicorn

from app.cli import create_admin
from app.main import create_app
from app.providers.base import Quote
from app.providers.registry import Providers, set_providers
from app.timeutil import utcnow

# symbol -> (price, currency, day change %)
QUOTES: dict[str, tuple[float, str, float]] = {
    "AAPL": (231.10, "USD", 0.40),
    "ILS=X": (3.07, "ILS", 0.0),
}


class FixedQuotes:
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        now = utcnow()
        return {
            s: Quote(symbol=s, price=p, currency=c, change_pct=ch, as_of=now, source="e2e")
            for s in symbols
            if s in QUOTES
            for (p, c, ch) in [QUOTES[s]]
        }


class FixedHistory:
    """A gently rising, seeded random walk ending at the quoted price (same series every run)."""

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        if symbol not in QUOTES:
            return None
        n = max(days, 260)
        rng = np.random.default_rng(7)
        steps = rng.normal(0.0004, 0.012, n)
        path = np.exp(np.cumsum(steps))
        close = path / path[-1] * QUOTES[symbol][0]
        idx = pd.bdate_range(end=pd.Timestamp(utcnow().date()), periods=n)
        return pd.DataFrame(
            {
                "Open": close * 0.998,
                "High": close * 1.01,
                "Low": close * 0.99,
                "Close": close,
                "Volume": 1_000_000.0,
            },
            index=idx,
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    create_admin(os.environ["E2E_ADMIN_EMAIL"], os.environ["E2E_ADMIN_PASSWORD"])
    set_providers(Providers(quotes=FixedQuotes(), history=FixedHistory()))
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

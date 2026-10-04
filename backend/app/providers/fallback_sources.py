"""Free fallback price sources behind the existing provider interfaces (block 2.1).

Each source is key-gated: without its key (`enabled` is False) it is skipped and never an error.
Every source has its own TTL cache, a per-minute call budget and a circuit breaker that opens after
a 403/429, all from `Settings.quote_source_limits`. The `httpx.Client` is injectable so tests use a
`MockTransport` with fixed fixtures. Keys travel in headers where the vendor allows it and are never
logged (the httpx logger is silenced in `logging_setup`). Errors carry the HTTP status only.

Units: Finnhub, CoinGecko and Stooq price in USD for the symbols we ask about. The Yahoo-style
symbol (`BRK-B`, `BTC-USD`, `ILS=X`) is what the rest of the app sees; the vendor symbol stays
inside this module. None of these sources serves TASE.
"""

from __future__ import annotations

import io
import logging
import time
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime
from datetime import time as dtime
from typing import Any, ClassVar

import httpx
import pandas as pd

from app.config import QuoteSourceLimits, Settings, get_settings
from app.providers.base import Market, Quote, market_of_symbol
from app.providers.cache import TTLCache

log = logging.getLogger(__name__)


class SourceBlockedError(RuntimeError):
    """The vendor answered 403/429: back off, then let the chain move on."""


class SourceError(RuntimeError):
    """Any other failure (transport, bad payload). Carries no URL, key or body."""


class _Budget:
    """At most `per_minute` calls in any 60 seconds, plus a breaker that a 403/429 opens."""

    def __init__(self, limits: QuoteSourceLimits, clock: Callable[[], float]) -> None:
        self.limits = limits
        self._clock = clock
        self._calls: deque[float] = deque()
        self._open_until = 0.0

    def breaker_open(self) -> bool:
        return self._clock() < self._open_until

    def trip(self) -> None:
        self._open_until = self._clock() + self.limits.breaker_cooldown_seconds

    def take(self, n: int = 1) -> bool:
        now = self._clock()
        while self._calls and now - self._calls[0] >= 60.0:
            self._calls.popleft()
        if self.breaker_open() or len(self._calls) + n > self.limits.max_calls_per_minute:
            return False
        self._calls.extend([now] * n)
        return True


class FallbackSource:
    """Shared plumbing: key gate, HTTP with a budget, per-symbol TTL cache."""

    name: ClassVar[str] = ""
    markets: ClassVar[frozenset[Market]] = frozenset()
    key_attr: ClassVar[str | None] = None

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._clock = clock
        self._now = now or (lambda: datetime.now(UTC).replace(tzinfo=None))
        self.limits = self.settings.quote_source_limits.get(self.name, QuoteSourceLimits())
        self.budget = _Budget(self.limits, clock)
        self._cache: TTLCache[Quote] = TTLCache(self.limits.ttl_seconds, clock, max_entries=2000)

    # -- gate ------------------------------------------------------------------------------
    @property
    def api_key(self) -> str | None:
        key = getattr(self.settings, self.key_attr) if self.key_attr else "-"
        return key or None

    @property
    def enabled(self) -> bool:
        return self.api_key is not None

    def covers(self, symbol: str) -> bool:
        return market_of_symbol(symbol) in self.markets and not symbol.startswith("^")

    def available(self) -> bool:
        return self.enabled and not self.budget.breaker_open()

    # -- http ------------------------------------------------------------------------------
    @property
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.limits.timeout_seconds)
        return self._client

    def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if not self.budget.take():
            raise SourceBlockedError(f"{self.name}: call budget or breaker")
        try:
            resp = self._http.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise SourceError(f"{self.name}: transport error ({type(exc).__name__})") from None
        if resp.status_code in (403, 429):
            self.budget.trip()
            raise SourceBlockedError(f"{self.name}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise SourceError(f"{self.name}: HTTP {resp.status_code}")
        return resp

    # -- quotes (subclasses implement `_fetch`) ---------------------------------------------
    def _fetch(self, symbols: list[str]) -> dict[str, Quote]:
        raise NotImplementedError

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Latest quotes for the symbols this source covers. Never raises."""
        if not self.available():
            return {}
        wanted = [s for s in dict.fromkeys(symbols) if self.covers(s)]
        out: dict[str, Quote] = {}
        todo: list[str] = []
        for sym in wanted:
            hit = self._cache.get(sym)
            if hit is not None:
                out[sym] = hit
            else:
                todo.append(sym)
        if todo:
            try:
                fresh = self._fetch(todo)
            except (SourceBlockedError, SourceError) as exc:
                log.warning("%s", exc)
                fresh = {}
            except Exception as exc:  # a bad payload must never break a quote cycle
                log.warning("%s: unreadable answer (%s)", self.name, type(exc).__name__)
                fresh = {}
            for sym, q in fresh.items():
                self._cache.set(sym, q)
                out[sym] = q
        return out


def _utc_naive(ts: float | int) -> datetime:
    return datetime.fromtimestamp(float(ts), UTC).replace(tzinfo=None)


def _positive(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 and v == v and v != float("inf") else None


class FinnhubQuoteProvider(FallbackSource):
    """Finnhub `/quote` (free: 60/min, US stocks and ETFs, USD, no candles on the free tier)."""

    name = "finnhub"
    markets = frozenset({"US"})
    key_attr = "finnhub_api_key"

    @staticmethod
    def vendor_symbol(symbol: str) -> str:
        return symbol.upper().replace("-", ".")  # Yahoo BRK-B -> Finnhub BRK.B

    def _fetch(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for sym in symbols:
            try:
                resp = self._get(
                    f"{self.settings.finnhub_base_url}/quote",
                    params={"symbol": self.vendor_symbol(sym)},
                    headers={"X-Finnhub-Token": self.api_key or ""},
                )
            except SourceBlockedError:
                if out:
                    break  # keep what we have; the breaker (or budget) is now closed to us
                raise
            q = self.parse(sym, resp.json())
            if q is not None:
                out[sym] = q
        return out

    def parse(self, symbol: str, data: Any) -> Quote | None:
        if not isinstance(data, dict):
            return None
        price, ts = _positive(data.get("c")), data.get("t")
        if price is None or not ts:  # unknown symbols come back as all zeros
            return None
        pct = data.get("dp")
        return Quote(
            symbol=symbol,
            price=price,
            currency="USD",
            change_pct=float(pct) if isinstance(pct, int | float) else None,
            as_of=_utc_naive(ts),
            source=self.name,
            basis="live",
        )


class CoinGeckoQuoteProvider(FallbackSource):
    """CoinGecko demo key: one batched `simple/price` call for all coins."""

    name = "coingecko"
    markets = frozenset({"CRYPTO"})
    key_attr = "coingecko_api_key"

    def covers(self, symbol: str) -> bool:
        return symbol.upper() in self.settings.coingecko_ids

    def _fetch(self, symbols: list[str]) -> dict[str, Quote]:
        ids = {self.settings.coingecko_ids[s.upper()]: s for s in symbols}
        resp = self._get(
            f"{self.settings.coingecko_base_url}/simple/price",
            params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_last_updated_at": "true",
            },
            headers={"x-cg-demo-api-key": self.api_key or ""},
        )
        return self.parse(ids, resp.json())

    def parse(self, ids: dict[str, str], data: Any) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        if not isinstance(data, dict):
            return out
        for coin, sym in ids.items():
            row = data.get(coin)
            if not isinstance(row, dict):
                continue
            price = _positive(row.get("usd"))
            ts = row.get("last_updated_at")
            if price is None or not ts:
                continue
            pct = row.get("usd_24h_change")
            out[sym] = Quote(
                symbol=sym,
                price=price,
                currency="USD",
                change_pct=float(pct) if isinstance(pct, int | float) else None,
                as_of=_utc_naive(ts),
                source=self.name,
                basis="live",
            )
        return out


# ---------------------------------------------------------------- daily bars (history + last close)
def parse_daily_csv(text: str) -> pd.DataFrame | None:
    """Stooq daily CSV -> OHLCV frame. Anything that is not a CSV with a Date header is a failure
    (Stooq answers HTTP 200 with plain error text when the key or quota is wrong)."""
    body = text.strip()
    first = body.splitlines()[0].lower() if body else ""
    if not first.startswith("date,open,high,low,close"):
        return None
    try:
        df = pd.read_csv(io.StringIO(body))
    except Exception:
        return None
    df.columns = [str(c).strip().capitalize() for c in df.columns]
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).set_index("Date").sort_index()
    return None if df.empty else df[["Open", "High", "Low", "Close", "Volume"]]


def _last_close_quote(symbol: str, df: pd.DataFrame, source: str) -> Quote | None:
    close = _positive(df["Close"].iloc[-1])
    if close is None:
        return None
    prev = _positive(df["Close"].iloc[-2]) if len(df) > 1 else None
    day = pd.Timestamp(df.index[-1]).date()
    return Quote(
        symbol=symbol,
        price=close,
        currency="USD",
        change_pct=(close / prev - 1.0) * 100.0 if prev else None,
        as_of=datetime.combine(day, dtime(21, 0)),  # US close, UTC-naive (date is what matters)
        source=source,
        basis="last_close",
    )


class _DailyBarsSource(FallbackSource):
    """History cache and last-close quote on top of a daily-bars endpoint."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._bars: TTLCache[pd.DataFrame] = TTLCache(
            self.limits.ttl_seconds,
            self._clock,
            max_entries=self.settings.history_cache_max_entries,
        )

    def _fetch_bars(self, symbol: str, days: int) -> pd.DataFrame | None:
        raise NotImplementedError

    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        if not self.available() or not self.covers(symbol):
            return None
        key = f"{symbol}:{days}"
        hit = self._bars.get(key)
        if hit is not None:
            return hit
        try:
            df = self._fetch_bars(symbol, days)
        except (SourceBlockedError, SourceError) as exc:
            log.warning("%s", exc)
            return None
        except Exception as exc:
            log.warning("%s: unreadable answer (%s)", self.name, type(exc).__name__)
            return None
        if df is None or df.empty:
            return None
        df.attrs["source"] = self.name
        self._bars.set(key, df)
        return df

    def _fetch(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for sym in symbols:
            df = self._fetch_bars(sym, 10)
            q = _last_close_quote(sym, df, self.name) if df is not None else None
            if q is not None:
                out[sym] = q
        return out


class StooqProvider(_DailyBarsSource):
    """Stooq daily CSV (US `.us`). Needs an apikey since 2026; optional third source."""

    name = "stooq"
    markets = frozenset({"US"})
    key_attr = "stooq_api_key"

    @staticmethod
    def vendor_symbol(symbol: str) -> str:
        return symbol.lower().replace(".", "-") + ".us"

    def _fetch_bars(self, symbol: str, days: int) -> pd.DataFrame | None:
        resp = self._get(
            f"{self.settings.stooq_base_url}/q/d/l/",
            params={"s": self.vendor_symbol(symbol), "i": "d", "apikey": self.api_key},
        )
        df = parse_daily_csv(resp.text)
        return None if df is None else df.tail(max(days, 1))


class FmpHistoryProvider(_DailyBarsSource):
    """Financial Modeling Prep free end-of-day bars (US only, 250 calls/day)."""

    name = "fmp"
    markets = frozenset({"US"})
    key_attr = "fmp_api_key"

    def _fetch_bars(self, symbol: str, days: int) -> pd.DataFrame | None:
        start = self._now().date() - pd.Timedelta(days=int(days * 1.6) + 5)
        resp = self._get(
            f"{self.settings.fmp_base_url}/stable/historical-price-eod/full",
            params={
                "symbol": symbol.upper(),
                "from": start.isoformat(),
                "apikey": self.api_key,
            },
        )
        return self.parse_bars(resp.json())

    @staticmethod
    def parse_bars(data: Any) -> pd.DataFrame | None:
        rows = data.get("historical") if isinstance(data, dict) else data  # legacy v3 wrapper
        if not isinstance(rows, list) or not rows:
            return None
        df = pd.DataFrame(rows)
        need = {"date", "open", "high", "low", "close"}
        if not need <= set(df.columns):
            return None
        df = df.rename(columns=str.capitalize)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        if "Volume" not in df.columns:
            df["Volume"] = 0.0
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Date", "Close"]).set_index("Date").sort_index()
        return None if df.empty else df[["Open", "High", "Low", "Close", "Volume"]]

    def _fetch(self, symbols: list[str]) -> dict[str, Quote]:
        return {}  # FMP is history only: its last bar is not offered as a quote


# ---------------------------------------------------------------- USD/ILS daily reference rates
class _FxSource(FallbackSource):
    """USD/ILS daily reference rate, reported as the quote of `Settings.fx_symbol` (basis
    `last_close`, dated): a daily rate is never labelled live."""

    markets = frozenset({"US"})  # the chain routes the FX symbol itself; `covers` is overridden

    def covers(self, symbol: str) -> bool:
        return symbol == self.settings.fx_symbol

    def _quote(self, rate: Any, day: date) -> Quote | None:
        lo, hi = self.settings.fx_plausible_range
        price = _positive(rate)
        if price is None or not lo <= price <= hi:
            return None
        return Quote(
            symbol=self.settings.fx_symbol,
            price=price,
            currency="ILS",
            as_of=datetime.combine(day, dtime(15, 0)),
            source=self.name,
            basis="last_close",
        )


class FrankfurterFxProvider(_FxSource):
    """Frankfurter (ECB reference rates, no key). `latest?base=USD&symbols=ILS`."""

    name = "frankfurter"

    def _fetch(self, symbols: list[str]) -> dict[str, Quote]:
        resp = self._get(
            f"{self.settings.frankfurter_base_url}/latest",
            params={"base": "USD", "symbols": "ILS"},
        )
        q = self.parse(resp.json())
        return {self.settings.fx_symbol: q} if q else {}

    def parse(self, data: Any) -> Quote | None:
        if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
            return None
        try:
            day = date.fromisoformat(str(data.get("date")))
        except ValueError:
            return None
        return self._quote(data["rates"].get("ILS"), day)


class BoiFxProvider(_FxSource):
    """Bank of Israel representative USD rate (public XML, no key). The element names are
    unverified (the vendor page was unreachable when this was written): the parser accepts the
    known shape tolerantly and the plausibility range rejects a misread number."""

    name = "boi"

    def _fetch(self, symbols: list[str]) -> dict[str, Quote]:
        resp = self._get(self.settings.boi_rates_url)
        q = self.parse(resp.text)
        return {self.settings.fx_symbol: q} if q else {}

    def parse(self, text: str) -> Quote | None:
        root = ET.fromstring(text)
        for node in root.iter():
            kids = {
                c.tag.split("}")[-1].upper().replace("_", ""): (c.text or "").strip() for c in node
            }
            code = kids.get("KEY") or kids.get("CURRENCYCODE") or kids.get("CURRENCY")
            if code != "USD":
                continue
            rate = kids.get("CURRENTEXCHANGERATE") or kids.get("RATE")
            stamp = kids.get("LASTUPDATE") or kids.get("DATE") or ""
            try:
                day = datetime.fromisoformat(stamp[:19]).date()
            except ValueError:
                return None
            return self._quote(rate, day)
        return None


QUOTE_SOURCE_CLASSES: dict[str, type[FallbackSource]] = {
    "finnhub": FinnhubQuoteProvider,
    "coingecko": CoinGeckoQuoteProvider,
    "stooq": StooqProvider,
    "fmp": FmpHistoryProvider,
    "frankfurter": FrankfurterFxProvider,
    "boi": BoiFxProvider,
}

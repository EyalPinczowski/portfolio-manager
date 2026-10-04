"""Provider interfaces. Signal code never calls yfinance/requests directly.

Currency normalisation happens here, at the provider layer: Yahoo prices many TASE stocks in
agorot (currency "ILA"). We decide by the reported currency field, never by the ".TA" suffix.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

import pandas as pd
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints, model_validator
from pydantic import Field as PydField

from app.timeutil import as_utc

# Naive datetimes are UTC (the database convention); provider times always carry an offset, so they
# compare with `Explanation.as_of` (also aware) without a TypeError.
UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]
SourceName = Annotated[str, StringConstraints(max_length=120)]

AGOROT_CODES = {"ILA", "ILX"}
OHLC_COLUMNS = ("Open", "High", "Low", "Close")


QuoteBasis = Literal["live", "last_close"]
QUOTE_FLAG_DISAGREEMENT = "price_disagreement"


class Quote(BaseModel):
    symbol: str
    price: float  # normalised (ILA -> ILS)
    currency: str  # normalised
    change_pct: float | None = None
    as_of: datetime  # naive UTC
    source: str = "yfinance"  # which provider produced the price
    # "live": a current market price. "last_close": an end-of-day or daily reference price, shown
    # with its date and never fresh enough for exit levels.
    basis: QuoteBasis = "live"
    flag: str | None = None  # e.g. "price_disagreement" (two live sources > N % apart)


def is_pence(currency: str | None) -> bool:
    """British pence: "GBp" (lower-case p) or "GBX". Upper-case "GBP" is pounds."""
    if currency is None:
        return False
    cur = currency.strip()
    return cur == "GBp" or cur.upper() == "GBX"


def is_minor_unit(currency: str | None) -> bool:
    """Agorot (ILA) or pence (GBp/GBX): the raw price must be divided by 100."""
    if currency is None:
        return False
    return currency.strip().upper() in AGOROT_CODES or is_pence(currency)


def normalize_currency(currency: str | None) -> str | None:
    """ILA (agorot) is reported as ILS and GBp/GBX (pence) as GBP after normalisation."""
    if currency is None:
        return None
    if is_pence(currency):
        return "GBP"
    cur = currency.strip().upper()
    return "ILS" if cur in AGOROT_CODES else cur


def normalize_price(price: float, currency: str | None) -> tuple[float, str | None]:
    """Normalise a raw Yahoo price. Only the currency field decides (never the symbol suffix).

    Indices are quoted in points (currency is usually None/"") and are never divided.
    """
    if is_minor_unit(currency):
        return price / 100.0, normalize_currency(currency)
    return price, normalize_currency(currency)


def normalize_history(df: pd.DataFrame, currency: str | None) -> pd.DataFrame:
    """Scale OHLC columns by 1/100 for agorot/pence-quoted instruments; volume is untouched."""
    if not is_minor_unit(currency):
        return df
    out = df.copy()
    for col in OHLC_COLUMNS:
        if col in out.columns:
            out[col] = out[col] / 100.0
    if "Adj Close" in out.columns:
        out["Adj Close"] = out["Adj Close"] / 100.0
    return out


@runtime_checkable
class QuoteProvider(Protocol):
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """Batched latest quotes. Missing symbols are simply absent from the result."""


@runtime_checkable
class HistoryProvider(Protocol):
    def get_history(self, symbol: str, days: int) -> pd.DataFrame | None:
        """Daily OHLCV (Open High Low Close Volume, DatetimeIndex), normalised. None if unavailable."""


class OcrRow(BaseModel):
    """A raw row as returned by a structured OCR provider (Gemini)."""

    name: str = ""
    symbol: str | None = None
    tase_number: str | None = None
    quantity: float | None = None
    price: float | None = None
    value: float | None = None
    cost: float | None = None
    currency: str | None = None
    unit: str | None = None  # "agorot" | "ILS" | "USD" | None


class OcrResult(BaseModel):
    provider: str
    text: str | None = None  # plain text (Tesseract)
    rows: list[OcrRow] | None = None  # structured rows (Gemini)


class OcrUnavailableError(RuntimeError):
    """The OCR engine is not installed or not configured."""


@runtime_checkable
class OcrProvider(Protocol):
    name: str
    # True if the image is sent to an outside service. Such a provider only ever receives an image
    # whose word-box redaction completed (see importer/service.py). Providers without the
    # attribute are treated as local.

    def extract(self, image_bytes: bytes) -> OcrResult: ...


# ---------------------------------------------------------------- Phase 2 data providers
Market = Literal["US", "TASE", "CRYPTO"]
ALL_MARKETS: frozenset[Market] = frozenset({"US", "TASE", "CRYPTO"})

# Why a field has no value. `coverage` is by design: the provider does not serve that market (for
# example a US-only source asked about a TASE symbol). The others are runtime conditions.
MissingReason = Literal["coverage", "not_found", "unavailable", "rate_limited", "stale"]


def market_of_symbol(symbol: str) -> Market:
    """The market a Yahoo-style symbol belongs to (".TA" suffix: TASE, "-USD": crypto)."""
    sym = symbol.strip().upper()
    if sym.endswith(".TA"):
        return "TASE"
    if sym.endswith("-USD"):
        return "CRYPTO"
    return "US"


class Field[T](BaseModel):
    """One value with its provenance. A value is either present, or missing with a reason.

    Never substitute a neutral default for a missing field: a signal built from missing fields has
    confidence 0 (CLAUDE.md rule 4).
    """

    model_config = ConfigDict(allow_inf_nan=False)

    value: T | None = None
    source: SourceName
    as_of: UtcDatetime | None = None  # aware UTC, when the provider says the value was true
    missing_reason: MissingReason | None = None

    @model_validator(mode="after")
    def _value_xor_reason(self) -> Field[T]:
        if self.value is None and self.missing_reason is None:
            raise ValueError("a Field without a value needs a missing_reason")
        if self.value is not None and self.missing_reason is not None:
            raise ValueError("a Field with a value cannot carry a missing_reason")
        return self

    @property
    def is_missing(self) -> bool:
        return self.value is None

    @classmethod
    def ok(cls, value: T, source: str, as_of: datetime | None = None) -> Field[T]:
        return cls(value=value, source=source, as_of=as_of)

    @classmethod
    def missing(cls, source: str, reason: MissingReason) -> Field[T]:
        return cls(value=None, source=source, missing_reason=reason)


Period = Literal["TTM", "FY"]
_MAJOR_CURRENCY = re.compile(r"[A-Z]{3}")


class FundamentalsSnapshot(BaseModel):
    """Valuation and quality numbers, each with its own source and date.

    `currency` is the ISO code of the monetary fields (market cap, EPS) and `period` says whether
    flow figures are trailing twelve months or a fiscal year. Both are required as soon as any
    value is present: a TASE EPS in agorot next to one in shekels, or a TTM figure compared with a
    fiscal-year one, would be silently wrong. Minor units (ILA, GBX) are refused: normalise first.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    symbol: str
    currency: str | None = None
    period: Period | None = None
    market_cap: Field[float]
    pe_trailing: Field[float]
    pe_forward: Field[float]
    eps_ttm: Field[float]
    revenue_growth_yoy_pct: Field[float]
    gross_margin_pct: Field[float]
    operating_margin_pct: Field[float]
    roic_pct: Field[float]
    fcf_yield_pct: Field[float]
    debt_to_equity: Field[float]

    @model_validator(mode="after")
    def _labelled(self) -> FundamentalsSnapshot:
        if self.currency is not None and not (
            _MAJOR_CURRENCY.fullmatch(self.currency) and not is_minor_unit(self.currency)
        ):
            raise ValueError(
                "currency must be a 3-letter upper-case ISO code in major units (not ILA/GBX)"
            )
        if not self.all_fields_missing:
            if self.currency is None:
                raise ValueError("a fundamentals snapshot with values needs its currency")
            if self.period is None:
                raise ValueError("a fundamentals snapshot with values needs its period (TTM or FY)")
        return self

    @classmethod
    def all_missing(cls, symbol: str, source: str, reason: MissingReason) -> FundamentalsSnapshot:
        return cls(
            symbol=symbol, **{n: Field[float].missing(source, reason) for n in cls._numeric()}
        )

    @classmethod
    def _numeric(cls) -> list[str]:
        return [n for n in cls.model_fields if n not in ("symbol", "currency", "period")]

    @property
    def fields(self) -> dict[str, Field[float]]:
        return {n: getattr(self, n) for n in self._numeric()}

    @property
    def all_fields_missing(self) -> bool:
        return all(f.is_missing for f in self.fields.values())


class NewsItem(BaseModel):
    id: str  # stable id the LLM roles must cite
    headline: str
    url: str = ""
    publisher: str = ""
    published_at: UtcDatetime | None = None
    # When we could first have known about it (point-in-time control for evals).
    available_at: UtcDatetime | None = None


class Transcript(BaseModel):
    symbol: str
    fiscal_year: int
    fiscal_quarter: int = PydField(ge=1, le=4)
    call_date: date | None = None
    text: str


class Filing(BaseModel):
    id: str
    form: str  # 10-K, 10-Q, 8-K, 20-F ...
    filed_at: UtcDatetime | None = None
    title: str = ""
    url: str = ""


class _Covering(Protocol):
    name: str
    markets: frozenset[Market]


def covers(provider: _Covering, symbol: str) -> bool:
    return market_of_symbol(symbol) in provider.markets


@runtime_checkable
class FundamentalsProvider(Protocol):
    name: str
    markets: frozenset[Market]

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        """Never raises for a symbol it does not serve: every field is missing(`coverage`)."""


@runtime_checkable
class NewsProvider(Protocol):
    name: str
    markets: frozenset[Market]

    def get_news(self, symbol: str, limit: int = 20) -> Field[list[NewsItem]]: ...


@runtime_checkable
class TranscriptProvider(Protocol):
    name: str
    markets: frozenset[Market]

    def get_transcripts(self, symbol: str, quarters: int = 4) -> Field[list[Transcript]]: ...


@runtime_checkable
class FilingsProvider(Protocol):
    name: str
    markets: frozenset[Market]

    def get_filings(
        self, symbol: str, forms: tuple[str, ...] = (), limit: int = 20
    ) -> Field[list[Filing]]: ...


# ---------------------------------------------------------------- Israeli funds (GemelNet)
class FundInfo(BaseModel):
    """One Israeli fund (provident, pension, study fund or mutual fund) as a public dataset lists it."""

    model_config = ConfigDict(allow_inf_nan=False)

    fund_id: Annotated[str, StringConstraints(pattern=r"^\d{1,9}$")]
    name: Annotated[str, StringConstraints(max_length=200)]
    classification: Annotated[str, StringConstraints(max_length=200)] | None = None
    managing_corporation: Annotated[str, StringConstraints(max_length=200)] | None = None


class FundMonth(BaseModel):
    """One reporting month. A value the dataset left empty stays None (never a neutral 0)."""

    model_config = ConfigDict(allow_inf_nan=False)

    period: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}$")]  # "YYYY-MM"
    monthly_return_pct: float | None = None
    total_assets: float | None = None  # as the dataset states it (unit not verified)
    management_fee_pct: float | None = None


class FundSeries(BaseModel):
    """A fund with its monthly return series (ascending by month) and its category's last month."""

    model_config = ConfigDict(allow_inf_nan=False)

    info: FundInfo
    months: list[FundMonth]
    # Average of the same-category funds' return for `category_period` (None: not enough peers).
    category_period: str | None = None
    category_avg_monthly_return_pct: float | None = None
    category_peer_count: int = 0


@runtime_checkable
class FundProvider(Protocol):
    name: str
    markets: frozenset[Market]

    def search_funds(self, query: str) -> Field[list[FundInfo]]:
        """Funds whose name or number matches. Missing(`not_found`) when nothing matches."""

    def get_fund(self, fund_id: str) -> Field[FundSeries]:
        """The monthly series of one fund. Never raises: failures become a missing reason."""


# ---------------------------------------------------------------- dividends (corporate actions)
class DividendEvent(BaseModel):
    """One dividend payment (past) or announcement (future). Amounts are per share, major units."""

    model_config = ConfigDict(allow_inf_nan=False)

    ex_date: date
    pay_date: date | None = None  # the free source rarely has it: None means unknown
    amount: float = PydField(gt=0)  # per share, normalised (agorot -> ILS)
    currency: str  # normalised ISO code
    announced: bool = False  # True: a future date the source reports; False: a past payment
    # True: the amount is the last known payment carried forward (the source gave a date only).
    amount_is_estimate: bool = False


class DividendHistory(BaseModel):
    symbol: str
    events: list[DividendEvent]  # ascending by ex_date, past and announced future


@runtime_checkable
class DividendProvider(Protocol):
    name: str
    markets: frozenset[Market]

    def get_dividends(self, symbol: str) -> Field[DividendHistory]:
        """Never raises. Missing(`not_found`): no dividend on record (a non-payer is the same)."""


def describe_missing(what: str, field: Field[Any]) -> str:
    """A human-readable reason for a signal's `reasons` when a field is missing."""
    why = {
        "coverage": f"{field.source} does not cover this market",
        "not_found": f"{field.source} has no record of this symbol",
        "unavailable": f"{field.source} is unavailable right now",
        "rate_limited": f"{field.source} is rate limited right now",
        "stale": f"{field.source} data is too old",
    }.get(field.missing_reason or "", f"{field.source} returned nothing")
    return f"No {what} data: {why}."

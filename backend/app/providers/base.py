"""Provider interfaces. Signal code never calls yfinance/requests directly.

Currency normalisation happens here, at the provider layer: Yahoo prices many TASE stocks in
agorot (currency "ILA"). We decide by the reported currency field, never by the ".TA" suffix.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel

AGOROT_CODES = {"ILA", "ILX"}
OHLC_COLUMNS = ("Open", "High", "Low", "Close")


class Quote(BaseModel):
    symbol: str
    price: float  # normalised (ILA -> ILS)
    currency: str  # normalised
    change_pct: float | None = None
    as_of: datetime  # naive UTC


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

    def extract(self, image_bytes: bytes) -> OcrResult: ...

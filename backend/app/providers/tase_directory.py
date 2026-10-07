"""TASE Data Hub "Securities - Basic": the traded-securities list (TASE number, Hebrew name).

Turns on only when `TASE_API_KEY` is set (`FallbackSource` key gate). One call fetches the whole
list for a trading day; the daily job stores it (`tase_directory` table) so the importer and the
symbol search can find a fund by its TASE number or Hebrew name. It carries no prices.

The field names of the real answer were not known when this was written, so the parser accepts
several spellings, is case-insensitive, and finds the list at the top level or under a key such as
`tradeSecuritiesList`. The first real answer must be checked (docs/reminders.md).

The key travels only in the `apikey` header and is never logged; neither are bodies or headers.
Every failure returns an empty list (a 401/403/429 also opens the breaker).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import BaseModel

from app.importer.parse import TASE_NUMBER_PATTERN
from app.providers.fallback_sources import FallbackSource, SourceBlockedError, SourceError
from app.timeutil import local_today

NUMBER_KEYS = ("securityId", "id", "number", "securityNumber", "IsinOrNumber")
NAME_HE_KEYS = ("name", "hebrewName", "securityName", "nameHe")
NAME_EN_KEYS = ("englishName", "nameEn")
SYMBOL_KEYS = ("symbol", "ticker", "tradingSymbol")
TYPE_KEYS = ("type", "securityType")
LIST_KEYS = ("tradeSecuritiesList", "items", "data", "securities", "results", "list")
MAX_NAME = 200


class DirectoryEntry(BaseModel):
    tase_number: str
    name_he: str = ""
    name_en: str = ""
    trading_symbol: str | None = None
    type: str = ""


def _pick(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for n in names:
        v = lowered.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return " ".join(str(value).split())[:MAX_NAME]


def _find_list(data: Any, depth: int = 0) -> list[Any]:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict) or depth > 2:
        return []
    lowered = {str(k).lower(): v for k, v in data.items()}
    for key in LIST_KEYS:
        v = lowered.get(key.lower())
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _find_list(v, depth + 1)
            if inner:
                return inner
    for v in data.values():  # an unknown key holding a list of objects
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    return []


def parse_directory(data: Any) -> list[DirectoryEntry]:
    """Tolerant parse: unknown shapes give an empty list, a row without a TASE number is skipped."""
    out: dict[str, DirectoryEntry] = {}
    for row in _find_list(data):
        if not isinstance(row, dict):
            continue
        number = re.sub(r"\D", "", _text(_pick(row, NUMBER_KEYS)))
        if not re.fullmatch(TASE_NUMBER_PATTERN, number):
            continue
        entry = DirectoryEntry(
            tase_number=number,
            name_he=_text(_pick(row, NAME_HE_KEYS)),
            name_en=_text(_pick(row, NAME_EN_KEYS)),
            trading_symbol=_text(_pick(row, SYMBOL_KEYS)).upper() or None,
            type=_text(_pick(row, TYPE_KEYS)),
        )
        if entry.name_he or entry.name_en:
            out.setdefault(number, entry)
    return list(out.values())


class TaseDirectory(FallbackSource):
    name = "tase"
    key_attr = "tase_api_key"

    def __init__(
        self, *args: Any, sleep: Callable[[float], None] = time.sleep, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._sleep = sleep
        self._calls = 0

    def _url(self, day: date) -> str:
        base = self.settings.tase_base_url.rstrip("/")
        return f"{base}/v1/basic-securities/trade-securities-list/{day.year}/{day.month}/{day.day}"

    def _pace(self) -> None:
        """At most `tase_requests_per_window` calls per `tase_window_seconds` (10 per 2 s)."""
        if self._calls:
            self._sleep(self.settings.tase_window_seconds / self.settings.tase_requests_per_window)
        self._calls += 1

    def fetch_day(self, day: date) -> list[DirectoryEntry]:
        """The list for one day; raises `SourceBlockedError`/`SourceError` (status only)."""
        self._pace()
        resp = self._get(
            self._url(day),
            headers={
                "accept": "application/json",
                "accept-language": "he-IL",
                "apikey": self.api_key or "",
            },
        )
        if not resp.content:
            return []  # 204 / empty body: no list for that day
        try:
            return parse_directory(resp.json())
        except ValueError:
            raise SourceError("tase: not JSON") from None

    def fetch_latest(self, today: date | None = None) -> list[DirectoryEntry]:
        """Today, then back up to `tase_directory_lookback_days` days, until a day has rows.
        Never raises: no key, a block, a bad key or a transport error all give []."""
        if not self.available():
            return []
        day = today or local_today()
        for back in range(self.settings.tase_directory_lookback_days + 1):
            try:
                rows = self.fetch_day(day - timedelta(days=back))
            except SourceBlockedError:
                return []
            except SourceError as exc:
                if "HTTP 401" in str(exc):
                    self.budget.trip()
                    return []
                if "HTTP 404" in str(exc):
                    continue  # no list for that day
                return []
            except (httpx.HTTPError, ValueError):  # pragma: no cover - _get wraps these
                return []
            if rows:
                return rows
        return []

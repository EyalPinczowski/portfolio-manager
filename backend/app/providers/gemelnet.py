"""GemelNet (Israel Ministry of Finance) through the free data.gov.il CKAN datastore API.

Key-free, personal and non-commercial. The source is monthly: one row per fund per reporting
month. Everything here is label-first: `Field.source` is "gemelnet", `as_of` is the end of the
latest reporting month, and an empty or unreadable answer is a missing reason, never a number.

Verified live on 2026-10-04: the resource id (default in `Settings.gemelnet_resource_ids
["monthly_returns"]`; an empty value makes the lookup answer `unavailable`), the column names in
`Settings.gemelnet_fields`, and the `q`, `filters` and `sort` queries used below. A real answer is
recorded in `tests/fixtures/live_2026_10/`. The
`httpx.Client` is injectable, so tests use a `MockTransport` with the recorded fixture in
`tests/fixtures/gemelnet/`.
"""

from __future__ import annotations

import calendar
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import ValidationError

from app.providers.base import (
    Field,
    FundInfo,
    FundMonth,
    FundSeries,
    Market,
)
from app.providers.cache import TTLCache
from app.providers.fallback_sources import FallbackSource, SourceBlockedError, SourceError

log = logging.getLogger(__name__)

SOURCE = "gemelnet"
RESOURCE_KEY = "monthly_returns"
_ID = re.compile(r"\d{1,9}")


def parse_period(raw: Any) -> str | None:
    """ "YYYY-MM" from 202509, "202509", "2025-09", "2025-09-30T00:00:00" or "09/2025"."""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(int(raw)) if isinstance(raw, float) and raw.is_integer() else str(raw).strip()
    m = re.fullmatch(r"(\d{4})[-/]?(\d{2})(?:[-/T ].*)?", text) or re.fullmatch(
        r"(\d{2})/(\d{4})", text
    )
    if m is None:
        return None
    a, b = m.groups()
    year, month = (b, a) if len(a) == 2 else (a, b)
    return f"{year}-{month}" if 1 <= int(month) <= 12 and 1990 <= int(year) <= 2200 else None


def period_end(period: str) -> datetime:
    year, month = int(period[:4]), int(period[5:7])
    return datetime(year, month, calendar.monthrange(year, month)[1], tzinfo=UTC)


def _number(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        v = float(str(raw).replace(",", "").strip())
    except ValueError:
        return None
    return v if v == v and abs(v) != float("inf") else None


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    t = " ".join(str(raw).split())
    return t or None


def _fund_id(raw: Any) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    text = str(int(raw)) if isinstance(raw, float) and raw.is_integer() else str(raw).strip()
    return text if _ID.fullmatch(text) else None


class GemelNetProvider(FallbackSource):
    """Implements `FundProvider`."""

    name: ClassVar[str] = SOURCE
    markets: ClassVar[frozenset[Market]] = frozenset({"TASE"})
    key_attr: ClassVar[str | None] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        ttl = self.limits.ttl_seconds
        self._search_cache: TTLCache[list[FundInfo]] = TTLCache(ttl, self._clock, max_entries=200)
        self._series_cache: TTLCache[FundSeries] = TTLCache(ttl, self._clock, max_entries=200)

    # -- configuration ---------------------------------------------------------------------
    @property
    def resource_id(self) -> str | None:
        return self.settings.gemelnet_resource_ids.get(RESOURCE_KEY) or None

    def _col(self, key: str) -> str:
        return self.settings.gemelnet_fields[key]

    def _unconfigured(self) -> bool:
        return self.resource_id is None

    # -- http -------------------------------------------------------------------------------
    def _records(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        resp = self._get(
            f"{self.settings.gemelnet_base_url}/datastore_search",
            params={"resource_id": self.resource_id, **params},
        )
        try:
            body = resp.json()
        except ValueError:
            raise SourceError(f"{self.name}: not JSON") from None
        result = body.get("result") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("success") is not True
            or not isinstance(result, dict)
        ):
            raise SourceError(f"{self.name}: unsuccessful answer")
        records = result.get("records")
        if not isinstance(records, list):
            raise SourceError(f"{self.name}: no records list")
        return [r for r in records if isinstance(r, dict)]

    def _failure(self, exc: Exception) -> Field[Any]:
        log.warning("%s", exc)
        if isinstance(exc, SourceBlockedError):
            return Field.missing(SOURCE, "rate_limited")
        return Field.missing(SOURCE, "unavailable")

    # -- parsing ----------------------------------------------------------------------------
    def _info(self, rec: dict[str, Any]) -> FundInfo | None:
        fid = _fund_id(rec.get(self._col("fund_id")))
        name = _text(rec.get(self._col("fund_name")))
        if fid is None or name is None:
            return None
        try:
            return FundInfo(
                fund_id=fid,
                name=name[:200],
                classification=(_text(rec.get(self._col("classification"))) or "")[:200] or None,
                managing_corporation=(_text(rec.get(self._col("managing_corporation"))) or "")[:200]
                or None,
            )
        except ValidationError:
            return None

    def _month(self, rec: dict[str, Any]) -> FundMonth | None:
        period = parse_period(rec.get(self._col("period")))
        if period is None:
            return None
        return FundMonth(
            period=period,
            monthly_return_pct=_number(rec.get(self._col("monthly_yield"))),
            total_assets=_number(rec.get(self._col("total_assets"))),
            management_fee_pct=_number(rec.get(self._col("management_fee"))),
        )

    # -- FundProvider -----------------------------------------------------------------------
    def search_funds(self, query: str) -> Field[list[FundInfo]]:
        q = " ".join(query.split())
        if len(q) < self.settings.gemelnet_search_min_chars:
            return Field.missing(SOURCE, "not_found")
        if self._unconfigured():
            return Field.missing(SOURCE, "unavailable")
        key = q.lower()
        hit = self._search_cache.get(key)
        if hit is not None:
            return Field.ok(hit, SOURCE, None)
        if not self.available():
            return Field.missing(SOURCE, "rate_limited")
        limit = self.settings.gemelnet_search_max_results
        params: dict[str, Any] = {
            "limit": limit * 12,  # one row per fund per month: many rows collapse to one fund
            "sort": f"{self._col('period')} desc",
        }
        if _ID.fullmatch(q):
            params["filters"] = json.dumps({self._col("fund_id"): q})
        else:
            params["q"] = q
        try:
            records = self._records(params)
        except (SourceBlockedError, SourceError) as exc:
            return self._failure(exc)
        except Exception as exc:  # a bad payload never breaks a request
            return self._failure(SourceError(f"{self.name}: unreadable ({type(exc).__name__})"))
        found: dict[str, FundInfo] = {}
        for rec in records:  # newest month first: the first row of a fund has its latest name
            info = self._info(rec)
            if info is not None and info.fund_id not in found:
                found[info.fund_id] = info
        funds = list(found.values())[:limit]
        if not funds:
            return Field.missing(SOURCE, "not_found")
        self._search_cache.set(key, funds)
        return Field.ok(funds, SOURCE, None)

    def get_fund(self, fund_id: str) -> Field[FundSeries]:
        fid = fund_id.strip()
        if not _ID.fullmatch(fid):
            return Field.missing(SOURCE, "not_found")
        if self._unconfigured():
            return Field.missing(SOURCE, "unavailable")
        hit = self._series_cache.get(fid)
        if hit is not None:
            return Field.ok(hit, SOURCE, period_end(hit.months[-1].period))
        if not self.available():
            return Field.missing(SOURCE, "rate_limited")
        try:
            records = self._records(
                {
                    "filters": json.dumps({self._col("fund_id"): fid}),
                    "sort": f"{self._col('period')} desc",
                    "limit": self.settings.gemelnet_series_months,
                }
            )
            info = next((i for rec in records if (i := self._info(rec)) is not None), None)
            by_period: dict[str, FundMonth] = {}
            for rec in records:
                m = self._month(rec)
                if m is not None and m.period not in by_period:
                    by_period[m.period] = m
            if info is None or not by_period:
                return Field.missing(SOURCE, "not_found")
            months = [by_period[p] for p in sorted(by_period)]
            series = FundSeries(info=info, months=months)
            self._add_category(series)
        except (SourceBlockedError, SourceError) as exc:
            return self._failure(exc)
        except Exception as exc:
            return self._failure(SourceError(f"{self.name}: unreadable ({type(exc).__name__})"))
        self._series_cache.set(fid, series)
        return Field.ok(series, SOURCE, period_end(months[-1].period))

    def _add_category(self, series: FundSeries) -> None:
        """Same-category average for the fund's latest month (best effort: failure leaves it out)."""
        cls, last = series.info.classification, series.months[-1].period
        if not cls:
            return
        try:
            records = self._records(
                {
                    "filters": json.dumps({self._col("classification"): cls}),
                    "sort": f"{self._col('period')} desc",
                    "limit": self.settings.gemelnet_category_max_rows,
                }
            )
        except (SourceBlockedError, SourceError) as exc:
            log.warning("%s (category average skipped)", exc)
            return
        values: dict[str, float] = {}
        for rec in records:
            m, fid = self._month(rec), _fund_id(rec.get(self._col("fund_id")))
            if (
                m is not None
                and fid is not None
                and m.period == last
                and m.monthly_return_pct is not None
            ):
                values[fid] = m.monthly_return_pct
        if len(values) >= self.settings.gemelnet_category_min_peers:
            series.category_period = last
            series.category_avg_monthly_return_pct = sum(values.values()) / len(values)
            series.category_peer_count = len(values)

"""In-memory fakes of the Phase 2 data providers (tests and offline development; no network).

They honour the same contract as a real provider: a symbol outside the declared `markets` gets
`missing_reason="coverage"` without any lookup; a covered symbol with no data gets `not_found`.
"""

from __future__ import annotations

from datetime import datetime

from app.providers.base import (
    ALL_MARKETS,
    Field,
    Filing,
    FundamentalsSnapshot,
    Market,
    NewsItem,
    Transcript,
    covers,
)
from app.timeutil import utcnow


class FakeFundamentals:
    def __init__(
        self,
        data: dict[str, dict[str, float]] | None = None,
        markets: frozenset[Market] = frozenset({"US"}),
        name: str = "fake-fundamentals",
        as_of: datetime | None = None,
    ) -> None:
        self.name, self.markets = name, markets
        self.data = data or {}
        self.as_of = as_of or utcnow()
        self.calls: list[str] = []

    def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot:
        self.calls.append(symbol)
        if not covers(self, symbol):
            return FundamentalsSnapshot.all_missing(symbol, self.name, "coverage")
        row = self.data.get(symbol)
        if row is None:
            return FundamentalsSnapshot.all_missing(symbol, self.name, "not_found")
        fields: dict[str, Field[float]] = {}
        for n in FundamentalsSnapshot._numeric():
            fields[n] = (
                Field[float].ok(row[n], self.name, self.as_of)
                if n in row
                else Field[float].missing(self.name, "not_found")
            )
        return FundamentalsSnapshot(symbol=symbol, **fields)


class _FakeList:
    def __init__(self, name: str, markets: frozenset[Market]) -> None:
        self.name, self.markets = name, markets
        self.calls: list[str] = []


class FakeNews(_FakeList):
    def __init__(
        self,
        data: dict[str, list[NewsItem]] | None = None,
        markets: frozenset[Market] = ALL_MARKETS,
        name: str = "fake-news",
    ) -> None:
        super().__init__(name, markets)
        self.data = data or {}

    def get_news(self, symbol: str, limit: int = 20) -> Field[list[NewsItem]]:
        self.calls.append(symbol)
        if not covers(self, symbol):
            return Field[list[NewsItem]].missing(self.name, "coverage")
        items = self.data.get(symbol)
        if not items:
            return Field[list[NewsItem]].missing(self.name, "not_found")
        return Field[list[NewsItem]].ok(items[:limit], self.name, utcnow())


class FakeTranscripts(_FakeList):
    def __init__(
        self,
        data: dict[str, list[Transcript]] | None = None,
        markets: frozenset[Market] = frozenset({"US"}),
        name: str = "fake-transcripts",
    ) -> None:
        super().__init__(name, markets)
        self.data = data or {}

    def get_transcripts(self, symbol: str, quarters: int = 4) -> Field[list[Transcript]]:
        self.calls.append(symbol)
        if not covers(self, symbol):
            return Field[list[Transcript]].missing(self.name, "coverage")
        items = self.data.get(symbol)
        if not items:
            return Field[list[Transcript]].missing(self.name, "not_found")
        return Field[list[Transcript]].ok(items[:quarters], self.name, utcnow())


class FakeFilings(_FakeList):
    def __init__(
        self,
        data: dict[str, list[Filing]] | None = None,
        markets: frozenset[Market] = frozenset({"US"}),
        name: str = "fake-filings",
    ) -> None:
        super().__init__(name, markets)
        self.data = data or {}

    def get_filings(
        self, symbol: str, forms: tuple[str, ...] = (), limit: int = 20
    ) -> Field[list[Filing]]:
        self.calls.append(symbol)
        if not covers(self, symbol):
            return Field[list[Filing]].missing(self.name, "coverage")
        items = [f for f in self.data.get(symbol, []) if not forms or f.form in forms]
        if not items:
            return Field[list[Filing]].missing(self.name, "not_found")
        return Field[list[Filing]].ok(items[:limit], self.name, utcnow())

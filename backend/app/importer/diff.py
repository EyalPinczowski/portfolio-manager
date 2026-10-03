"""Diff a draft against the last holdings snapshot, proposing changes for the user to confirm."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.importer.parse import MONEY_MAX, QTY_MAX, Currency, RowSymbol

ChangeType = Literal["buy", "sell", "deposit", "withdrawal"]
EPS = 1e-9


class ProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_index: int = Field(ge=-1, le=10_000)  # -1 when the holding vanished from the screenshot
    symbol: RowSymbol
    type: ChangeType
    quantity: float | None = Field(ge=0, le=QTY_MAX, allow_inf_nan=False)
    amount: float | None = Field(ge=0, le=MONEY_MAX, allow_inf_nan=False)  # in `currency`
    currency: Currency


def diff_rows(
    new_rows: list[dict[str, Any]], last_rows: list[dict[str, Any]] | None
) -> list[ProposedChange]:
    """Rows: dicts with symbol, quantity, price_native, currency.

    With no previous snapshot (first import) there is nothing to diff: the import is the baseline.
    A quantity increase is proposed as a buy, a decrease or a vanished holding as a sell. The
    user can switch a change to deposit/withdrawal (in-kind transfer) in the review screen.
    """
    if last_rows is None:
        return []
    old = {r["symbol"]: r for r in last_rows if r.get("symbol")}
    changes: list[ProposedChange] = []
    seen: set[str] = set()
    for idx, row in enumerate(new_rows):
        sym = row.get("symbol")
        qty = row.get("quantity")
        if not sym or qty is None:
            continue
        seen.add(sym)
        old_qty = float(old[sym]["quantity"]) if sym in old else 0.0
        delta = float(qty) - old_qty
        if abs(delta) <= EPS:
            continue
        price = row.get("price_native")
        changes.append(
            ProposedChange(
                row_index=row.get("index", idx),
                symbol=sym,
                type="buy" if delta > 0 else "sell",
                quantity=abs(delta),
                amount=round(abs(delta) * float(price), 4) if price is not None else None,
                currency="USD" if row.get("currency") == "USD" else "ILS",
            )
        )
    for sym, row in old.items():
        if sym in seen:
            continue
        price = row.get("price_native")
        qty = float(row["quantity"])
        changes.append(
            ProposedChange(
                row_index=-1,
                symbol=sym,
                type="sell",
                quantity=qty,
                amount=round(qty * float(price), 4) if price is not None else None,
                currency="USD" if row.get("currency") == "USD" else "ILS",
            )
        )
    return changes

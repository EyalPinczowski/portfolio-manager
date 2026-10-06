"""Combine overlapping screenshots of one scrolling list (port of `mergeScreenshots`).

A card that is in two screenshots (the last card of one image is the first of the next) is merged,
**never summed**:

- identical price and value: one row, flag `duplicate_removed`;
- one copy is cut off (no price or value, or the same price without the P&L % at the card's
  bottom): the complete copy wins, `duplicate_removed`, no conflict;
- both complete but different: one row (the later screenshot, which is fresher), flag `conflict`,
  and the other copy's numbers in `row.conflict`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.importer.hebrew import names_match
from app.importer.parse import ParsedRow, RowConflict


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)


def _complete(r: ParsedRow) -> bool:
    return r.value is not None and r.price is not None


def _cut_off_copy(a: ParsedRow, b: ParsedRow) -> bool:
    """`a` is a cut-off copy of `b`: the same price, but the bottom of the card (the P&L %, so the
    cost) was not on that screen, and its value may be half read. Not a real disagreement."""
    return _close(a.price, b.price) and a.cost is None and b.cost is not None


def same_card(a: ParsedRow, b: ParsedRow) -> bool:
    """The same security: by symbol, else by TASE number; names only when neither row has an id."""
    if a.symbol and b.symbol:
        return a.symbol == b.symbol
    if a.tase_number and b.tase_number:
        return a.tase_number == b.tase_number
    if a.symbol or b.symbol or a.tase_number or b.tase_number:
        return False
    return names_match(a.name, b.name)


def _with_flag(row: ParsedRow, flag: str) -> ParsedRow:
    if flag not in row.flags:
        row.flags = [*row.flags, flag]
    return row


def merge_screenshots(
    parts: Sequence[Sequence[ParsedRow]], reindex: bool = False
) -> list[ParsedRow]:
    """Rows of several screenshots, in order of first appearance. Inputs are not modified.

    With a single part nothing is merged (one screenshot is one list). `reindex` renumbers the
    result by position (the server parser does; rows from the client keep their own `index`).
    """
    rows: list[ParsedRow] = []
    multi = len(parts) > 1
    for part in parts:
        for r in part:
            at = next((i for i, x in enumerate(rows) if same_card(x, r)), -1) if multi else -1
            if at < 0:
                rows.append(r.model_copy(deep=True))
                continue
            old = rows[at]
            if _complete(old) and _complete(r) and _cut_off_copy(old, r):
                win = r.model_copy(deep=True, update={"index": old.index})
                win.name = r.name or old.name
                rows[at] = _with_flag(win, "duplicate_removed")
            elif _complete(old) and _complete(r) and _cut_off_copy(r, old):
                old.name = old.name or r.name
                _with_flag(old, "duplicate_removed")
            elif (
                _complete(old)
                and _complete(r)
                and not (_close(old.price, r.price) and _close(old.value, r.value))
            ):
                lost = RowConflict(price=old.price, value=old.value, quantity=old.quantity)
                win = r.model_copy(deep=True, update={"index": old.index})
                win.name = r.name or old.name
                win.conflict = lost
                rows[at] = _with_flag(win, "conflict")
            elif not _complete(old) and _complete(r):
                win = r.model_copy(deep=True, update={"index": old.index})
                win.name = r.name or old.name
                rows[at] = _with_flag(win, "duplicate_removed")
            else:
                old.name = old.name or r.name
                _with_flag(old, "duplicate_removed")
    if reindex:
        for i, row in enumerate(rows):
            row.index = i
    return rows


def merge_rows(rows: Sequence[ParsedRow]) -> list[ParsedRow]:
    """One list of rows in which the same security may appear twice (a draft from several
    screenshots): each row is treated as its own screenshot, later rows win. Never sums."""
    return merge_screenshots([[r] for r in rows])

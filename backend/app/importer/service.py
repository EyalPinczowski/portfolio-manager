"""Screenshot import pipeline.

redact -> OCR provider -> parse -> validate qty x price ~ value -> match against Security ->
diff vs the last snapshot -> draft -> (user review) -> confirm.

The uploaded image is processed in memory only and is never written to disk, so "deleting the
image" is guaranteed by construction: the bytes go out of scope when `build_draft` returns.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import update
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.importer.diff import ProposedChange, diff_rows
from app.importer.imageio import ImageRejectedError
from app.importer.layouts import rows_from_ocr_result
from app.importer.match import (
    SecurityIndex,
    apply_match,
    currency_flag,
    new_security_kind,
    resolve_row,
)
from app.importer.merge import merge_rows
from app.importer.parse import (
    ParsedRow,
    cost_native,
    price_native,
    row_currency,
    validate_row,
)
from app.importer.redact import redact_image
from app.models import (
    Holding,
    HoldingsSnapshot,
    ImportDraft,
    Portfolio,
    Security,
    Transaction,
)
from app.portfolio.quotes import refresh_symbols
from app.portfolio.valuation import (
    ensure_tracking_started,
    pending_markers,
    record_quantity_change,
    register_pending,
    screenshot_prices,
    sync_pending_flows,
    value_portfolio,
)
from app.providers.base import OcrProvider, OcrUnavailableError
from app.providers.fx_provider import get_usd_ils
from app.providers.ocr.tesseract import ON_DEVICE_MESSAGE
from app.providers.registry import get_providers
from app.securities import infer_security
from app.timeutil import as_utc, local_today, utcnow

log = logging.getLogger(__name__)

# Flags the reading side (the on-device parser) decides; the server keeps them. Every other flag
# (match, currency, value checks) is the server's own and is recomputed.
CLIENT_FLAGS = frozenset({"quantity_fractional", "cost_inferred", "duplicate_removed", "conflict"})


def draft_expires_at(draft: ImportDraft, settings: Settings | None = None) -> datetime:
    """When an unconfirmed draft is deleted (aware UTC)."""
    s = settings or get_settings()
    return as_utc(draft.created_at) + timedelta(hours=s.import_draft_ttl_hours)


def purge_expired_drafts(
    db: Session, settings: Settings | None = None, now: datetime | None = None
) -> int:
    """Delete unconfirmed drafts older than the TTL (retention rule: 24 h). Returns the count."""
    s = settings or get_settings()
    cutoff = (now or utcnow()) - timedelta(hours=s.import_draft_ttl_hours)
    old = db.exec(
        select(ImportDraft).where(
            col(ImportDraft.status) != "confirmed", col(ImportDraft.created_at) < cutoff
        )
    ).all()
    for d in old:
        db.delete(d)
    db.commit()
    return len(old)


def baseline_rows(db: Session, portfolio_id: int) -> list[dict[str, Any]] | None:
    """What the portfolio holds now, as the diff's "before": quantity per holding and its last
    known native price (the newest screenshot price of this portfolio, else the average cost).

    The holdings are the baseline, not the last snapshot: after a partial update the snapshot
    holds only the rows of that screenshot. None means "first import" (nothing to diff against).
    """
    holdings = db.exec(select(Holding).where(Holding.portfolio_id == portfolio_id)).all()
    if not holdings:
        has_history = db.exec(
            select(HoldingsSnapshot.id).where(HoldingsSnapshot.portfolio_id == portfolio_id)
        ).first()
        return [] if has_history is not None else None
    prices = screenshot_prices(db, portfolio_id)
    out: list[dict[str, Any]] = []
    for h in holdings:
        px, cur = prices.get(h.symbol) or (h.avg_cost, h.cost_currency)
        out.append(
            {"symbol": h.symbol, "quantity": h.quantity, "price_native": px, "currency": cur}
        )
    return out


def matchable_securities(db: Session, owner_id: int) -> list[Security]:
    """The only securities a user's import may match: verified ones plus the symbols already in
    this user's own portfolios. Another user's unverified ticker never matches or is suggested."""
    own = select(Holding.symbol).join(Portfolio, col(Portfolio.id) == col(Holding.portfolio_id))
    own = own.where(Portfolio.owner_id == owner_id)
    return list(
        db.exec(
            select(Security).where(
                col(Security.verified).is_(True) | col(Security.symbol).in_(own.scalar_subquery())
            )
        ).all()
    )


def finalize_rows(
    db: Session,
    rows: list[ParsedRow],
    settings: Settings,
    owner_id: int,
    rematch: bool = True,
    previous: list[ParsedRow] | None = None,
    renumber: bool = False,
) -> list[ParsedRow]:
    """Validate and (re)match rows. A row that already has a known symbol keeps it.

    `row.index` is the client's own key for the row and is echoed unchanged; only the server's own
    OCR path asks for `renumber`.

    `owner_id` scopes the match index (`matchable_securities`). With `rematch=False` (a user's
    edit) a known symbol is kept, but a row whose symbol, currency or unit differs from the same
    row in `previous` is checked again: it raises `currency_changed` / `unit_mismatch` when its
    currency disagrees with the security's, so an edit is never a silent confirmation. A row the
    user did not touch keeps the flags (or the absence of them) it was confirmed with.
    """
    index = SecurityIndex(matchable_securities(db, owner_id))
    for i, row in enumerate(rows):
        if renumber:
            row.index = i
        validate_row(row, settings)
        known = index.by_symbol.get((row.symbol or "").upper()) if row.symbol else None
        if known is not None and not rematch:
            row.matched_name = known.name_en
            row.flags = [f for f in row.flags if f not in ("unmatched", "low_confidence_match")]
            row.candidates = []
            if previous is None or _changed(row, previous[i] if i < len(previous) else None):
                row.flags = [f for f in row.flags if f not in ("currency_changed", "unit_mismatch")]
                flag = currency_flag(row, known)
                if flag is not None:
                    row.flags.append(flag)
            continue
        apply_match(row, resolve_row(row, index, settings), index)
    return rows


def _changed(row: ParsedRow, old: ParsedRow | None) -> bool:
    """Did an edit touch what decides the currency check (symbol, currency, unit)?"""
    if old is None:
        return True
    return (
        (row.symbol or "").upper(),
        row_currency(row),
        row.unit,
    ) != ((old.symbol or "").upper(), row_currency(old), old.unit)


def _diff_input(rows: list[ParsedRow]) -> list[dict[str, Any]]:
    return [
        {
            "index": r.index,
            "symbol": r.symbol,
            "quantity": r.quantity,
            "price_native": price_native(r),
            "currency": row_currency(r),
        }
        for r in rows
    ]


def recompute_changes(
    db: Session,
    portfolio_id: int,
    rows: list[ParsedRow],
    scope: str = "partial",
    previous: list[ProposedChange] | None = None,
) -> list[ProposedChange]:
    """The proposed changes of a draft against what the portfolio holds. In a `full` update a
    holding that is not in the screenshots is listed with the user's earlier choice (`previous`),
    else `keep`: it is never sold or withdrawn unless the user says so."""
    changes = diff_rows(
        _diff_input(rows),
        baseline_rows(db, portfolio_id),
        scope="full" if scope == "full" else "partial",
        missing_type="keep",
    )
    chosen = {c.symbol: c.type for c in previous or [] if c.row_index == -1}
    for c in changes:
        if c.row_index == -1 and c.symbol in chosen:
            c.type = chosen[c.symbol]
    return changes


def keep_stock_rows(rows: list[ParsedRow], renumber: bool = True) -> list[ParsedRow]:
    """Drop lines that are not stock rows (account / owner / address lines read by OCR).

    A row survives only if it matches a security or its quantity x price ~ value validates.
    Dropped lines are never stored (and never logged): the draft holds stock fields only.
    """
    kept = [
        r
        for r in rows
        if r.symbol is not None
        or r.tase_number is not None  # a broker card identified by its security number
        or r.exchange is not None  # ... or by `exchange • ticker`
        or not {"missing_fields", "value_mismatch"} & set(r.flags)
    ]
    if renumber:
        for i, r in enumerate(kept):
            r.index = i
    return kept


def _save_draft(
    db: Session, portfolio: Portfolio, rows: list[ParsedRow], scope: str = "partial"
) -> ImportDraft:
    assert portfolio.id is not None
    if not rows:
        raise HTTPException(422, "No holdings could be read from the image")
    draft = ImportDraft(
        portfolio_id=portfolio.id,
        rows=[r.model_dump() for r in rows],
        proposed_changes=[c.model_dump() for c in recompute_changes(db, portfolio.id, rows, scope)],
        status="draft",
        scope=scope,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def build_draft(
    db: Session,
    portfolio: Portfolio,
    image_bytes: bytes,
    ocr: OcrProvider,
    settings: Settings | None = None,
    scope: str = "partial",
) -> ImportDraft:
    s = settings or get_settings()
    assert portfolio.id is not None
    available = getattr(ocr, "available", None)
    if callable(available) and not available():
        # No engine on this server (the slim image has no Tesseract): say so before decoding the
        # image, which is the memory-heavy part.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ON_DEVICE_MESSAGE)
    try:
        redacted, report = redact_image(image_bytes, s)
    except ImageRejectedError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The file is not a readable image"
        ) from exc
    try:
        if not report.word_boxes_available:
            log.warning("Word-box redaction unavailable: long digit runs could not be blurred")
            if getattr(ocr, "third_party", False):
                # Never send a half-redacted image to a third party.
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Server-side reading with an external service needs full redaction, which is "
                    "unavailable here. Use on-device reading instead.",
                )
        try:
            result = ocr.extract(redacted)
        except OcrUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    finally:
        del redacted, image_bytes
    rows = rows_from_ocr_result(result, s)
    del result  # raw OCR text goes out of scope here and is never stored
    if not rows:
        raise HTTPException(422, "No holdings could be read from the image")
    rows = merge_rows(rows)
    finalize_rows(db, rows, s, portfolio.owner_id, renumber=True)
    return _save_draft(db, portfolio, keep_stock_rows(rows), scope)


def build_draft_from_rows(
    db: Session,
    portfolio: Portfolio,
    rows: list[ParsedRow],
    settings: Settings | None = None,
    scope: str = "partial",
) -> ImportDraft:
    """On-device OCR path: the browser read the screenshot, only the parsed rows arrive."""
    s = settings or get_settings()
    for r in rows:  # the server decides flags / candidates / matches, never the client
        r.flags = [f for f in r.flags if f in CLIENT_FLAGS]
        r.candidates, r.matched_name = [], None
    rows = merge_rows(rows)  # overlapping screenshots: one row per card, never summed
    finalize_rows(db, rows, s, portfolio.owner_id)
    return _save_draft(db, portfolio, keep_stock_rows(rows, renumber=False), scope)


def rows_from_models(models: Sequence[BaseModel]) -> list[ParsedRow]:
    """Request rows to `ParsedRow`s. A row keeps the `index` the client gave it (the client
    re-attaches its own metadata by it); rows without one are numbered with unused integers."""
    rows = [ParsedRow.model_validate(m.model_dump()) for m in models]
    explicit = [r.index for r, m in zip(rows, models, strict=True) if "index" in m.model_fields_set]
    if len(set(explicit)) != len(explicit):
        raise HTTPException(422, "Row indexes must be unique")
    used = set(explicit)
    free = (i for i in itertools.count() if i not in used)
    for r, m in zip(rows, models, strict=True):
        if "index" not in m.model_fields_set:
            r.index = next(free)
    return rows


def row_security(db: Session, row: ParsedRow, created: list[str]) -> Security:
    """The row's security; one the seed does not know is created here, unverified and without any
    OCR text (the name is the symbol), after `new_security_kind` confirmed the evidence. It is only
    ever matched for users who hold it, and the first quote verifies it."""
    assert row.symbol is not None
    sec = db.get(Security, row.symbol)
    if sec is not None:
        return sec
    kind = new_security_kind(row)
    if kind == "tase":
        sec = Security(
            symbol=row.symbol,
            name_en=row.symbol,
            tase_number=row.tase_number,
            asset_type="fund",
            market="TASE",
            currency="ILS",
            country="Israel",
            verified=False,
        )
    elif kind == "us":
        sec = infer_security(row.symbol)
    else:  # pragma: no cover - the caller checked
        raise HTTPException(422, f"Row {row.index + 1} is not matched to a security")
    db.add(sec)
    db.flush()
    created.append(sec.symbol)
    return sec


def draft_rows(draft: ImportDraft) -> list[ParsedRow]:
    return [ParsedRow.model_validate(r) for r in draft.rows]


def confirm_draft(
    db: Session, draft: ImportDraft, portfolio: Portfolio, settings: Settings | None = None
) -> ImportDraft:
    """Write holdings, a HoldingsSnapshot and the (inferred) transactions; start tracking.

    **Atomic**: everything is written in one database transaction and committed once at the end.
    Any failure (a bad row, a provider error, a crash between two writes) rolls the whole thing
    back: the portfolio, its transactions and the draft stay exactly as they were. Only the quote
    refresh that verifies new securities runs after the commit, and it is best effort.
    """
    s = settings or get_settings()
    assert portfolio.id is not None and draft.id is not None
    if draft.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This import was already confirmed")
    rows = draft_rows(draft)
    if not rows:
        raise HTTPException(422, "The import has no rows")
    for r in rows:
        if not r.symbol or (db.get(Security, r.symbol) is None and new_security_kind(r) is None):
            raise HTTPException(
                422,
                f"Row {r.index + 1} ('{r.name}') is not matched to a security; fix or remove it",
            )
        if r.quantity is None or r.quantity <= 0:
            raise HTTPException(422, f"Row {r.index + 1} has no valid quantity")
        if "currency_changed" in r.flags:
            raise HTTPException(
                422,
                f"Row {r.index + 1} ('{r.name}') is shown in a different currency than the "
                "security's own; confirm the currency (remove the flag) or edit the row",
            )
    rows = merge_rows(rows)  # never summed: the later copy wins
    changes = [ProposedChange.model_validate(c) for c in draft.proposed_changes]
    new_symbols: list[str] = []
    try:
        _apply_confirm(db, draft, portfolio, rows, changes, s, new_symbols)
        db.commit()  # the one commit
    except BaseException:
        db.rollback()
        raise
    if new_symbols:
        try:  # best effort and outside the import's transaction: a quote verifies the security
            refresh_symbols(db, new_symbols, get_providers().quotes)
        except Exception as exc:
            db.rollback()
            log.warning("quote refresh after an import failed: %s", type(exc).__name__)
    db.refresh(draft)
    return draft


def _missing_choices(
    draft: ImportDraft,
    changes: list[ProposedChange],
    rows: list[ParsedRow],
    existing: dict[str, Holding],
) -> dict[str, ProposedChange]:
    """The user's sold / withdrawn / keep choice for each holding that is not in the screenshots
    (`row_index == -1`). Only a `full` update has them, only for holdings the portfolio really has
    and the rows do not mention. `keep` choices need no action and are dropped here."""
    in_rows = {r.symbol for r in rows}
    out: dict[str, ProposedChange] = {}
    for ch in changes:
        if ch.row_index != -1:
            continue
        if draft.scope != "full":
            raise HTTPException(
                422, "'Not in these screenshots' choices only apply to a full update"
            )
        if ch.symbol not in existing or ch.symbol in in_rows:
            raise HTTPException(
                422, f"{ch.symbol} is not a holding that is missing from these screenshots"
            )
        if ch.type != "keep":
            out[ch.symbol] = ch
    return out


def _apply_confirm(
    db: Session,
    draft: ImportDraft,
    portfolio: Portfolio,
    rows: list[ParsedRow],
    changes: list[ProposedChange],
    s: Settings,
    new_symbols: list[str],
) -> None:
    """The writes of `confirm_draft`. Never commits (flush only); the caller owns the transaction."""
    assert portfolio.id is not None and draft.id is not None
    claimed = db.exec(
        update(ImportDraft)  # type: ignore[call-overload]
        .where(col(ImportDraft.id) == draft.id, col(ImportDraft.status) == "draft")
        .values(status="confirmed")
    )
    if claimed.rowcount != 1:  # a concurrent confirm got there first
        raise HTTPException(status.HTTP_409_CONFLICT, "This import was already confirmed")
    today = local_today()
    usd_ils = get_usd_ils(db, s)
    first_import = portfolio.tracking_started_at is None
    existing = {
        h.symbol: h
        for h in db.exec(select(Holding).where(Holding.portfolio_id == portfolio.id)).all()
    }
    previous_qty = {sym: h.quantity for sym, h in existing.items()}
    gone = _missing_choices(draft, changes, rows, existing)
    # Priced as before any change: a removed holding without an amount books its flow at its price.
    before = (
        value_portfolio(db, portfolio, s)
        if any(ch.amount is None for ch in gone.values()) and not first_import
        else None
    )
    snapshot_rows: list[dict[str, Any]] = []
    for r in rows:
        assert r.symbol is not None and r.quantity is not None
        row_security(db, r, new_symbols)
        cost = cost_native(r)
        cur = row_currency(r)
        h = existing.get(r.symbol)
        if h is None:
            h = Holding(portfolio_id=portfolio.id, symbol=r.symbol, quantity=r.quantity)
        # An estimate from the broker's P&L % is offered, not forced: it never replaces a cost
        # the holding already has (the user accepts it by removing the `cost_inferred` flag).
        forced_over = cost is not None and "cost_inferred" in r.flags and h.avg_cost is not None
        h.quantity = r.quantity
        if cost is not None and not forced_over:
            h.avg_cost, h.cost_currency = cost, cur
        elif h.avg_cost is None:
            h.cost_currency = cur
        db.add(h)
        px = price_native(r)  # kept per portfolio in the HoldingsSnapshot (never a global quote)
        snapshot_rows.append(
            {"symbol": r.symbol, "quantity": r.quantity, "price_native": px, "currency": cur}
        )
    for sym in gone:
        db.delete(existing[sym])
    db.add(HoldingsSnapshot(portfolio_id=portfolio.id, source="screenshot", rows=snapshot_rows))
    if not first_import:
        for ch in changes:
            if ch.row_index == -1 and ch.symbol not in gone:
                continue
            if ch.amount is None:
                if ch.symbol in gone and before is not None:
                    gh = existing[ch.symbol]
                    valued = next((v for v in before.holdings if v.holding.id == gh.id), None)
                    record_quantity_change(db, portfolio, gh, -gh.quantity, valued, today)
                continue
            db.add(
                Transaction(
                    portfolio_id=portfolio.id,
                    symbol=ch.symbol,
                    type=ch.type,
                    quantity=ch.quantity,
                    price=(ch.amount / ch.quantity) if ch.quantity else None,
                    amount=ch.amount,
                    currency=ch.currency,
                    fx_to_ils=usd_ils if ch.currency == "USD" else 1.0,
                    date=today,
                    inferred=True,
                )
            )
    portfolio.last_screenshot_update_at = utcnow()
    db.add(portfolio)
    draft.status = "confirmed"
    draft.rows = []  # retention: only the HoldingsSnapshot / holdings keep the stock data
    draft.proposed_changes = []
    db.add(draft)
    db.flush()
    if not ensure_tracking_started(db, portfolio, today, commit=False):
        # New holdings without a real price (and quantity changes of ones still waiting for it) go
        # into the holding's one marker; settled at the first real price, never at the cost.
        val = value_portfolio(db, portfolio, s)
        waiting = pending_markers(db, portfolio.id)
        for v in val.holdings:
            h = v.holding
            old_qty = previous_qty.get(h.symbol)
            if old_qty is None and not v.performance_priced:
                register_pending(db, portfolio, h, h.quantity, today)
            elif old_qty is not None and h.id in waiting and h.quantity != old_qty:
                register_pending(db, portfolio, h, h.quantity - old_qty, today)
        sync_pending_flows(db, portfolio, settings=s, today=today)
    db.flush()

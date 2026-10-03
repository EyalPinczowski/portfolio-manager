"""Screenshot import pipeline.

redact -> OCR provider -> parse -> validate qty x price ~ value -> match against Security ->
diff vs the last snapshot -> draft -> (user review) -> confirm.

The uploaded image is processed in memory only and is never written to disk, so "deleting the
image" is guaranteed by construction: the bytes go out of scope when `build_draft` returns.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.importer.diff import ProposedChange, diff_rows
from app.importer.imageio import ImageRejectedError
from app.importer.match import SecurityIndex, apply_match, currency_flag, match_row
from app.importer.parse import (
    ParsedRow,
    cost_native,
    parse_ocr_result,
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
from app.portfolio.valuation import (
    ensure_tracking_started,
    pending_markers,
    register_pending,
    sync_pending_flows,
    value_portfolio,
)
from app.providers.base import OcrProvider, OcrUnavailableError
from app.providers.fx_provider import get_usd_ils
from app.providers.ocr.tesseract import ON_DEVICE_MESSAGE
from app.timeutil import as_utc, local_today, utcnow

log = logging.getLogger(__name__)


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


def last_snapshot_rows(db: Session, portfolio_id: int) -> list[dict[str, Any]] | None:
    snap = db.exec(
        select(HoldingsSnapshot)
        .where(HoldingsSnapshot.portfolio_id == portfolio_id)
        .order_by(col(HoldingsSnapshot.id).desc())
    ).first()
    if snap is not None:
        return list(snap.rows)
    holdings = db.exec(select(Holding).where(Holding.portfolio_id == portfolio_id)).all()
    if not holdings:
        return None
    return [
        {
            "symbol": h.symbol,
            "quantity": h.quantity,
            "price_native": h.avg_cost,
            "currency": h.cost_currency,
        }
        for h in holdings
    ]


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
) -> list[ParsedRow]:
    """Validate and (re)match rows. A row that already has a known symbol keeps it.

    `owner_id` scopes the match index (`matchable_securities`). With `rematch=False` (a user's
    edit) a known symbol is kept, but a row whose symbol, currency or unit differs from the same
    row in `previous` is checked again: it raises `currency_changed` / `unit_mismatch` when its
    currency disagrees with the security's, so an edit is never a silent confirmation. A row the
    user did not touch keeps the flags (or the absence of them) it was confirmed with.
    """
    index = SecurityIndex(matchable_securities(db, owner_id))
    for i, row in enumerate(rows):
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
        apply_match(row, match_row(row, index, settings), index)
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
    db: Session, portfolio_id: int, rows: list[ParsedRow]
) -> list[ProposedChange]:
    return diff_rows(_diff_input(rows), last_snapshot_rows(db, portfolio_id))


def keep_stock_rows(rows: list[ParsedRow]) -> list[ParsedRow]:
    """Drop lines that are not stock rows (account / owner / address lines read by OCR).

    A row survives only if it matches a security or its quantity x price ~ value validates.
    Dropped lines are never stored (and never logged): the draft holds stock fields only.
    """
    kept = [
        r
        for r in rows
        if r.symbol is not None or not {"missing_fields", "value_mismatch"} & set(r.flags)
    ]
    for i, r in enumerate(kept):
        r.index = i
    return kept


def _save_draft(db: Session, portfolio: Portfolio, rows: list[ParsedRow]) -> ImportDraft:
    assert portfolio.id is not None
    if not rows:
        raise HTTPException(422, "No holdings could be read from the image")
    draft = ImportDraft(
        portfolio_id=portfolio.id,
        rows=[r.model_dump() for r in rows],
        proposed_changes=[c.model_dump() for c in recompute_changes(db, portfolio.id, rows)],
        status="draft",
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
    rows = parse_ocr_result(result, s)
    del result  # raw OCR text goes out of scope here and is never stored
    if not rows:
        raise HTTPException(422, "No holdings could be read from the image")
    finalize_rows(db, rows, s, portfolio.owner_id)
    return _save_draft(db, portfolio, keep_stock_rows(rows))


def build_draft_from_rows(
    db: Session, portfolio: Portfolio, rows: list[ParsedRow], settings: Settings | None = None
) -> ImportDraft:
    """On-device OCR path: the browser read the screenshot, only the parsed rows arrive."""
    s = settings or get_settings()
    for r in rows:  # the server decides flags / candidates / matches, never the client
        r.flags, r.candidates, r.matched_name = [], [], None
    finalize_rows(db, rows, s, portfolio.owner_id)
    return _save_draft(db, portfolio, keep_stock_rows(rows))


def merge_duplicate_rows(rows: list[ParsedRow]) -> list[ParsedRow]:
    """Two rows for the same security (e.g. two lots) become one holding with summed quantity."""
    merged: dict[str, ParsedRow] = {}
    for r in rows:
        key = r.symbol or ""
        if key in merged:
            first = merged[key]
            first.quantity = (first.quantity or 0.0) + (r.quantity or 0.0)
            first.value = (
                (first.value or 0.0) + (r.value or 0.0) if r.value is not None else first.value
            )
        else:
            merged[key] = r.model_copy()
    return list(merged.values())


def draft_rows(draft: ImportDraft) -> list[ParsedRow]:
    return [ParsedRow.model_validate(r) for r in draft.rows]


def confirm_draft(
    db: Session, draft: ImportDraft, portfolio: Portfolio, settings: Settings | None = None
) -> ImportDraft:
    """Write holdings, a HoldingsSnapshot and the (inferred) transactions; start tracking."""
    s = settings or get_settings()
    assert portfolio.id is not None and draft.id is not None
    if draft.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This import was already confirmed")
    rows = draft_rows(draft)
    if not rows:
        raise HTTPException(422, "The import has no rows")
    for r in rows:
        if not r.symbol or db.get(Security, r.symbol) is None:
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
    rows = merge_duplicate_rows(rows)
    today = local_today()
    usd_ils = get_usd_ils(db, s)
    first_import = portfolio.tracking_started_at is None
    existing = {
        h.symbol: h
        for h in db.exec(select(Holding).where(Holding.portfolio_id == portfolio.id)).all()
    }
    previous_qty = {sym: h.quantity for sym, h in existing.items()}
    keep: set[str] = set()
    snapshot_rows: list[dict[str, Any]] = []
    for r in rows:
        assert r.symbol is not None and r.quantity is not None
        sec = db.get(Security, r.symbol)
        assert sec is not None
        keep.add(r.symbol)
        cost = cost_native(r)
        cur = row_currency(r)
        h = existing.get(r.symbol)
        if h is None:
            h = Holding(portfolio_id=portfolio.id, symbol=r.symbol, quantity=r.quantity)
        h.quantity = r.quantity
        if cost is not None:
            h.avg_cost, h.cost_currency = cost, cur
        elif h.avg_cost is None:
            h.cost_currency = cur
        db.add(h)
        px = price_native(r)  # kept per user in the HoldingsSnapshot below (never a global quote)
        snapshot_rows.append(
            {"symbol": r.symbol, "quantity": r.quantity, "price_native": px, "currency": cur}
        )
    for sym, h in existing.items():
        if sym not in keep:
            db.delete(h)
    db.add(HoldingsSnapshot(portfolio_id=portfolio.id, source="screenshot", rows=snapshot_rows))
    if not first_import:
        for raw in draft.proposed_changes:
            ch = ProposedChange.model_validate(raw)
            amount = ch.amount
            if amount is None:
                continue
            db.add(
                Transaction(
                    portfolio_id=portfolio.id,
                    symbol=ch.symbol,
                    type=ch.type,
                    quantity=ch.quantity,
                    price=(amount / ch.quantity) if ch.quantity else None,
                    amount=amount,
                    currency=ch.currency,
                    fx_to_ils=usd_ils if ch.currency == "USD" else 1.0,
                    date=today,
                    inferred=True,
                )
            )
    draft.status = "confirmed"
    draft.rows = []  # retention: only the HoldingsSnapshot / holdings keep the stock data
    draft.proposed_changes = []
    db.add(draft)
    db.commit()
    if not ensure_tracking_started(db, portfolio, today):
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
        db.commit()
    db.refresh(draft)
    return draft

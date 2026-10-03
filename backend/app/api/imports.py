"""Screenshot import endpoints: upload -> draft -> review -> confirm."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool

from app.api.schemas import ImportDraftOut, ImportPatch, ImportRowModel, ImportRowsBody
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.auth.ratelimit import enforce_limit, upload_limiter
from app.config import Settings, get_settings
from app.importer import service
from app.importer.diff import ProposedChange
from app.importer.imageio import CONTENT_TYPES, sniff_image_type, wipe
from app.importer.parse import ParsedRow
from app.importer.service import draft_expires_at
from app.models import ImportDraft, Portfolio, User
from app.providers.registry import get_providers
from app.repo import get_draft, get_portfolio
from app.timeutil import as_utc, utcnow

router = APIRouter(tags=["imports"])


def _out(d: ImportDraft, settings: Settings | None = None) -> ImportDraftOut:
    assert d.id is not None
    s = settings or get_settings()
    return ImportDraftOut(
        id=d.id,
        portfolio_id=d.portfolio_id,
        status=d.status,  # type: ignore[arg-type]
        rows=[ImportRowModel.model_validate(r) for r in d.rows],
        proposed_changes=[ProposedChange.model_validate(c) for c in d.proposed_changes],
        expires_at=draft_expires_at(d, s),
    )


def _require_consent(user: User) -> None:
    if user.ocr_consent_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "OCR consent is required before uploading")


async def _read_limited_body(request: Request, limit: int) -> bytearray:
    """Read the raw body from the stream, enforcing the limit while reading (no temp files)."""
    buf = bytearray()
    try:
        async for chunk in request.stream():
            buf.extend(chunk)
            if len(buf) > limit:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image is too large")
    except BaseException:
        wipe(buf)
        raise
    return buf


@router.post(
    "/portfolios/{portfolio_id}/imports",
    response_model=ImportDraftOut,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "The raw image bytes (not multipart).",
            "content": {
                ct: {"schema": {"type": "string", "format": "binary"}}
                for ct in ("image/png", "image/jpeg", "image/webp")
            },
        }
    },
)
async def create_import(
    portfolio_id: int,
    request: Request,
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
) -> ImportDraftOut:
    # Auth (UserDep) has already run: nothing below reads the body for an anonymous caller.
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    _require_consent(user)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Send the image as image/png, image/jpeg or image/webp",
        )
    enforce_limit(upload_limiter, f"user:{user.id}", settings.upload_rate_limit_per_hour, 3600.0)
    buf = await _read_limited_body(request, settings.max_upload_bytes)
    try:
        if not buf:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty body")
        if sniff_image_type(buf) is None:  # decided from the bytes, not the header
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "Only PNG, JPEG and WebP images are accepted",
            )
        data = bytes(buf)
        try:
            draft = await run_in_threadpool(
                service.build_draft, db, p, data, get_providers().ocr(), settings
            )
        finally:
            del data
        return _out(draft, settings)
    finally:
        wipe(buf)


@router.post(
    "/portfolios/{portfolio_id}/imports/rows",
    response_model=ImportDraftOut,
    status_code=status.HTTP_201_CREATED,
)
def create_import_from_rows(
    portfolio_id: int, body: ImportRowsBody, user: UserDep, db: DbDep, settings: SettingsDep
) -> ImportDraftOut:
    """On-device OCR path: no image ever reaches the server. No OCR consent is needed (no third
    party and no server-side reading)."""
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    enforce_limit(upload_limiter, f"user:{user.id}", settings.upload_rate_limit_per_hour, 3600.0)
    rows = [ParsedRow.model_validate(r.model_dump()) for r in body.rows]
    return _out(service.build_draft_from_rows(db, p, rows, settings), settings)


def _live_draft(
    db: DbDep, user_id: int, draft_id: int, settings: Settings
) -> tuple[ImportDraft, Portfolio]:
    """An unconfirmed draft past its 24 h expiry is gone (deleted now if the purge has not run)."""
    d, p = get_draft(db, user_id, draft_id)
    if d.status != "confirmed" and draft_expires_at(d, settings) <= as_utc(utcnow()):
        db.delete(d)
        db.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import draft not found")
    return d, p


@router.get("/imports/{draft_id}", response_model=ImportDraftOut)
def get_import(draft_id: int, user: UserDep, db: DbDep, settings: SettingsDep) -> ImportDraftOut:
    assert user.id is not None
    d, _ = _live_draft(db, user.id, draft_id, settings)
    return _out(d, settings)


@router.patch("/imports/{draft_id}", response_model=ImportDraftOut)
def patch_import(
    draft_id: int, body: ImportPatch, user: UserDep, db: DbDep, settings: SettingsDep
) -> ImportDraftOut:
    assert user.id is not None
    d, p = _live_draft(db, user.id, draft_id, settings)
    if d.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This import can no longer be edited")
    if body.rows is not None:
        rows = [ParsedRow.model_validate(r.model_dump()) for r in body.rows]
        service.finalize_rows(db, rows, settings, rematch=False)
        d.rows = [r.model_dump() for r in rows]
        if body.proposed_changes is None:
            assert p.id is not None
            d.proposed_changes = [c.model_dump() for c in service.recompute_changes(db, p.id, rows)]
    if body.proposed_changes is not None:
        d.proposed_changes = [c.model_dump() for c in body.proposed_changes]
    db.add(d)
    db.commit()
    db.refresh(d)
    return _out(d, settings)


@router.post("/imports/{draft_id}/confirm", response_model=ImportDraftOut)
def confirm_import(
    draft_id: int, user: UserDep, db: DbDep, settings: SettingsDep
) -> ImportDraftOut:
    assert user.id is not None
    d, p = _live_draft(db, user.id, draft_id, settings)
    return _out(service.confirm_draft(db, d, p, settings), settings)

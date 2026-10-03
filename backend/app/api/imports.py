"""Screenshot import endpoints: upload -> draft -> review -> confirm."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.schemas import ImportDraftOut, ImportPatch, ImportRowModel
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.importer import service
from app.importer.diff import ProposedChange
from app.importer.parse import ParsedRow
from app.models import ImportDraft
from app.providers.registry import get_providers
from app.repo import get_draft, get_portfolio

router = APIRouter(tags=["imports"])


def _out(d: ImportDraft) -> ImportDraftOut:
    assert d.id is not None
    return ImportDraftOut(
        id=d.id,
        portfolio_id=d.portfolio_id,
        status=d.status,  # type: ignore[arg-type]
        rows=[ImportRowModel.model_validate(r) for r in d.rows],
        proposed_changes=[ProposedChange.model_validate(c) for c in d.proposed_changes],
    )


@router.post(
    "/portfolios/{portfolio_id}/imports",
    response_model=ImportDraftOut,
    status_code=status.HTTP_201_CREATED,
)
def create_import(
    portfolio_id: int,
    file: Annotated[UploadFile, File()],
    user: UserDep,
    db: DbDep,
    settings: SettingsDep,
) -> ImportDraftOut:
    assert user.id is not None
    p = get_portfolio(db, user.id, portfolio_id)
    if user.ocr_consent_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "OCR consent is required before uploading")
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image is too large")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    draft = service.build_draft(db, p, data, get_providers().ocr(), settings)
    return _out(draft)


@router.get("/imports/{draft_id}", response_model=ImportDraftOut)
def get_import(draft_id: int, user: UserDep, db: DbDep) -> ImportDraftOut:
    assert user.id is not None
    d, _ = get_draft(db, user.id, draft_id)
    return _out(d)


@router.patch("/imports/{draft_id}", response_model=ImportDraftOut)
def patch_import(
    draft_id: int, body: ImportPatch, user: UserDep, db: DbDep, settings: SettingsDep
) -> ImportDraftOut:
    assert user.id is not None
    d, p = get_draft(db, user.id, draft_id)
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
    return _out(d)


@router.post("/imports/{draft_id}/confirm", response_model=ImportDraftOut)
def confirm_import(
    draft_id: int, user: UserDep, db: DbDep, settings: SettingsDep
) -> ImportDraftOut:
    assert user.id is not None
    d, p = get_draft(db, user.id, draft_id)
    return _out(service.confirm_draft(db, d, p, settings))

"""Terms / disclaimer acceptance: every user must accept the current version before using the app."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.schemas import Body
from app.auth.deps import AuthDep, DbDep, SettingsDep, terms_accepted
from app.models import TermsAcceptance
from app.strictjson import StrictJsonRoute

router = APIRouter(tags=["terms"], route_class=StrictJsonRoute)


class TermsOut(BaseModel):
    version: str
    accepted: bool
    accepted_at: datetime | None = None


class TermsAcceptIn(Body):
    version: str


def _out(db: DbDep, auth: AuthDep, settings: SettingsDep) -> TermsOut:
    ok, at = terms_accepted(db, auth.user, settings)
    return TermsOut(version=settings.terms_version, accepted=ok, accepted_at=at)


@router.get("/terms", response_model=TermsOut)
def get_terms(auth: AuthDep, db: DbDep, settings: SettingsDep) -> TermsOut:
    return _out(db, auth, settings)


@router.post("/terms/accept", response_model=TermsOut)
def accept_terms(body: TermsAcceptIn, auth: AuthDep, db: DbDep, settings: SettingsDep) -> TermsOut:
    if body.version != settings.terms_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "terms_version_mismatch")
    assert auth.user.id is not None
    if not terms_accepted(db, auth.user, settings)[0]:
        db.add(TermsAcceptance(user_id=auth.user.id, version=body.version))
        db.commit()
    return _out(db, auth, settings)

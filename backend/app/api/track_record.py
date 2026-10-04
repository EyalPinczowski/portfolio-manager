"""`GET /api/track-record`: members only (login required, never public). See `app/trackrecord.py`."""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.deps import DbDep, SettingsDep, UserDep
from app.launchgate import GateDep
from app.strictjson import StrictJsonRoute
from app.trackrecord import TrackRecordOut, build_track_record

router = APIRouter(tags=["track-record"], route_class=StrictJsonRoute)


@router.get("/track-record", response_model=TrackRecordOut)
def track_record(user: UserDep, db: DbDep, settings: SettingsDep, gate: GateDep) -> TrackRecordOut:
    """Global paper calls whose horizon has ended, against S&P 500 / TA-125 buy-and-hold."""
    return build_track_record(db, settings, gate.evaluate())

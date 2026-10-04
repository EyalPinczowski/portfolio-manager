"""Search history and watchlist for Analyze a stock. Symbols only; strictly the caller's rows.

Nothing about an analysis (result, note, question, score) is stored. The watchlist shows the last
cached quote (the `price_quote` table) with its freshness; no provider is called here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from app.api.analyze import _symbol
from app.api.schemas import Body, Symbol
from app.auth.deps import DbDep, SettingsDep, UserDep
from app.errors import ApiError
from app.models import PriceQuote, Security, WatchlistItem
from app.portfolio.freshness import price_is_fresh
from app.scoring.screener import _valued
from app.securities import infer_security
from app.strictjson import StrictJsonRoute
from app.timeutil import as_utc, utcnow
from app.userlists import list_history, list_watchlist, new_watchlist_item, watchlist_item

router = APIRouter(tags=["lists"], route_class=StrictJsonRoute)


class SearchHistoryOut(BaseModel):
    symbol: str
    searched_at: datetime


class WatchlistAdd(Body):
    symbol: Symbol


class CachedQuoteOut(BaseModel):
    price: float
    currency: str
    as_of: datetime
    source: str
    basis: Literal["live", "last_close"]
    is_fresh: bool


class WatchlistOut(BaseModel):
    symbol: str
    market: str | None
    name: str | None
    added_at: datetime
    quote: CachedQuoteOut | None = None


@router.get("/search-history", response_model=list[SearchHistoryOut])
def get_search_history(user: UserDep, db: DbDep) -> list[SearchHistoryOut]:
    assert user.id is not None
    return [
        SearchHistoryOut(symbol=r.symbol, searched_at=as_utc(r.searched_at))
        for r in list_history(db, user.id)
    ]


@router.delete("/search-history", status_code=status.HTTP_204_NO_CONTENT)
def clear_search_history(user: UserDep, db: DbDep) -> Response:
    assert user.id is not None
    for r in list_history(db, user.id):
        db.delete(r)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/search-history/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def delete_search_history(symbol: str, user: UserDep, db: DbDep) -> Response:
    assert user.id is not None
    sym = _symbol(symbol)
    rows = [r for r in list_history(db, user.id) if r.symbol == sym]
    if not rows:
        raise ApiError(404, "not_found", "That symbol is not in your search history.")
    for r in rows:
        db.delete(r)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _cached_quote(db: DbDep, settings: SettingsDep, item_symbol: str) -> CachedQuoteOut | None:
    q = db.get(PriceQuote, item_symbol)
    if q is None or q.price <= 0:
        return None
    sec = db.get(Security, item_symbol) or infer_security(item_symbol)
    fresh = price_is_fresh(_valued(sec, q, 1.0, 1.0, 0), utcnow(), settings)
    return CachedQuoteOut(
        price=q.price,
        currency=q.currency,
        as_of=as_utc(q.as_of),
        source=q.source,
        basis=q.basis,  # type: ignore[arg-type]
        is_fresh=fresh,
    )


def _watch_out(db: DbDep, settings: SettingsDep, item: WatchlistItem) -> WatchlistOut:
    return WatchlistOut(
        symbol=item.symbol,
        market=item.market,
        name=item.name,
        added_at=as_utc(item.added_at),
        quote=_cached_quote(db, settings, item.symbol),
    )


@router.get("/watchlist", response_model=list[WatchlistOut])
def get_watchlist(user: UserDep, db: DbDep, settings: SettingsDep) -> list[WatchlistOut]:
    assert user.id is not None
    return [_watch_out(db, settings, i) for i in list_watchlist(db, user.id)]


@router.post("/watchlist", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def add_watchlist(
    body: WatchlistAdd, user: UserDep, db: DbDep, settings: SettingsDep
) -> WatchlistOut:
    assert user.id is not None
    sym = _symbol(body.symbol)
    if watchlist_item(db, user.id, sym) is not None:
        raise ApiError(409, "already_watched", f"{sym} is already on your watchlist.")
    if len(list_watchlist(db, user.id)) >= settings.max_watchlist_per_user:
        raise ApiError(
            422,
            "watchlist_full",
            f"Your watchlist is full ({settings.max_watchlist_per_user}); remove one first.",
        )
    item = new_watchlist_item(db, user.id, sym)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "already_watched", f"{sym} is already on your watchlist.") from None
    db.refresh(item)
    return _watch_out(db, settings, item)


@router.delete("/watchlist/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(symbol: str, user: UserDep, db: DbDep) -> Response:
    assert user.id is not None
    item = watchlist_item(db, user.id, _symbol(symbol))
    if item is None:
        raise ApiError(404, "not_found", "That symbol is not on your watchlist.")
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

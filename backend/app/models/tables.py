"""SQLModel tables (see docs/phase-1-spec.md, Data model)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.timeutil import utcnow


class User(SQLModel, table=True):
    __tablename__ = "user"
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    locale: str = "he"
    disclaimer_accepted_at: NaiveDatetime | None = None
    ocr_consent_at: NaiveDatetime | None = None
    telegram_chat_id: str | None = None
    is_admin: bool = False
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Invite(SQLModel, table=True):
    __tablename__ = "invite"
    code: str = Field(primary_key=True)
    created_by: int | None = Field(default=None, foreign_key="user.id")
    used_by: int | None = None
    expires_at: NaiveDatetime
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class AuthSession(SQLModel, table=True):
    """Server-side session. `id` is the sha256 of the random cookie token."""

    __tablename__ = "session"
    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    csrf_token: str
    expires_at: NaiveDatetime
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Security(SQLModel, table=True):
    __tablename__ = "security"
    symbol: str = Field(primary_key=True)
    name_en: str
    name_he: str = ""
    tase_number: str | None = Field(default=None, index=True)
    asset_type: str = "stock"  # stock|etf|crypto|fund|bond|cash
    market: str = "US"  # US|TASE|CRYPTO
    currency: str = "USD"  # normalised (TASE stocks are ILS, never ILA)
    sector: str = "Unknown"
    country: str = "Unknown"
    dual_listing_group: str | None = Field(default=None, index=True)


class Portfolio(SQLModel, table=True):
    __tablename__ = "portfolio"
    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    name: str
    base_currency: str = "ILS"
    risk_filter: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    tracking_started_at: date | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Holding(SQLModel, table=True):
    __tablename__ = "holding"
    __table_args__ = (UniqueConstraint("portfolio_id", "symbol"),)
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True)
    symbol: str = Field(index=True)
    quantity: float
    avg_cost: float | None = None
    cost_currency: str = "USD"
    horizon: str | None = None  # 1w|1m|3m|6m|1y
    risk_override: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class HoldingsSnapshot(SQLModel, table=True):
    __tablename__ = "holdings_snapshot"
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True)
    taken_at: NaiveDatetime = Field(default_factory=utcnow)
    source: str = "screenshot"  # screenshot|manual
    rows: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))


class ImportDraft(SQLModel, table=True):
    __tablename__ = "import_draft"
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True)
    rows: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "draft"  # draft|confirmed|discarded
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Transaction(SQLModel, table=True):
    __tablename__ = "transaction"
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True)
    symbol: str | None = None
    type: str  # buy|sell|deposit|withdrawal
    quantity: float | None = None
    price: float | None = None
    amount: float  # positive magnitude, in `currency`
    currency: str = "ILS"
    fx_to_ils: float = 1.0  # FX rate on the trade date (ILS per 1 unit of `currency`)
    date: date
    inferred: bool = False


class PortfolioSnapshot(SQLModel, table=True):
    __tablename__ = "portfolio_snapshot"
    __table_args__ = (UniqueConstraint("portfolio_id", "date"),)
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True)
    date: date
    value_ils: float
    value_usd: float
    net_flow_ils: float = 0.0


class PriceQuote(SQLModel, table=True):
    __tablename__ = "price_quote"
    symbol: str = Field(primary_key=True)
    price: float  # normalised (ILA -> ILS)
    currency: str
    change_pct: float | None = None
    as_of: NaiveDatetime = Field(default_factory=utcnow)


class PriceAlert(SQLModel, table=True):
    __tablename__ = "price_alert"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    symbol: str
    op: str  # above|below
    price: float
    active: bool = True
    triggered_at: NaiveDatetime | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Notification(SQLModel, table=True):
    __tablename__ = "notification"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str
    title: str
    body: str
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    read: bool = False


class SignalCache(SQLModel, table=True):
    """Cached combined score card per symbol (market data, not user data)."""

    __tablename__ = "signal_cache"
    symbol: str = Field(primary_key=True)
    computed_at: NaiveDatetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

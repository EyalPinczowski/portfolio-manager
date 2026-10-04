"""SQLModel tables (see docs/phase-1-spec.md, Data model)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import NaiveDatetime
from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint, text
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
    # Random per user; the known-device cookie carries it. Rotated on logout, so a copied cookie (or
    # a reused SQLite id) stops being a known device. Null (legacy rows) means "no device is known".
    device_nonce: str | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Invite(SQLModel, table=True):
    __tablename__ = "invite"
    code: str = Field(primary_key=True)
    created_by: int | None = Field(default=None, foreign_key="user.id", ondelete="CASCADE")
    used_by: int | None = Field(default=None, foreign_key="user.id", ondelete="SET NULL")
    expires_at: NaiveDatetime
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class AuthSession(SQLModel, table=True):
    """Server-side session. `token_hash` is the sha256 of the random cookie token (the token itself
    is never stored); `id` is the public, listable id used by the sessions endpoints."""

    __tablename__ = "session"
    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    csrf_token: str
    expires_at: NaiveDatetime
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    last_seen_at: NaiveDatetime = Field(default_factory=utcnow)


class Security(SQLModel, table=True):
    __tablename__ = "security"
    symbol: str = Field(primary_key=True)
    name_en: str
    name_he: str = ""
    tase_number: str | None = Field(default=None, index=True)
    asset_type: str = "stock"  # stock|etf|crypto|fund|bond|cash
    market: str = "US"  # US|TASE|CRYPTO
    currency: str = "USD"  # normalised (TASE stocks are ILS, never ILA)
    # The currency Yahoo last reported for this symbol, raw (TASE stocks: "ILA" = agorot). Written
    # after every successful lookup and read as the fallback when the lookup is rate limited, so a
    # restart under a 429 does not blank TASE quotes. Unlike `currency` it can say agorot.
    yahoo_currency: str | None = None
    sector: str = "Unknown"
    country: str = "Unknown"
    dual_listing_group: str | None = Field(default=None, index=True)
    # Seeded rows are verified. A ticker a user typed in is created unverified and becomes verified
    # when the quote provider returns a price for it. Search lists verified securities only, so one
    # user's watchlist never shows up in another user's search.
    verified: bool = True


class Portfolio(SQLModel, table=True):
    __tablename__ = "portfolio"
    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    name: str
    base_currency: str = "ILS"
    risk_filter: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    tracking_started_at: date | None = None
    # The return the user expects, in percent over `expected_return_horizon_months`. Set by the user
    # only (never defaulted); the post-mortem says `needs_expectation` while either is null.
    expected_return_pct: float | None = None
    expected_return_horizon_months: int | None = None
    # When a screenshot import was last confirmed (aware-UTC semantics, stored naive like the rest).
    # Drives "Last updated from a screenshot" and the nudge (`update_is_stale`).
    last_screenshot_update_at: NaiveDatetime | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Holding(SQLModel, table=True):
    __tablename__ = "holding"
    __table_args__ = (UniqueConstraint("portfolio_id", "symbol"),)
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True, ondelete="CASCADE")
    symbol: str = Field(index=True)
    quantity: float
    avg_cost: float | None = None
    cost_currency: str = "USD"
    horizon: str | None = None  # 1w|1m|3m|6m|1y
    risk_override: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class HoldingsSnapshot(SQLModel, table=True):
    __tablename__ = "holdings_snapshot"
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True, ondelete="CASCADE")
    taken_at: NaiveDatetime = Field(default_factory=utcnow)
    source: str = "screenshot"  # screenshot|manual
    rows: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))


class ImportDraft(SQLModel, table=True):
    __tablename__ = "import_draft"
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True, ondelete="CASCADE")
    rows: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    proposed_changes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "draft"  # draft|confirmed|discarded
    # partial: holdings missing from the screenshots are left alone; full: they are listed as
    # "not in these screenshots" for the user to decide (sold / withdrawn / keep).
    scope: str = Field(default="partial", sa_column_kwargs={"server_default": "partial"})
    created_at: NaiveDatetime = Field(default_factory=utcnow)


_PENDING_ONLY = text("type = 'pending_buy'")


class Transaction(SQLModel, table=True):
    __tablename__ = "transaction"
    __table_args__ = (
        # One outstanding `pending_buy` marker per holding (ordinary rows have no holding_id).
        Index(
            "uq_transaction_pending_holding",
            "holding_id",
            unique=True,
            sqlite_where=_PENDING_ONLY,
            postgresql_where=_PENDING_ONLY,
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True, ondelete="CASCADE")
    symbol: str | None = None
    type: str  # buy|sell|deposit|withdrawal
    holding_id: int | None = None  # only set on a `pending_buy` marker (see portfolio/valuation)
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
    portfolio_id: int = Field(foreign_key="portfolio.id", index=True, ondelete="CASCADE")
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
    source: str = Field(default="yfinance", sa_column_kwargs={"server_default": "yfinance"})
    basis: str = Field(
        default="live", sa_column_kwargs={"server_default": "live"}
    )  # live|last_close
    flag: str | None = None


class PriceAlert(SQLModel, table=True):
    __tablename__ = "price_alert"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    symbol: str
    op: str  # above|below
    price: float
    active: bool = True
    triggered_at: NaiveDatetime | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class SearchHistory(SQLModel, table=True):
    """Symbols a user analyzed, nothing else: no result, note, question or score is stored."""

    __tablename__ = "search_history"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_search_history_user_symbol"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    symbol: str
    searched_at: NaiveDatetime = Field(default_factory=utcnow)


class WatchlistItem(SQLModel, table=True):
    """A symbol the user follows. `market` and `name` are copied from a known Security, if any."""

    __tablename__ = "watchlist_item"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    symbol: str
    market: str | None = None
    name: str | None = None
    added_at: NaiveDatetime = Field(default_factory=utcnow)


class Notification(SQLModel, table=True):
    __tablename__ = "notification"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
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


class PaperCall(SQLModel, table=True):
    """One forward paper-trading call. **Append-only**: after insert only the resolution fields
    (`RESOLUTION_FIELDS`) may change, and only once. The ORM guard lives in `app/papertrading.py`
    (a `before_update` listener); on Postgres the migration also installs a trigger. The launch
    gate reads the global calls (`is_global`); per-user paper portfolios use `user_id`."""

    __tablename__ = "paper_call"
    __table_args__ = (
        CheckConstraint(
            "(is_global AND user_id IS NULL) OR (NOT is_global AND user_id IS NOT NULL)",
            name="ck_paper_call_scope",
        ),
    )
    id: int | None = Field(default=None, primary_key=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True, ondelete="CASCADE")
    is_global: bool = False
    symbol: str = Field(index=True)
    side: str  # buy|sell: what the call said, as the paper portfolio simulates it
    horizon: str  # 1w|1m|3m|6m|1y
    entry: float
    stop: float | None = None
    targets: list[float] = Field(default_factory=list, sa_column=Column(JSON))
    explanation: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    model_hash: str
    prompt_hash: str
    weights_hash: str = Field(index=True)
    # ---- resolution (the only fields that may change, once) ----
    resolved_at: NaiveDatetime | None = None
    outcome: str | None = None  # target_hit|stop_hit|horizon_end|error
    outcome_price: float | None = None
    benchmark_returns: dict[str, float] | None = Field(default=None, sa_column=Column(JSON))


class BacktestRun(SQLModel, table=True):
    """A finished backtest of one weights config (immutable: the ORM refuses updates)."""

    __tablename__ = "backtest_run"
    id: int | None = Field(default=None, primary_key=True)
    weights_hash: str = Field(index=True)
    period_start: date
    period_end: date
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    passed: bool
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)


class LlmUsage(SQLModel, table=True):
    """Per provider, model and UTC day: how many calls were made, tokens used and how many times a
    role fell back to its template. Incremented atomically in SQL, so concurrent processes agree."""

    __tablename__ = "llm_usage"
    provider: str = Field(primary_key=True)
    model: str = Field(primary_key=True)
    day: date = Field(primary_key=True)
    requests: int = 0
    tokens: int = 0
    fallbacks: int = 0


class LlmBucket(SQLModel, table=True):
    """Token bucket per provider (shared by every process). `version` makes each refill-and-take a
    compare-and-swap, which works the same on SQLite and Postgres."""

    __tablename__ = "llm_bucket"
    provider: str = Field(primary_key=True)
    tokens: float
    version: int = 0
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class LlmCache(SQLModel, table=True):
    """Validated LLM answers keyed by the hash of their input (never templates, never inputs)."""

    __tablename__ = "llm_cache"
    key: str = Field(primary_key=True)
    role: str = Field(index=True)
    provider: str
    model: str
    response: str  # the validated JSON of the output model
    created_at: NaiveDatetime = Field(default_factory=utcnow, index=True)

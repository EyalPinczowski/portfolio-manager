"""All thresholds, weights, intervals and switches live here (never inline)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.verdict_words import VERDICT_WORDS

DISCLAIMER = "Not financial advice."
DEFAULT_SECRET_KEY = "change-me-in-production"

SIGNAL_NAMES: tuple[str, ...] = (
    "technical",
    "patterns",
    "fundamentals",
    "analysts",
    "geo_news",
    "sentiment",
)
HORIZONS: tuple[str, ...] = ("1w", "1m", "3m", "6m", "1y")  # "1y" means 1y+
IMPLEMENTED_SIGNALS: frozenset[str] = frozenset({"technical", "patterns"})


Timeframe = Literal["4h", "1d", "1wk", "1mo"]
TakeProfitSource = Literal[
    "resistance",  # next resistance on the horizon's chart
    "weekly_resistance",
    "long_term_resistance",
    "r_multiple",  # a multiple of the distance to the stop (between r_multiple_min and _max)
    "upper_bollinger",
    "analyst_mean_target",
    "analyst_high_target",
    "fibonacci_extension",
    "trailing_only",  # no fixed take-profit: trail the stop
]
StopStructure = Literal[
    "minor_support", "swing_low", "major_support", "weekly_swing_low", "multi_month_support"
]


class QuoteSourceLimits(BaseModel):
    """Cache, rate and back-off settings of one price source (config, never code constants)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ttl_seconds: float = Field(default=300.0, ge=0)  # a fetched quote/bar set is reused this long
    max_calls_per_minute: int = Field(default=30, gt=0)  # our own budget, below the vendor's
    breaker_cooldown_seconds: float = Field(default=900.0, ge=0)  # pause after a 403/429
    timeout_seconds: float = Field(default=8.0, gt=0)


def default_quote_source_limits() -> dict[str, QuoteSourceLimits]:
    """quote-sources-2026-10-03.md: Finnhub 60/min, CoinGecko demo 30/min, Stooq quota unpublished,
    FMP 250/day, FX daily rates cached 24 h."""
    return {
        "finnhub": QuoteSourceLimits(ttl_seconds=300, max_calls_per_minute=50),
        "coingecko": QuoteSourceLimits(ttl_seconds=180, max_calls_per_minute=20),
        "stooq": QuoteSourceLimits(ttl_seconds=6 * 3600, max_calls_per_minute=10),
        "fmp": QuoteSourceLimits(ttl_seconds=24 * 3600, max_calls_per_minute=10),
        "frankfurter": QuoteSourceLimits(ttl_seconds=24 * 3600, max_calls_per_minute=10),
        "boi": QuoteSourceLimits(ttl_seconds=24 * 3600, max_calls_per_minute=10),
        # GemelNet (data.gov.il CKAN) publishes monthly: a long TTL, a small budget.
        "gemelnet": QuoteSourceLimits(
            ttl_seconds=12 * 3600, max_calls_per_minute=10, timeout_seconds=15.0
        ),
    }


class ScaleOutPlanSpec(BaseModel):
    """One preset's scale-out plan: shares at TP1 and TP2, the rest trails (README, exit levels).

    More conservative = secure profit earlier (bigger first share, tighter trail, earlier
    breakeven); more aggressive = smaller first share, wider trail, more left to run.
    """

    first_fraction: float = Field(gt=0, lt=1)  # share of the position at TP1
    second_fraction: float = Field(ge=0, lt=1)  # share at TP2
    trail_atr_scale: float = Field(gt=0)  # multiplies the trailing ATR multiple
    breakeven_atr_multiple: float = Field(gt=0)  # gain (in ATR) that suggests a breakeven stop

    @model_validator(mode="after")
    def _remainder_trails(self) -> ScaleOutPlanSpec:
        if self.first_fraction + self.second_fraction >= 1.0:
            raise ValueError("first + second fraction must leave a share to trail")
        return self

    @property
    def trail_fraction(self) -> float:
        return 1.0 - self.first_fraction - self.second_fraction


def default_scale_out_plans() -> dict[str, ScaleOutPlanSpec]:
    rows = {
        # preset: (first, second, trail scale, breakeven ATR)
        "very_conservative": (0.50, 0.30, 0.90, 0.5),
        "conservative": (0.45, 0.30, 0.95, 0.75),
        "balanced": (0.40, 0.30, 1.00, 1.0),
        "balanced_aggressive": (0.35, 0.30, 1.05, 1.25),
        "aggressive": (0.25, 0.25, 1.15, 1.5),
        "very_aggressive": (0.20, 0.20, 1.30, 2.0),
    }
    return {
        k: ScaleOutPlanSpec(
            first_fraction=a, second_fraction=b, trail_atr_scale=c, breakeven_atr_multiple=d
        )
        for k, (a, b, c, d) in rows.items()
    }


class HorizonSpec(BaseModel):
    """How exit levels are computed for one holding period (the README horizon table)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    chart_timeframes: list[Timeframe] = Field(min_length=1)  # primary chart first
    atr_timeframe: Timeframe
    atr_period: int = Field(gt=0)
    # Stop distance as a multiple of ATR; both None for a purely structural stop (1y+).
    atr_multiple_min: float | None = Field(default=None, gt=0)
    atr_multiple_max: float | None = Field(default=None, gt=0)
    stop_ma_period: int | None = Field(default=None, gt=0)  # the moving average to stop below
    stop_structure: list[StopStructure] = Field(default_factory=list)
    take_profit_sources: list[TakeProfitSource] = Field(min_length=1)
    r_multiple_min: float | None = Field(default=None, gt=0)
    r_multiple_max: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _ranges(self) -> HorizonSpec:
        for lo, hi, what in (
            (self.atr_multiple_min, self.atr_multiple_max, "atr_multiple"),
            (self.r_multiple_min, self.r_multiple_max, "r_multiple"),
        ):
            if (lo is None) != (hi is None):
                raise ValueError(f"{what}_min and {what}_max must be set together")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{what}_min must not exceed {what}_max")
        if ("r_multiple" in self.take_profit_sources) != (self.r_multiple_min is not None):
            raise ValueError("r_multiple needs a range, and a range needs the r_multiple source")
        if self.atr_multiple_min is None and not (self.stop_ma_period or self.stop_structure):
            raise ValueError("a stop needs an ATR multiple, a moving average or a structure")
        return self


def default_horizon_table() -> dict[str, HorizonSpec]:
    """README -> Filters -> "1. Holding period". Change the numbers in config, not in code."""
    return {
        "1w": HorizonSpec(
            label="1 week", chart_timeframes=["1d", "4h"], atr_timeframe="1d", atr_period=14,
            atr_multiple_min=1.0, atr_multiple_max=1.5, stop_structure=["minor_support"],
            take_profit_sources=["resistance", "r_multiple"], r_multiple_min=1.5, r_multiple_max=2.0,
        ),
        "1m": HorizonSpec(
            label="1 month", chart_timeframes=["1d"], atr_timeframe="1d", atr_period=14,
            atr_multiple_min=2.0, atr_multiple_max=2.0, stop_ma_period=20,
            stop_structure=["swing_low"],
            take_profit_sources=["resistance", "r_multiple", "upper_bollinger"],
            r_multiple_min=2.0, r_multiple_max=2.0,
        ),
        "3m": HorizonSpec(
            label="3 months", chart_timeframes=["1d", "1wk"], atr_timeframe="1d", atr_period=14,
            atr_multiple_min=2.5, atr_multiple_max=3.0, stop_ma_period=50,
            stop_structure=["major_support"],
            take_profit_sources=["weekly_resistance", "r_multiple", "analyst_mean_target"],
            r_multiple_min=2.0, r_multiple_max=3.0,
        ),
        "6m": HorizonSpec(
            label="6 months", chart_timeframes=["1wk"], atr_timeframe="1wk", atr_period=14,
            atr_multiple_min=2.0, atr_multiple_max=2.0, stop_ma_period=100,
            stop_structure=["weekly_swing_low"],
            take_profit_sources=[
                "analyst_mean_target", "analyst_high_target", "r_multiple", "fibonacci_extension",
            ],
            r_multiple_min=3.0, r_multiple_max=3.0,
        ),
        "1y": HorizonSpec(
            label="1 year+", chart_timeframes=["1wk", "1mo"], atr_timeframe="1wk", atr_period=14,
            stop_ma_period=200, stop_structure=["multi_month_support"],
            take_profit_sources=["analyst_high_target", "long_term_resistance", "trailing_only"],
        ),
    }  # fmt: skip


def _default_weights() -> dict[str, float]:
    return {
        "technical": 25.0,
        "patterns": 10.0,
        "fundamentals": 20.0,
        "analysts": 20.0,
        "geo_news": 12.5,
        "sentiment": 12.5,
    }


def _default_tase_hours() -> dict[str, tuple[str, str]]:
    """TASE override: Mon-Thu 09:59-17:25, Fri 09:59-13:50 (Asia/Jerusalem)."""
    regular = ("09:59", "17:25")
    return {
        "mon": regular,
        "tue": regular,
        "wed": regular,
        "thu": regular,
        "fri": ("09:59", "13:50"),
    }


class BacktestTarget(BaseModel):
    """Success bar for one risk preset over one backtest window (approved by the user 2026-10-04)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_return_pct: float  # the window's return must be at least this
    max_drawdown_pct: float = Field(gt=0)  # and the worst peak-to-trough fall at most this


def default_backtest_targets() -> dict[str, BacktestTarget]:
    # Per 6-month window. Approved by the user 2026-10-04.
    return {
        "conservative": BacktestTarget(min_return_pct=3.0, max_drawdown_pct=4.0),
        "balanced": BacktestTarget(min_return_pct=5.0, max_drawdown_pct=7.0),
        "balanced_aggressive": BacktestTarget(min_return_pct=8.0, max_drawdown_pct=10.0),
        "aggressive": BacktestTarget(min_return_pct=12.0, max_drawdown_pct=15.0),
    }


class Settings(BaseSettings):
    # env_parse_none_str: "none" in the environment means None (e.g. DATABASE_PREPARE_THRESHOLD=none).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_parse_none_str="none"
    )

    # --- core ---
    # "production" is the default so a forgotten variable can never expose docs or insecure cookies.
    # Local development sets ENV=dev (and COOKIE_SECURE=false when serving plain http).
    env: Literal["dev", "production"] = "production"
    # SQLite for dev, Postgres for the cloud. Exact forms: backend/README.md ("Database URLs").
    # `postgres://` and `postgresql://` are rewritten to the installed psycopg 3 driver.
    database_url: str = "sqlite:///./portfolio.db"
    # Run `alembic upgrade head` when the API / scheduler / CLI starts. Unset: true in dev and
    # false in production (there, run `alembic upgrade head` as a release step; the API then only
    # checks that the schema is at head and refuses to start otherwise).
    auto_migrate: bool | None = None
    # Rollback safety (docs/migrations.md). An older image started against a database that a newer
    # release already migrated ("DB ahead") keeps running when the newer revisions are expand-only:
    # at most this many numbered revisions ahead, and none of them a `contract` revision.
    db_ahead_max_revisions: int = 1
    # Extra revision ids to accept as "ahead and compatible" (operator override, comma separated).
    db_accepted_ahead_revisions: list[str] = Field(default_factory=list)
    # Postgres only. Prepared statements: psycopg prepares a query after this many uses. Set it to
    # "none" (disabled) behind Supabase's transaction pooler (port 6543), which does not support them.
    database_prepare_threshold: int | None = 5
    # Postgres only. Sized for a 512 MB host with one worker and a small connection limit
    # (Supabase free: about 15 pooled / 60 direct connections): at most pool_size + max_overflow.
    db_pool_size: int = 3
    db_max_overflow: int = 2
    db_pool_pre_ping: bool = True  # detect connections the pooler or server dropped
    db_pool_recycle_seconds: int = 1800  # replace connections older than this
    db_pool_timeout_seconds: float = 30.0
    secret_key: str = DEFAULT_SECRET_KEY
    cors_origins: list[str] = Field(default_factory=list)

    # --- auth ---
    cookie_name: str = "pm_session"
    cookie_secure: bool = True  # must stay True in production (the app refuses to start otherwise)
    session_ttl_hours: int = 24 * 14
    session_touch_seconds: int = 300  # how often `last_seen_at` is refreshed
    session_purge_interval_minutes: int = 60  # expired session rows are deleted this often
    password_min_length: int = 10
    login_rate_limit_attempts: int = 5  # free failures per email before backoff starts
    login_rate_limit_ip_multiplier: int = 4  # a shared IP gets this many times more free failures
    # Email-only backoff (all IPs together) tolerates this many times more failures than one
    # (email, IP) pair, so a stranger cannot lock an account out; a known device is exempt.
    login_rate_limit_email_multiplier: int = 20
    device_cookie_name: str = "pm_device"
    device_cookie_days: int = 90
    device_cookie_max_users: int = 5  # accounts remembered per browser
    login_rate_limit_window_seconds: int = 300  # failures are forgotten / backoff capped at this
    login_backoff_base_seconds: float = 15.0  # first block; doubles with every further failure
    signup_rate_limit_per_hour: int = 10  # per IP, every attempt counts
    upload_rate_limit_per_hour: int = 30  # per user, screenshot and on-device rows imports
    import_edit_rate_limit_per_hour: int = 300  # per user, PATCH of an import draft (review edits)
    holding_add_rate_limit_per_hour: int = 120  # per user, manual POST of a holding
    # Client IP behind the Cloudflare Pages Function proxy (docs/deployment.md). The Function sends
    # the visitor's IP in `trusted_proxy_header` (e.g. "X-Client-IP") and the shared secret in
    # `proxy_auth_header`. The IP header is trusted ONLY when the secret matches (constant-time
    # compare); the app refuses to start when the header is configured and no secret is set.
    trusted_proxy_header: str | None = None
    proxy_shared_secret: str | None = None  # env PROXY_SHARED_SECRET; at least 16 characters
    proxy_auth_header: str = "X-Proxy-Auth"
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)  # optionally also restrict peers
    # Used when the proxy header is not trusted for a request: the host's own client-IP header
    # (Render sets CF-Connecting-IP). Off by default because a client can forge it when the API is
    # reachable directly. Without either, the TCP peer address is used.
    fallback_ip_header: str | None = None
    argon2_memory_kib: int = 19 * 1024  # OWASP: m=19 MiB, t=2, p=1
    argon2_time_cost: int = 2
    argon2_parallelism: int = 1
    argon2_max_concurrent: int = 2  # concurrent hashes (memory bound on a 512 MB host)
    # Cloudflare Turnstile on login after repeated failures for one (email, IP). `turnstile_enabled`
    # unset means: off in dev, on in production when both keys are set.
    turnstile_enabled: bool | None = None
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None
    turnstile_after_failures: int = 3  # failures of one (email, IP) pair
    # Failures of the email key (all IPs together) that also require a challenge, so a distributed
    # guesser meets Turnstile too. A known device is exempt (as from the email backoff).
    turnstile_email_after_failures: int = 10
    # Cloudflare echoes the widget's hostname and action: a token minted elsewhere is refused.
    # Empty hostnames: the hostnames of `cors_origins`. Empty action: not checked.
    turnstile_allowed_hostnames: list[str] = Field(default_factory=list)
    turnstile_expected_action: str = "login"
    turnstile_verify_url: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    turnstile_timeout_seconds: float = 5.0
    max_alerts_per_user: int = 50
    max_search_history_per_user: int = Field(default=50, ge=1)  # oldest rows are pruned
    max_watchlist_per_user: int = Field(default=100, ge=1)  # adding past it is a 422
    invite_ttl_days: int = 14
    admin_max_invite_days: int = Field(default=90, ge=1)  # longest invite an admin can create
    admin_invites_per_hour: int = Field(default=30, ge=1)  # per admin

    # --- market data ---
    benchmark_sp500: str = "^GSPC"
    benchmark_ta125: str = "^TA125.TA"
    # Paper-call resolution: benchmark closes may lie at most this many days before the date asked
    # (weekends, holidays); history is fetched this many days further back than the call's age.
    paper_benchmark_max_gap_days: int = Field(default=5, ge=0)
    paper_benchmark_history_padding_days: int = Field(default=10, ge=0)
    # Scheduler job that resolves paper calls whose horizon has ended (leader only).
    paper_resolve_interval_minutes: int = Field(default=60, ge=1, le=1440)
    paper_resolve_batch_size: int = Field(default=200, ge=1, le=5000)
    # A horizon-end price needs a close this close to the horizon's last day, else the call stays
    # open and is retried (never a made-up result).
    paper_resolve_max_gap_days: int = Field(default=5, ge=0)
    fx_symbol: str = "ILS=X"
    fx_fallback_usd_ils: float = 3.6  # last resort only; always reported as stale
    fx_stale_after_hours: float = 72.0
    quote_stale_after_minutes: int = 30
    # Exit levels are only suggested on a price this fresh. Per market, in minutes. While the market
    # is closed the last session's closing quote still counts (within the same window of the close).
    price_fresh_window_minutes: dict[str, int] = Field(
        default_factory=lambda: {"US": 60, "TASE": 60, "CRYPTO": 30}
    )
    # --- fallback price sources (docs/reviews/quote-sources-2026-10-03.md) ---
    # A source with no key is disabled (never an error). Yahoo stays the primary.
    finnhub_api_key: str | None = None  # US live quote, free 60/min
    coingecko_api_key: str | None = None  # crypto, demo key (attribution required)
    fmp_api_key: str | None = None  # US daily history, free 250 calls/day
    stooq_api_key: str | None = None  # US last close + history (optional third source)
    quote_fallback_enabled: bool = True
    # Source order per market (first success wins). TASE has no free fallback: it keeps the last
    # stored close, labelled with its date, and is never fresh through a fallback.
    quote_fallback_order: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "US": ["finnhub", "stooq"],
            "CRYPTO": ["coingecko"],
            "TASE": [],
            "FX": ["frankfurter", "boi"],
        }
    )
    history_fallback_order: dict[str, list[str]] = Field(
        default_factory=lambda: {"US": ["fmp", "stooq"], "CRYPTO": [], "TASE": []}
    )
    quote_source_limits: dict[str, QuoteSourceLimits] = Field(
        default_factory=default_quote_source_limits
    )
    finnhub_base_url: str = "https://finnhub.io/api/v1"
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    stooq_base_url: str = "https://stooq.com"
    fmp_base_url: str = "https://financialmodelingprep.com"
    frankfurter_base_url: str = "https://api.frankfurter.dev/v1"
    boi_rates_url: str = "https://www.boi.org.il/PublicApi/GetExchangeRates?asXml=true"
    # Yahoo crypto symbol (BTC-USD) -> CoinGecko coin id.
    coingecko_ids: dict[str, str] = Field(
        default_factory=lambda: {
            "BTC-USD": "bitcoin",
            "ETH-USD": "ethereum",
            "SOL-USD": "solana",
            "XRP-USD": "ripple",
            "ADA-USD": "cardano",
            "DOGE-USD": "dogecoin",
            "LTC-USD": "litecoin",
            "BNB-USD": "binancecoin",
        }
    )
    # Two live sources more than this far apart: the quote is flagged `price_disagreement` (and is
    # never "fresh" for exit levels) instead of being stored silently.
    quote_disagreement_pct: float = 5.0
    # Cross-check the primary against a live fallback for at most this many symbols per cycle.
    quote_crosscheck_max_symbols: int = 10
    # A fallback history is used only when it matches the primary's overlapping closes this closely;
    # otherwise it carries `history_source_mismatch`.
    history_mismatch_tolerance_pct: float = 2.0
    history_overlap_min_days: int = 5
    # A USD/ILS rate outside this range is a parse error, not a rate.
    fx_plausible_range: tuple[float, float] = (1.5, 8.0)
    # The holding-period table that drives exit levels (README "Holding period"). Keys must be
    # exactly HORIZONS; override it as JSON in the HORIZON_TABLE env var.
    horizon_table: dict[str, HorizonSpec] = Field(default_factory=default_horizon_table)
    # Exit-level tunables (`scoring/exit_levels.py`); the per-horizon numbers are in `horizon_table`.
    exit_levels_min_bars: int = Field(default=60, gt=0)  # fewer daily bars: no levels at all
    exit_levels_support_buffer_pct: float = Field(
        default=0.5, ge=0
    )  # a stop sits this far under a level
    exit_levels_min_stop_atr: float = Field(
        default=1.0, gt=0
    )  # a chart stop closer than this is noise
    # Stop width by preset: multiplies the middle of the horizon's ATR range (riskier = wider).
    exit_levels_preset_atr_scale: dict[str, float] = Field(
        default_factory=lambda: {
            "very_conservative": 0.85,
            "conservative": 0.9,
            "balanced": 1.0,
            "balanced_aggressive": 1.05,
            "aggressive": 1.15,
            "very_aggressive": 1.25,
        }
    )
    # A chart level this far (in ATRs) beyond the ATR stop may replace it, so the stop sits behind it.
    exit_levels_structure_reach_atr: float = Field(default=1.5, ge=0)
    # History and live quote must agree within this many percent (a x100 agorot slip never passes).
    exit_levels_max_history_gap_pct: float = Field(default=50.0, gt=0)
    exit_levels_crypto_atr_multiplier: float = Field(default=1.5, gt=0)  # crypto stops are wider
    exit_levels_chandelier_lookback: int = Field(default=22, gt=1)  # bars for the highest high
    exit_levels_trailing_atr_default: float = Field(
        default=3.0, gt=0
    )  # horizons without an ATR range
    # Scale-out plan per risk preset (proposed defaults, for the user to review; a custom filter
    # without a preset uses `exit_levels_scale_out_fallback_preset`).
    exit_levels_scale_out_plans: dict[str, ScaleOutPlanSpec] = Field(
        default_factory=default_scale_out_plans
    )
    exit_levels_scale_out_fallback_preset: str = "balanced"
    exit_levels_max_take_profits: int = Field(default=3, gt=0)
    exit_levels_pivot_cluster_pct: float = Field(default=1.5, gt=0)
    exit_levels_pivot_windows: dict[str, int] = Field(
        default_factory=lambda: {
            "minor_support": 3,
            "swing_low": 5,
            "major_support": 10,
            "weekly_swing_low": 3,
            "multi_month_support": 2,
            "resistance": 5,
            "weekly_resistance": 3,
            "long_term_resistance": 3,
        }
    )
    exit_levels_fib_extensions: list[float] = Field(default_factory=lambda: [1.272, 1.618])
    exit_levels_fib_lookback_bars: int = Field(default=120, gt=1)  # daily bars for the swing
    exit_levels_bollinger_period: int = Field(default=20, gt=1)
    exit_levels_dedupe_pct: float = Field(default=0.5, ge=0)  # take-profits closer than this merge
    # Review: a saved stop is "too tight" under this many ATRs and "too wide" over that many.
    exit_levels_too_tight_atr: float = Field(default=1.0, gt=0)
    exit_levels_too_wide_atr: float = Field(default=5.0, gt=0)
    exit_review_top_contributors: int = Field(default=5, gt=0)
    history_cache_ttl_seconds: int = 6 * 3600
    history_failure_ttl_seconds: int = 300
    history_cache_max_entries: int = 200  # one entry per symbol; the oldest is evicted
    provider_small_cache_max_entries: int = 5000  # currencies and failure markers
    history_days: int = 420
    provider_max_retries: int = 3
    provider_backoff_base_seconds: float = 2.0
    # Which currencies Yahoo may report per symbol suffix. A lookup outside the list is ignored (a
    # USD answer for TEVA.TA would make the stored ILA wrong and every TASE price x100). Symbols
    # without a matching suffix are unrestricted.
    yahoo_currency_allowed: dict[str, list[str]] = Field(
        default_factory=lambda: {".TA": ["ILA", "ILS"], "-USD": ["USD"]}
    )
    currency_cache_ttl_seconds: int = 7 * 24 * 3600
    provider_breaker_threshold: int = 2  # consecutive all-empty quote fetches before backing off
    provider_breaker_cooldown_seconds: float = 300.0
    provider_single_retry_max: int = 5  # tickers retried one by one after a batch came back empty

    # --- scheduler ---
    # True: the API process runs the jobs (single-host deployment, docs/deployment.md) behind a
    # leader lock, so a second instance serves requests but never runs the jobs. False: run
    # `python -m app.scheduler` as its own process.
    scheduler_in_process: bool = False
    scheduler_lock_path: str | None = None  # SQLite leader lock file; default: next to the DB file
    scheduler_lock_id: int = 0x504D5343  # Postgres advisory-lock key ("PMSC")
    # Postgres leader lock connection. Session-level advisory locks do not work through a
    # transaction pooler (port 6543): point this at the session pooler / direct URL if DATABASE_URL
    # uses the transaction pooler. Default: DATABASE_URL.
    scheduler_lock_database_url: str | None = None
    scheduler_leader_check_seconds: int = 30  # standby retries the lock; the leader verifies it
    scheduler_max_workers: int = 1  # in-process job threads (jobs run one after another)
    quotes_interval_minutes: int = 5
    scores_interval_minutes: int = 30
    score_cache_ttl_minutes: int = 360
    # --- analyze a stock (analyze/): one cached row per symbol, nothing is fetched while it is fresh ---
    analyze_cache_ttl_minutes: int = Field(default=15, gt=0)
    analyze_levels_per_side: int = Field(
        default=3, ge=1, le=10
    )  # support / resistance levels shown
    # --- screener / universe (scoring/screener.py, scheduler job run_universe_score_refresh) ---
    universe_file: str | None = None  # symbols, one per line; default: app/data/universe_seed.txt
    universe_refresh_interval_minutes: int = Field(default=15, gt=0)
    universe_refresh_batch_size: int = Field(default=20, gt=0)  # scorecards (history calls) per run
    universe_score_ttl_minutes: int = Field(default=720, gt=0)  # re-score a symbol after this long
    universe_quote_chunk_size: int = Field(default=50, gt=0)  # symbols per batched quote call
    universe_pause_seconds: float = Field(default=0.5, ge=0)  # between history calls (rate limits)
    screener_top_n: int = Field(default=10, gt=0)
    screener_max_skipped: int = Field(default=300, gt=0)
    screener_min_score: float = Field(default=10.0, ge=-100, le=100)  # cached total score floor
    screener_min_confidence: float = Field(default=0.2, ge=0, le=1)
    screener_max_score_age_hours: int = Field(default=48, gt=0)
    screener_diversification_bonus: float = Field(default=10.0, ge=0, le=50)  # rank points, max
    screener_vol_lookback_days: int = Field(default=60, ge=10)
    # Annualised volatility ceiling (%) per risk preset; a candidate above it is skipped.
    screener_max_volatility_pct: dict[str, float] = Field(
        default_factory=lambda: {
            "very_conservative": 30.0,
            "conservative": 40.0,
            "balanced": 55.0,
            "balanced_aggressive": 70.0,
            "aggressive": 90.0,
            "very_aggressive": 130.0,
        }
    )
    snapshot_hour: int = 23
    snapshot_minute: int = 59
    snapshot_misfire_grace_seconds: int = 6 * 3600  # run a late snapshot instead of skipping it
    scheduler_misfire_grace_seconds: int = 300
    scheduler_timezone: str = "Asia/Jerusalem"
    week_start_day: Literal[
        "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"
    ] = "sunday"
    # Portfolio post-mortem (`portfolio/postmortem.py`). Proposed defaults, awaiting user approval.
    postmortem_min_days: int = Field(default=30, ge=1)  # less history: `not_enough_history`
    postmortem_timing_window_days: int = Field(default=60, ge=5)  # "local high" look-back
    postmortem_near_high_pct: float = Field(default=95.0, gt=0, le=100)  # price / window max
    postmortem_forward_days: int = Field(default=30, ge=1)  # how far "what happened after" looks
    postmortem_material_move_pct: float = Field(default=3.0, gt=0)  # a move that counts as such
    postmortem_top_n: int = Field(default=3, ge=1, le=10)  # top contributors / detractors shown
    postmortem_max_close_age_days: int = Field(default=7, ge=1)  # older last close = stale
    tase_timezone: str = "Asia/Jerusalem"
    us_timezone: str = "America/New_York"
    tase_hours: dict[str, tuple[str, str]] = Field(default_factory=_default_tase_hours)
    tase_holidays: list[date] = Field(default_factory=list)  # forced closed (library override)
    tase_extra_open_days: list[date] = Field(default_factory=list)  # forced open
    us_open: str = "09:30"
    us_close: str = "16:00"
    us_holidays: list[date] = Field(default_factory=list)  # forced closed (library override)
    us_extra_open_days: list[date] = Field(default_factory=list)  # forced open
    post_close_fetch_delay_minutes: int = 15  # one more quote fetch this long after each close

    # --- scoring ---
    signal_weights: dict[str, float] = Field(default_factory=_default_weights)
    tech_min_rows: int = 50
    tech_category_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "trend": 0.35,
            "momentum": 0.30,
            "volatility": 0.15,
            "volume": 0.20,
        }
    )
    tech_rsi_overbought: float = 70.0
    tech_rsi_oversold: float = 30.0
    tech_stoch_overbought: float = 80.0
    tech_stoch_oversold: float = 20.0
    tech_volume_spike_ratio: float = 1.5
    tech_high_atr_pct: float = 5.0
    patterns_min_rows: int = 60
    patterns_pivot_window: int = 5
    patterns_cluster_tolerance_pct: float = 1.5
    patterns_near_level_pct: float = 2.0
    patterns_breakout_lookback: int = 20
    patterns_cross_lookback: int = 20
    patterns_double_tolerance_pct: float = 2.0
    patterns_double_min_depth_pct: float = 4.0
    max_inline_scorecards: int = 0

    # --- risk / x-ray ---
    default_risk_preset: str = "balanced_aggressive"
    home_country: str = "Israel"
    xray_top_n: int = 10
    diversified_sectors: list[str] = Field(
        default_factory=lambda: ["Diversified", "Broad Market", "Crypto", "Unknown"]
    )
    non_country_labels: list[str] = Field(default_factory=lambda: ["Global", "Unknown"])
    # Toggleable X-ray rules (informational only; nothing is ever blocked). A rule's threshold is
    # taken from the portfolio's RiskFilter unless the user overrides it within these bounds (%).
    # (currency has no default threshold: informational unless the user sets one)

    # --- Israeli funds (GemelNet on data.gov.il, free CKAN datastore API, no key) ---
    # Personal, non-commercial use; data only behind login. The default resource is the dataset
    # "gemelnet" file "2024 to today" (verified live 2026-10-04: refreshed daily, monthly rows from
    # 2024-01). Earlier years live in separate files (1999-2022, 2023), so a series starts in 2024.
    # If data.gov.il replaces the file, find the new id with `package_search?q=gemelnet` and set
    # GEMELNET_RESOURCE_IDS as JSON, for example {"monthly_returns": "<uuid>"}. An empty dict
    # turns the lookup off ("unavailable"); manual entry still works.
    gemelnet_base_url: str = "https://data.gov.il/api/3/action"
    gemelnet_resource_ids: dict[str, str] = Field(
        default_factory=lambda: {"monthly_returns": "a30dcbea-a1d2-482c-ae29-8f781f5025fb"}
    )
    # Our name -> the dataset's column name (all verified against the live dataset on 2026-10-04).
    gemelnet_fields: dict[str, str] = Field(
        default_factory=lambda: {
            "fund_id": "FUND_ID",
            "fund_name": "FUND_NAME",
            "classification": "FUND_CLASSIFICATION",
            "managing_corporation": "MANAGING_CORPORATION",
            "period": "REPORT_PERIOD",
            "monthly_yield": "MONTHLY_YIELD",
            "total_assets": "TOTAL_ASSETS",
            "management_fee": "AVG_ANNUAL_MANAGEMENT_FEE",
        }
    )
    gemelnet_search_max_results: int = Field(default=20, ge=1, le=50)
    gemelnet_search_min_chars: int = Field(default=2, ge=1)
    gemelnet_series_months: int = Field(default=60, ge=12, le=240)  # monthly rows read per fund
    gemelnet_category_max_rows: int = Field(default=500, ge=1, le=5000)  # peers for the average
    gemelnet_category_min_peers: int = Field(default=3, ge=1)  # fewer peers: no average
    gemelnet_stale_after_days: int = Field(default=75, ge=1)  # monthly data older than this: stale
    fund_search_rate_limit_per_hour: int = Field(default=120, ge=1)  # per user
    gemelnet_credit: str = (
        "Fund data: GemelNet, Ministry of Finance (data.gov.il), personal non-commercial use."
    )

    # --- dividend calendar (yfinance corporate actions through the provider interface) ---
    dividend_cache_ttl_seconds: float = Field(default=6 * 3600.0, ge=0)
    dividend_cache_max_entries: int = Field(default=500, ge=1)
    dividend_upcoming_days: int = Field(default=120, ge=1, le=400)  # how far ahead to list
    dividend_history_days: int = Field(default=800, ge=30)  # payments read back for the estimate
    dividend_estimate_months: int = Field(default=12, ge=1, le=24)
    dividend_max_symbols_per_request: int = Field(default=60, ge=1)  # provider calls per page
    # Ex-dates a stock is expected to repeat: a payment more than this many days old is dropped
    # from the estimate (a stopped dividend is not extrapolated).
    dividend_stale_after_days: int = Field(default=400, ge=30)
    dividend_credit: str = "Dividend dates: Yahoo Finance (estimates, not announcements)."
    xray_home_bias_default_max_pct: float = Field(default=50.0, gt=0, le=100)  # no RiskFilter field
    xray_rule_override_bounds: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: {
            "concentration": (1.0, 100.0),
            "currency": (10.0, 100.0),
            "country_home": (5.0, 100.0),
            "sector": (5.0, 100.0),
        }
    )

    # --- importer ---
    max_upload_bytes: int = 8 * 1024 * 1024  # raw image body on the server-OCR import route
    max_body_bytes: int = 1024 * 1024  # every other /api request body
    max_image_pixels: int = 25_000_000  # decompression-bomb guard (width x height)
    # Peak decode memory (bands x pixels, plus an RGB copy for non-RGB modes): 12 MP of RGBA/CMYK,
    # 21 MP of gray/palette, or the full 25 MP as RGB.
    max_image_decode_bytes: int = 84_000_000
    image_decode_wait_seconds: float = 30.0  # waiting for the one decode slot, then 503
    import_max_side_px: int = 5000  # taller/wider images are downscaled (in strips) before OCR
    import_strip_rows: int = 256
    import_draft_ttl_hours: int = 24  # unconfirmed drafts are purged after this long
    import_max_rows: int = 200
    screenshot_update_nudge_days: int = (
        7  # "update from a screenshot" is stale after this many days
    )
    import_name_max_chars: int = 200
    draft_purge_interval_minutes: int = 60
    redact_header_fraction: float = 0.12
    # Meitav Trade: the status bar and the app header span the top ~14 % of the screenshot.
    redact_header_fraction_meitav_trade: float = 0.14
    # Meitav Trade has no quantity or cost column: quantity = value / price, cost from the P&L %.
    import_infer_min_value: float = (
        1.0  # below this (value's own currency) a quantity is not inferred
    )
    import_infer_round_slack: float = 1.5  # slack on the rounding error of value and price
    import_infer_min_pnl_pct: float = -99.9  # lower P&L % gives no usable cost
    redact_min_digit_run: int = 6
    redact_blur_radius: int = 12
    import_value_tolerance: float = 0.02
    match_suggest_threshold: float = 60.0  # minimum name similarity to list a candidate
    match_min_length_ratio: float = 0.5  # shorter/longer name length for similarity candidates
    match_containment_score: float = 80.0  # candidate score when one name contains the other
    match_containment_min_chars: int = 4
    match_max_candidates: int = 5
    tesseract_lang: str = "heb+eng"
    gemini_api_key: str | None = None
    # Model ids live here, never inline. The startup probe (`app.model_probe`) checks that the id
    # exists for the key and otherwise takes the first available fallback. The Gemini 2.5 series
    # shuts down no earlier than 2026-10-16; Groq's Llama models may have left the free tier. The
    # fallback ids are best guesses: an id the provider does not list is simply skipped.
    gemini_model: str = "gemini-2.5-flash"
    gemini_model_fallbacks: list[str] = Field(
        default_factory=lambda: ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]
    )
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"
    groq_model_fallbacks: list[str] = Field(
        default_factory=lambda: ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    )
    # --- LLM foundation (`app/llm/`): the template path is the default; a provider is used only
    # when its key is set, the bucket has a token and the answer validates ---
    llm_enabled: bool = True
    llm_provider_order: list[str] = Field(default_factory=lambda: ["gemini", "groq"])
    llm_requests_per_minute: int = 8  # token bucket per provider, shared by all processes
    llm_bucket_cas_retries: int = 100
    llm_timeout_seconds: float = 30.0
    llm_max_output_tokens: int = 1024

    # --- RAG (docs/rag-spec.md): chunking runs in the index job, never in the API process ---
    rag_chunk_target_tokens: int = Field(default=300, ge=50, le=2000)
    rag_chunk_overlap_tokens: int = Field(default=40, ge=0, le=500)
    # Cheap token estimator (no tokenizer dependency): characters per token by script.
    rag_chars_per_token_latin: float = Field(default=4.0, gt=0)
    rag_chars_per_token_hebrew: float = Field(default=2.5, gt=0)
    rag_max_k: int = Field(default=12, ge=1, le=50)  # hard cap on chunks per search
    # A chunk older than its doc type's TTL (days) is never returned.
    rag_doc_ttl_days: dict[str, int] = Field(
        default_factory=lambda: {"filing": 400, "news": 14, "transcript": 120, "profile": 365}
    )
    # Hard cap on the whole prompt (instructions + PublicFacts + chunks) per LLM role, in estimated
    # tokens. Retrieval for a role never returns more than the room left in this budget.
    rag_role_budgets: dict[str, int] = Field(
        default_factory=lambda: {
            "company_profile": 2500,
            "news": 2000,
            "bear": 1500,
            "cio": 2000,
            "ask_portfolio": 2000,
        }
    )
    # Hook for optional local embeddings (e.g. multilingual-e5-small), off until keyword recall is
    # measured too low. Nothing reads it yet; see docs/rag-spec.md "Embeddings hook".
    rag_embeddings_enabled: bool = False
    llm_cache_ttl_hours: float = 24.0 * 7
    llm_cache_ttl_news_hours: float = 6.0  # answers whose prompt depends on news go stale fast
    # Free-tier quotas are per day and shared by every user: reserve them. Per-minute sub-buckets
    # (on top of `llm_requests_per_minute` per provider) stop one user or one role from emptying the
    # provider bucket; the daily budget is per provider (requests per UTC day), per-user daily caps
    # apply to on-demand calls, and batch work may only use `llm_batch_daily_fraction` of the
    # provider's day so a user's on-demand question is never starved by background jobs.
    llm_user_rpm: int = 3
    llm_role_rpm: int = 5
    llm_daily_budget: int = 900
    llm_batch_daily_fraction: float = 0.6
    llm_user_daily_budget: int = 60
    # Gemini 2.5/3 thinking tokens count against `maxOutputTokens` and can truncate the JSON: set the
    # budget explicitly (0 = off; None = leave the model's default). Pro models cannot be 0.
    gemini_thinking_budget: int | None = 0
    # Fenced free text (news, user notes): caps applied before it is sent.
    llm_untrusted_max_chars: int = 4000
    llm_untrusted_max_urls: int = 3
    llm_untrusted_url_chars: int = 100
    llm_scrub_min_digit_run: int = 6
    llm_scrub_min_name_chars: int = 4  # parts of a user's e-mail name shorter than this are kept
    model_probe_enabled: bool = True
    model_probe_timeout_seconds: float = 8.0

    # --- walk-forward backtest (app/backtest; deterministic technical + patterns score only) ---
    # Where `fetch-history` writes and the simulator reads daily bars. None: backend/data/history.
    backtest_history_dir: str | None = None
    backtest_capital_ils: float = Field(default=100_000.0, gt=0)  # every run starts from cash only
    backtest_horizon: str = (
        "3m"  # the horizon the exit levels are computed for (a key of horizon_table)
    )
    backtest_rebalance_every_days: int = Field(default=5, gt=0)  # trading days between screenings
    backtest_trailing_update_every_days: int = Field(default=5, gt=0)  # re-ratchet trailing stops
    backtest_max_new_per_rebalance: int = Field(default=3, gt=0)
    backtest_max_open_positions: int = Field(default=15, gt=0)
    backtest_commission_bps: float = Field(default=10.0, ge=0)  # per side, on the traded value
    backtest_slippage_bps: float = Field(default=5.0, ge=0)  # per side, against us
    backtest_fx_series_symbol: str = "ILS=X"  # USD/ILS from the store; else fx_fallback_usd_ils
    backtest_benchmark: str = "^GSPC"  # the excess return is measured against this symbol
    backtest_window_months: int = Field(default=6, gt=0)
    backtest_step_months: int = Field(default=1, gt=0)
    backtest_success_threshold: float = Field(default=0.8, ge=0, le=1)  # held-out success rate
    backtest_default_train_fraction: float = Field(default=0.7, gt=0, lt=1)  # when no --train-until
    # The targets below are proposals. `--record` refuses to write a passing backtest to the launch
    # gate until the user has approved them and set this to true.
    backtest_targets_approved: bool = False
    # Targets and drawdown caps per preset and window (approved by the user 2026-10-04).
    backtest_targets: dict[str, BacktestTarget] = Field(default_factory=default_backtest_targets)

    # --- launch gate (no live buy/sell verdicts until both gates pass) ---
    launch_require_backtest: bool = True
    launch_paper_min_weeks: int = 4
    launch_paper_max_critical_errors: int = 0
    launch_paper_min_resolved_calls: int = 50  # calls resolved at the 1-month horizon
    # A call counts as "resolved at 1 month" once it is resolved (not an error) and this many days
    # have passed since it was made, whether the stop, the target or the horizon ended it.
    launch_paper_window_days: int = 30
    # Only calls made with these hashes count. The weights hash is always the active weights
    # config; the model hash is optional (None: any model) because it is set by the Phase 2
    # committee run, not by the app config.
    launch_paper_model_hash: str | None = None
    launch_paper_must_beat: list[str] = Field(default_factory=lambda: ["^GSPC", "^TA125.TA"])
    # Track-record page (members only): how long each horizon lasts, in days, and the row cap.
    track_record_horizon_days: dict[str, int] = Field(
        default_factory=lambda: {"1w": 7, "1m": 30, "3m": 91, "6m": 182, "1y": 365}
    )
    track_record_max_rows: int = Field(default=500, ge=1, le=5000)

    # --- alerts ---
    # Words that make outgoing text (Telegram, notifications, weekly review) read as a buy/sell
    # verdict. The list lives in `app.verdict_words` (one list, shared with the verdict contract
    # test); this setting can replace it. `app.outbound` refuses text containing one while the
    # launch gate is closed (and refuses any free text: only `TemplateText` may leave then). The
    # disclaimer is ignored. Price-rule words (stop, target) are deliberately absent.
    outbound_verdict_words: list[str] = Field(default_factory=lambda: list(VERDICT_WORDS))
    telegram_bot_token: str | None = None  # env only; never logged or returned
    telegram_timeout_seconds: float = 10.0
    # The secret Telegram echoes in `X-Telegram-Bot-Api-Secret-Token` on every webhook call (set it
    # with `setWebhook secret_token=`). Without it the webhook route refuses everything.
    telegram_webhook_secret: str | None = None
    telegram_bot_username: str | None = None  # for the t.me deep link only
    telegram_link_code_ttl_minutes: int = Field(default=15, ge=1, le=60)
    telegram_link_code_length: int = Field(default=8, ge=6, le=16)
    telegram_link_codes_per_hour: int = Field(default=5, ge=1)  # per user
    telegram_link_attempts_per_hour: int = Field(default=10, ge=1)  # per Telegram chat
    # Weekly review (settings-spec section 3). Per-user values override these; the schedule is
    # evaluated in `scheduler_timezone` (Asia/Jerusalem) and checked every few minutes.
    weekly_review_default_enabled: bool = True
    weekly_review_default_day: Literal[
        "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"
    ] = "sunday"
    weekly_review_default_time: str = Field(default="20:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    weekly_review_check_interval_minutes: int = Field(default=15, ge=1, le=60)
    seed_csv_path: str | None = None

    @field_validator("horizon_table")
    @classmethod
    def _horizon_table_is_complete(cls, table: dict[str, HorizonSpec]) -> dict[str, HorizonSpec]:
        if set(table) != set(HORIZONS):
            raise ValueError(
                f"horizon_table needs exactly the keys {list(HORIZONS)}, got {sorted(table)}"
            )
        return table

    @field_validator("price_fresh_window_minutes")
    @classmethod
    def _fresh_windows(cls, windows: dict[str, int]) -> dict[str, int]:
        if not windows or any(m <= 0 for m in windows.values()):
            raise ValueError("price_fresh_window_minutes needs a positive window per market")
        return windows

    @model_validator(mode="after")
    def _resolve_auto_migrate(self) -> Settings:
        if self.auto_migrate is None:
            self.auto_migrate = self.env == "dev"
        if self.turnstile_enabled is None:
            self.turnstile_enabled = bool(
                self.env == "production" and self.turnstile_site_key and self.turnstile_secret_key
            )
        return self


MIN_PROXY_SECRET_CHARS = 16


def validate_proxy(settings: Settings) -> None:
    """The client-IP header may only be trusted together with a shared secret. Raises RuntimeError."""
    if not settings.trusted_proxy_header:
        return
    secret = settings.proxy_shared_secret
    if not secret:
        raise RuntimeError(
            "TRUSTED_PROXY_HEADER is set but PROXY_SHARED_SECRET is not: refusing to trust a "
            "client-IP header that anyone could forge"
        )
    if len(secret) < MIN_PROXY_SECRET_CHARS:
        raise RuntimeError(
            f"PROXY_SHARED_SECRET must be at least {MIN_PROXY_SECRET_CHARS} characters"
        )


TurnstileState = Literal["off", "on", "misconfigured"]


def turnstile_state(settings: Settings) -> TurnstileState:
    """`on` needs the flag and both keys; the flag without keys is `misconfigured` (the login
    then does not ask for a challenge, which would lock everyone out, and the check says so)."""
    if not settings.turnstile_enabled:
        return "off"
    if settings.turnstile_site_key and settings.turnstile_secret_key:
        return "on"
    return "misconfigured"


def validate_production(settings: Settings) -> None:
    """Refuse to start with unsafe settings in production. Raises RuntimeError."""
    if settings.env != "production":
        return
    problems: list[str] = []
    if not settings.cookie_secure:
        problems.append("COOKIE_SECURE must be true (session cookies need the Secure flag)")
    if settings.secret_key == DEFAULT_SECRET_KEY or len(settings.secret_key) < 32:
        problems.append("SECRET_KEY must be set to a random string of at least 32 characters")
    if any(o.strip() == "*" for o in settings.cors_origins):
        problems.append(
            'CORS_ORIGINS must not contain "*" (credentialed requests need explicit origins)'
        )
    if problems:
        raise RuntimeError("Refusing to start with ENV=production: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()

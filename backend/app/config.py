"""All thresholds, weights, intervals and switches live here (never inline)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
IMPLEMENTED_SIGNALS: frozenset[str] = frozenset({"technical", "patterns"})


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
    turnstile_after_failures: int = 3
    turnstile_verify_url: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    turnstile_timeout_seconds: float = 5.0
    max_alerts_per_user: int = 50
    invite_ttl_days: int = 14

    # --- market data ---
    benchmark_sp500: str = "^GSPC"
    benchmark_ta125: str = "^TA125.TA"
    fx_symbol: str = "ILS=X"
    fx_fallback_usd_ils: float = 3.6  # last resort only; always reported as stale
    fx_stale_after_hours: float = 72.0
    quote_stale_after_minutes: int = 30
    history_cache_ttl_seconds: int = 6 * 3600
    history_failure_ttl_seconds: int = 300
    history_cache_max_entries: int = 200  # one entry per symbol; the oldest is evicted
    provider_small_cache_max_entries: int = 5000  # currencies and failure markers
    history_days: int = 420
    provider_max_retries: int = 3
    provider_backoff_base_seconds: float = 2.0
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
    snapshot_hour: int = 23
    snapshot_minute: int = 59
    snapshot_misfire_grace_seconds: int = 6 * 3600  # run a late snapshot instead of skipping it
    scheduler_misfire_grace_seconds: int = 300
    scheduler_timezone: str = "Asia/Jerusalem"
    week_start_day: Literal[
        "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"
    ] = "sunday"
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
    import_name_max_chars: int = 200
    draft_purge_interval_minutes: int = 60
    redact_header_fraction: float = 0.12
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
    model_probe_enabled: bool = True
    model_probe_timeout_seconds: float = 8.0

    # --- launch gate (no live buy/sell verdicts until both gates pass) ---
    launch_require_backtest: bool = True
    launch_paper_min_weeks: int = 4
    launch_paper_max_critical_errors: int = 0
    launch_paper_min_resolved_calls: int = 50  # calls resolved at the 1-month horizon
    launch_paper_must_beat: list[str] = Field(default_factory=lambda: ["^GSPC", "^TA125.TA"])

    # --- alerts ---
    # Whole words that make outgoing text (Telegram, notifications, weekly review) read as a
    # buy/sell verdict. `app.outbound.release_text` refuses text containing one while the launch gate
    # is closed. The disclaimer is ignored. Price-rule words (stop, target) are deliberately absent.
    outbound_verdict_words: list[str] = Field(
        default_factory=lambda: [
            "buy", "sell", "hold", "accumulate", "recommend", "recommendation", "verdict",
            "upgrade", "downgrade", "bullish", "bearish", "rating", "outlook", "trim",
            "opinion", "stance", "conviction", "outperform", "underperform", "overweight",
            "underweight",
        ]
    )  # fmt: skip
    telegram_bot_token: str | None = None
    telegram_timeout_seconds: float = 10.0
    seed_csv_path: str | None = None

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

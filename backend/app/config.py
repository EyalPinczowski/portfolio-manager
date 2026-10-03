"""All thresholds, weights, intervals and switches live here (never inline)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DISCLAIMER = "Not financial advice."

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


def _default_us_holidays() -> list[date]:
    return [
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core ---
    database_url: str = "sqlite:///./portfolio.db"
    secret_key: str = "change-me-in-production"
    cors_origins: list[str] = Field(default_factory=list)

    # --- auth ---
    cookie_name: str = "pm_session"
    cookie_secure: bool = False
    session_ttl_hours: int = 24 * 14
    password_min_length: int = 10
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300
    invite_ttl_days: int = 14

    # --- market data ---
    benchmark_sp500: str = "^GSPC"
    benchmark_ta125: str = "^TA125.TA"
    fx_symbol: str = "ILS=X"
    fx_fallback_usd_ils: float = 3.6
    quote_stale_after_minutes: int = 30
    history_cache_ttl_seconds: int = 6 * 3600
    history_failure_ttl_seconds: int = 300
    history_days: int = 420
    provider_max_retries: int = 3
    provider_backoff_base_seconds: float = 2.0
    currency_cache_ttl_seconds: int = 7 * 24 * 3600

    # --- scheduler ---
    quotes_interval_minutes: int = 5
    scores_interval_minutes: int = 30
    score_cache_ttl_minutes: int = 360
    snapshot_hour: int = 23
    snapshot_minute: int = 59
    scheduler_timezone: str = "Asia/Jerusalem"
    tase_timezone: str = "Asia/Jerusalem"
    us_timezone: str = "America/New_York"
    tase_hours: dict[str, tuple[str, str]] = Field(default_factory=_default_tase_hours)
    tase_holidays: list[date] = Field(default_factory=list)
    us_open: str = "09:30"
    us_close: str = "16:00"
    us_holidays: list[date] = Field(default_factory=_default_us_holidays)

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
    max_upload_bytes: int = 8 * 1024 * 1024
    redact_header_fraction: float = 0.12
    redact_min_digit_run: int = 6
    redact_blur_radius: int = 12
    import_value_tolerance: float = 0.02
    match_auto_threshold: float = 88.0
    match_suggest_threshold: float = 60.0
    tesseract_lang: str = "heb+eng"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"

    # --- alerts ---
    telegram_bot_token: str | None = None
    telegram_timeout_seconds: float = 10.0
    seed_csv_path: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

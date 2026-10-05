"""Research group "faster & lighter": stored daily bars, boot command, scheduler spacing, LLM reuse."""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from typing import Any, ClassVar

import pandas as pd
import pytest
from sqlmodel import func, select

from app.cli import boot
from app.config import Settings
from app.db import new_session
from app.llm import structured_call
from app.llm.fakes import FakeLLMProvider
from app.models import DailyBar
from app.providers.yfinance_provider import YFinanceProvider
from app.timeutil import utcnow
from tests.test_llm_structured import GOOD, Bear, settings, template


def _frame(days: list[Any], base: float = 100.0, tz: str | None = None) -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(days))
    if tz:
        idx = idx.tz_localize(tz)
    n = len(idx)
    close = [base + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close, "High": [c + 1 for c in close], "Low": [c - 1 for c in close],
            "Close": close, "Volume": [1000.0] * n,
            "Dividends": [0.0] * n, "Stock Splits": [0.0] * n,
        },
        index=idx,
    )  # fmt: skip


class FakeTicker:
    calls: ClassVar[list[dict[str, Any]]] = []
    frame: ClassVar[pd.DataFrame] = pd.DataFrame()

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def history(self, **kw: Any) -> pd.DataFrame:
        FakeTicker.calls.append(kw)
        if "start" in kw:
            return FakeTicker.frame[FakeTicker.frame.index >= pd.Timestamp(kw["start"])]
        return FakeTicker.frame


def _provider(monkeypatch: pytest.MonkeyPatch, currency: str = "USD") -> YFinanceProvider:
    import yfinance as yf

    FakeTicker.calls = []
    monkeypatch.setattr(yf, "Ticker", FakeTicker)
    p = YFinanceProvider(Settings(_env_file=None), sleep=lambda _s: None)  # type: ignore[call-arg]
    p.raw_currency = lambda sym: currency  # type: ignore[method-assign]
    return p


def _recent(n: int, end_offset: int = 0) -> list[Any]:
    end = pd.Timestamp(utcnow().date()) - pd.Timedelta(days=end_offset)
    return list(pd.bdate_range(end=end, periods=n))


def _count(symbol: str) -> int:
    with new_session() as db:
        return int(
            db.exec(
                select(func.count()).select_from(DailyBar).where(DailyBar.symbol == symbol)
            ).one()
        )


def test_full_fetch_then_incremental_tail_without_duplicates(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    days = _recent(300)
    FakeTicker.frame = _frame(days)
    p = _provider(monkeypatch)
    df = p.get_history("AAPL", 400)
    assert df is not None and len(df) == 300
    assert "period" in FakeTicker.calls[0] and FakeTicker.calls[0]["period"] == "400d"
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]  # extras dropped
    assert _count("AAPL") == 300

    # a new provider (cold start, empty memory cache) reads the store and fetches only the tail
    FakeTicker.frame = _frame([*days, days[-1] + pd.Timedelta(days=1)], base=100.0)
    p2 = _provider(monkeypatch)
    df2 = p2.get_history("AAPL", 400)
    assert df2 is not None and len(df2) == 301
    assert FakeTicker.calls[0]["period"] == "5d"
    assert _count("AAPL") == 301  # re-read last bar replaced, not duplicated
    assert not df2.index.duplicated().any()


def test_old_gap_uses_start_of_last_stored_day(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _recent(250, end_offset=20)
    FakeTicker.frame = _frame(old)
    _provider(monkeypatch).get_history("MSFT", 330)
    FakeTicker.frame = _frame(_recent(270))
    p = _provider(monkeypatch)
    df = p.get_history("MSFT", 330)
    assert "start" in FakeTicker.calls[0] and "period" not in FakeTicker.calls[0]
    assert df is not None and not df.index.duplicated().any()
    assert _count("MSFT") >= len(df)


def test_agorot_stay_normalised_in_the_store(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTicker.frame = _frame(_recent(40), base=5000.0)  # agorot
    p = _provider(monkeypatch, currency="ILA")
    df = p.get_history("TEVA.TA", 60)
    assert df is not None and df["Close"].iloc[0] == pytest.approx(50.0)
    with new_session() as db:
        bar = db.exec(select(DailyBar).where(DailyBar.symbol == "TEVA.TA")).first()
    assert bar is not None and bar.close == pytest.approx(50.0)
    # the tail appended later is normalised too, and the stored part is not divided twice
    p2 = _provider(monkeypatch, currency="ILA")
    df2 = p2.get_history("TEVA.TA", 60)
    assert df2 is not None and df2["Close"].iloc[0] == pytest.approx(50.0)
    assert df2["Close"].max() < 100


def test_failed_tail_fetch_serves_stored_bars(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTicker.frame = _frame(_recent(60))
    _provider(monkeypatch).get_history("KO", 60)
    p = _provider(monkeypatch)
    FakeTicker.frame = pd.DataFrame()  # Yahoo answers nothing
    df = p.get_history("KO", 60)
    assert df is not None and len(df) >= 40


def test_prefetch_uses_one_batched_download(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import yfinance as yf

    p = _provider(monkeypatch)
    seen: list[dict[str, Any]] = []
    days = _recent(60)
    both = pd.concat({"AAA": _frame(days), "BBB": _frame(days, base=50.0)}, axis=1)

    def fake_download(**kw: Any) -> pd.DataFrame:
        seen.append(kw)
        return both

    monkeypatch.setattr(yf, "download", fake_download)
    assert p.prefetch_history(["AAA", "BBB"], 90) == 2
    assert len(seen) == 1 and seen[0]["threads"] is False and seen[0]["group_by"] == "ticker"
    assert _count("AAA") == 60 and _count("BBB") == 60
    FakeTicker.calls = []
    assert p.get_history("AAA", 90) is not None  # served from memory: no per-symbol call
    assert FakeTicker.calls == []


def test_boot_migrate_failure_blocks_but_bootstrap_failure_does_not(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import app.cli as cli

    monkeypatch.setattr(cli, "run_migrations", lambda e: None)
    monkeypatch.setattr(cli, "get_engine", lambda: None)
    monkeypatch.setattr(cli, "bootstrap_admin", lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    assert boot() == 0
    assert "bootstrap-admin: error: RuntimeError" in capsys.readouterr().out

    def boom(e: object) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(cli, "run_migrations", boom)
    with pytest.raises(RuntimeError):
        boot()


def test_provider_base_does_not_import_pandas() -> None:
    code = "import sys, app.providers.base, app.api.exit_levels; print('pandas' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    # exit_levels pulls pandas through scoring modules today; base alone must stay light
    out2 = subprocess.run(
        [sys.executable, "-c", "import sys, app.providers.base; print('pandas' in sys.modules)"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert out2.stdout.strip() == "False", out2.stderr
    assert out.returncode == 0, out.stderr


def test_scheduler_jobs_are_offset_and_jittered() -> None:
    from app.scheduler.setup import interval_trigger

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    a, b = interval_trigger(5, 0, s), interval_trigger(30, 1, s)
    assert a.jitter == s.scheduler_job_jitter_seconds
    assert b.start_date - a.start_date > timedelta(minutes=20)  # distinct first starts
    assert s.universe_refresh_interval_minutes == 60


def test_weekend_has_no_equity_session() -> None:
    from datetime import UTC, datetime

    from app.scheduler.calendars import any_equity_session_today

    assert not any_equity_session_today(datetime(2026, 10, 10, 12, tzinfo=UTC))  # Saturday
    assert any_equity_session_today(datetime(2026, 10, 7, 12, tzinfo=UTC))  # Wednesday


def _call(prov: list[FakeLLMProvider], prompt: str, now: Any = None, reuse: bool = True) -> Any:
    return structured_call(
        cache_scope="global", role="bear", model_cls=Bear, system="s", prompt=prompt,
        template=template, providers=prov, settings=settings(llm_cache_ttl_hours=1.0),
        now=now, reuse_unchanged=reuse,
    )  # fmt: skip


def test_unchanged_prompt_reuses_last_answer_after_ttl(env: None) -> None:
    _call([FakeLLMProvider([GOOD])], "same")
    later = utcnow() + timedelta(days=30)
    nothing = FakeLLMProvider([])
    res = _call([nothing], "same", now=later)
    assert res.source == "cache" and res.value.risk == "valuation"
    # without the flag, or with a changed prompt, the call is made again
    assert _call([FakeLLMProvider([GOOD])], "same", now=later, reuse=False).source == "llm"
    assert _call([FakeLLMProvider([GOOD])], "changed", now=later).source == "llm"


def test_prompt_orders_chunks_by_id() -> None:
    from datetime import datetime

    from app.rag.prompt import build_prompt
    from app.rag.retriever import Hit
    from tests.test_committee_cache_stable import _facts

    def hit(i: int, text: str) -> Hit:
        return Hit(
            chunk_id=i, symbol="AAPL", market="US", doc_type="news", source_url="u",
            as_of=datetime(2026, 10, 4), text=text, token_count=10, score=1.0,
        )  # fmt: skip

    a, b = hit(3, "alpha results beat"), hit(1, "completely different supervision text")
    assert (
        build_prompt("news", _facts(), [a, b]).text == build_prompt("news", _facts(), [b, a]).text
    )

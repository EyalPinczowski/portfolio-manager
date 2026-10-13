"""`universe-refresh` CLI, the newest-score-time / unscored fields, and the Actions workflow file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app import cli
from app.config import get_settings
from app.db import new_session
from app.models import SignalCache
from app.providers.registry import Providers, set_providers
from app.scoring.universe import count_due, refresh_universe_until_fresh, universe_securities
from tests.conftest import FakeHistory, FakeQuotes

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "universe-scores.yml"


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNIVERSE_REFRESH_BATCH_SIZE", "2")
    monkeypatch.setenv("UNIVERSE_PAUSE_SECONDS", "0")
    get_settings.cache_clear()


def _seed_universe() -> int:
    from app.securities import seed_securities

    with new_session() as db:
        seed_securities(db)
        return len(universe_securities(db))


def test_loops_batches_until_fresh(
    env: None, small: None, history: FakeHistory, quotes: FakeQuotes
) -> None:
    total = _seed_universe()
    assert total > 4
    with new_session() as db:
        attempted, left = refresh_universe_until_fresh(db, history, quotes, max_symbols=1000)  # type: ignore[arg-type]
        assert left == 0 and attempted >= total
        assert count_due(db) == 0
        assert db.get(SignalCache, universe_securities(db)[0].symbol) is not None


def test_max_limits_the_run(
    env: None, small: None, history: FakeHistory, quotes: FakeQuotes
) -> None:
    total = _seed_universe()
    with new_session() as db:
        attempted, left = refresh_universe_until_fresh(db, history, None, max_symbols=4)  # type: ignore[arg-type]
        assert attempted == 4
        assert left == total - 4


def test_cli_prints_counts_only(
    env: None,
    small: None,
    history: FakeHistory,
    quotes: FakeQuotes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_universe()
    set_providers(Providers(quotes=quotes, history=history))  # type: ignore[arg-type]
    try:
        assert cli.main(["universe-refresh", "--max", "4"]) == 0
        out = capsys.readouterr().out.strip()
        assert out.startswith("universe-refresh: 4 scored, ")
        assert cli.main(["universe-refresh", "--max", "0"]) == 1
    finally:
        set_providers(None)


def test_workflow_file() -> None:
    wf: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    on = wf.get("on", wf.get(True))
    cron = on["schedule"][0]["cron"]
    minute, hours, dom, mon, dow = cron.split()
    assert dow == "1-5" and (dom, mon) == ("*", "*")
    assert minute not in ("0", "30") and hours.startswith("13-21/3")
    assert "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["group"] and wf["concurrency"]["cancel-in-progress"] is False
    job = wf["jobs"]["score"]
    assert job["timeout-minutes"] == 20
    text = WORKFLOW.read_text()
    assert "secrets.DATABASE_URL" in text and "universe-refresh --max 80" in text

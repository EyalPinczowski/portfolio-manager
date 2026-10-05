"""CLI: `python -m app.cli migrate` / `create-invite` / `create-admin` / `fetch-history` / `backtest`."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.auth.passwords import hash_password
from app.config import Settings, get_settings, turnstile_state, validate_production, validate_proxy
from app.db import get_engine, new_session, prepare_database, run_migrations
from app.models import Invite, User
from app.timeutil import utcnow


def create_invite(days: int | None = None, created_by: int | None = None) -> str:
    s = get_settings()
    prepare_database(get_engine())
    code = secrets.token_urlsafe(12)
    with new_session() as db:
        db.add(
            Invite(
                code=code,
                created_by=created_by,
                expires_at=utcnow() + timedelta(days=days or s.invite_ttl_days),
            )
        )
        db.commit()
    return code


def create_admin(email: str, password: str) -> int:
    s = get_settings()
    if len(password) < s.password_min_length:
        raise ValueError(f"Password must be at least {s.password_min_length} characters")
    prepare_database(get_engine())
    with new_session() as db:
        email = email.strip().lower()
        if db.exec(select(User).where(User.email == email)).first() is not None:
            raise ValueError("A user with this email already exists")
        user = User(
            email=email,
            password_hash=hash_password(password),
            disclaimer_accepted_at=utcnow(),
            is_admin=True,
        )
        db.add(user)
        db.commit()
        assert user.id is not None
        return user.id


def bootstrap_admin(settings: Settings) -> str:
    """Create the admin from BOOTSTRAP_ADMIN_* once. Returns a status word; never the values."""
    email, password = settings.bootstrap_admin_email, settings.bootstrap_admin_password
    if not email or not password:
        return "not configured"
    try:
        create_admin(email, password)
    except IntegrityError:  # a second instance booting at the same moment created it first
        return "already exists"
    except ValueError as exc:
        if "already exists" in str(exc):
            return "already exists"
        if "at least" in str(exc):
            return "error: password too short"
        return "error: invalid settings"
    except Exception as exc:  # never include the message: it could carry values
        return f"error: {type(exc).__name__}"
    return "created"


def check_config(settings: Settings, probe: bool = False) -> dict[str, str]:
    """Human-readable state of the settings that matter for safety (no secret values)."""
    out: dict[str, str] = {"env": settings.env}
    for name, fn in (("production_settings", validate_production), ("proxy", validate_proxy)):
        try:
            fn(settings)
            out[name] = "ok"
        except RuntimeError as exc:
            out[name] = f"PROBLEM: {exc}"
    out["turnstile"] = turnstile_state(settings)
    if out["turnstile"] == "misconfigured":
        out["turnstile"] += (
            " (TURNSTILE_ENABLED is set but a key is missing: no challenge is shown)"
        )
    elif settings.env == "production" and out["turnstile"] == "off":
        out["turnstile"] += (
            " (no TURNSTILE_SITE_KEY / TURNSTILE_SECRET_KEY: login has no challenge)"
        )
    out["client_ip_header"] = (
        f"{settings.trusted_proxy_header} (needs {settings.proxy_auth_header})"
        if settings.trusted_proxy_header
        else "peer address"
    )
    out["scheduler"] = "in-process" if settings.scheduler_in_process else "separate process"
    if probe:
        from app.model_probe import probe_models

        for provider, choice in probe_models(settings).items():
            out[f"model.{provider}"] = f"{choice.model} ({choice.status})"
    return out


def boot() -> int:
    """One process for the container start: migrate, then bootstrap the admin.

    A migration failure propagates (non-zero exit: the API must not serve a stale schema). Admin
    bootstrap problems are reported by status word and never block the boot.
    """
    run_migrations(get_engine())
    print("database schema is up to date")
    try:
        print(f"bootstrap-admin: {bootstrap_admin(get_settings())}")
    except Exception as exc:  # never include the message: it could carry values
        print(f"bootstrap-admin: error: {type(exc).__name__}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Upgrade the database schema to the latest revision")
    inv = sub.add_parser("create-invite", help="Create a single-use signup invite code")
    inv.add_argument(
        "--days", type=int, default=None, help="Validity in days (default from config)"
    )
    sub.add_parser("bootstrap-admin", help="Create the first admin from BOOTSTRAP_ADMIN_* env vars")
    sub.add_parser(
        "boot", help="Container start: migrate (failure aborts), then bootstrap-admin (never fatal)"
    )
    chk = sub.add_parser("check-config", help="Show the state of the safety-relevant settings")
    chk.add_argument("--probe-models", action="store_true", help="Also ask the LLM providers")
    adm = sub.add_parser("create-admin", help="Create an admin user")
    adm.add_argument("--email", required=True)
    adm.add_argument("--password", help="Prompted if omitted")
    fh = sub.add_parser(
        "fetch-history",
        help="Download daily history once into the backtest store (needs network; never run in tests)",
    )
    fh.add_argument("--symbols-from", choices=["universe"], default=None)
    fh.add_argument("--symbols", help="Comma separated symbols instead of --symbols-from")
    fh.add_argument("--years", type=int, default=8)
    fh.add_argument("--history-dir", default=None)
    ri = sub.add_parser(
        "rag-index", help="Ingest local text/JSON files into the RAG index (no network)"
    )
    ri.add_argument("--from-dir", required=True)
    ri.add_argument("--symbols-from", choices=["universe"], default=None)
    ri.add_argument("--symbols", help="Comma separated symbols instead of --symbols-from")
    bt = sub.add_parser("backtest", help="Walk-forward backtest on the stored history")
    bt.add_argument("--profiles", default="conservative,balanced,balanced_aggressive,aggressive")
    bt.add_argument("--window-months", type=int, default=None)
    bt.add_argument("--step-months", type=int, default=None)
    bt.add_argument("--mode", choices=["random", "top"], default="random")
    bt.add_argument("--runs", type=int, default=5, help="Seeded runs per window (random mode)")
    bt.add_argument("--seed", type=int, default=1)
    bt.add_argument("--train-until", default=None, help="YYYY-MM-DD; later windows are held out")
    bt.add_argument("--benchmark", default=None)
    bt.add_argument("--history-dir", default=None)
    bt.add_argument(
        "--out", default=None, help="Report path (default docs/reviews/backtest-<date>.md)"
    )
    bt.add_argument("--workers", type=int, default=1)
    bt.add_argument(
        "--record",
        action="store_true",
        help="Record a PASS to the launch gate, only if every guard holds (see the report)",
    )
    args = parser.parse_args(argv)
    if args.cmd == "migrate":
        run_migrations(get_engine())
        print("database schema is up to date")
        return 0
    if args.cmd == "boot":
        return boot()
    if args.cmd == "bootstrap-admin":
        status = bootstrap_admin(get_settings())
        print(f"bootstrap-admin: {status}")
        return 1 if status.startswith("error") else 0
    if args.cmd == "check-config":
        report = check_config(get_settings(), probe=args.probe_models)
        for key, value in report.items():
            print(f"{key}: {value}")
        return 1 if any(v.startswith("PROBLEM") for v in report.values()) else 0
    if args.cmd == "fetch-history":
        from app.backtest.commands import run_fetch_history

        return run_fetch_history(
            get_settings(), args.symbols_from, args.symbols, args.years, args.history_dir
        )
    if args.cmd == "rag-index":
        from pathlib import Path

        from app.rag.indexjob import run_index
        from app.scoring.universe import load_universe

        wanted: set[str] | None = None
        if args.symbols:
            wanted = {x.strip().upper() for x in args.symbols.split(",") if x.strip()}
        elif args.symbols_from == "universe":
            wanted = set(load_universe())
        prepare_database(get_engine())
        with new_session() as db:
            stats, purged = run_index(db, Path(args.from_dir), wanted)
        print(
            f"rag-index: {stats.docs} documents, {stats.chunks_added} chunks added, "
            f"{stats.chunks_skipped} already stored, {purged} expired chunks removed"
        )
        return 0
    if args.cmd == "backtest":
        from app.backtest.commands import run_backtest

        s = get_settings()
        common: dict[str, Any] = {
            "profiles": args.profiles,
            "window_months": args.window_months or s.backtest_window_months,
            "step_months": args.step_months or s.backtest_step_months,
            "mode": args.mode,
            "runs": args.runs,
            "seed": args.seed,
            "train_until": args.train_until,
            "history_dir": args.history_dir,
            "out": args.out,
            "workers": args.workers,
            "record": args.record,
            "benchmark": args.benchmark,
        }
        if not args.record:
            return run_backtest(s, **common)
        prepare_database(get_engine())
        with new_session() as db:
            return run_backtest(s, db=db, **common)
    if args.cmd == "create-invite":
        print(create_invite(args.days))
        return 0
    password = args.password or getpass.getpass("Password: ")
    try:
        uid = create_admin(args.email, password)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"created admin user id={uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

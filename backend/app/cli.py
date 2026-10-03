"""CLI: `python -m app.cli migrate` / `create-invite` / `create-admin`."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import timedelta

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Upgrade the database schema to the latest revision")
    inv = sub.add_parser("create-invite", help="Create a single-use signup invite code")
    inv.add_argument(
        "--days", type=int, default=None, help="Validity in days (default from config)"
    )
    chk = sub.add_parser("check-config", help="Show the state of the safety-relevant settings")
    chk.add_argument("--probe-models", action="store_true", help="Also ask the LLM providers")
    adm = sub.add_parser("create-admin", help="Create an admin user")
    adm.add_argument("--email", required=True)
    adm.add_argument("--password", help="Prompted if omitted")
    args = parser.parse_args(argv)
    if args.cmd == "migrate":
        run_migrations(get_engine())
        print("database schema is up to date")
        return 0
    if args.cmd == "check-config":
        report = check_config(get_settings(), probe=args.probe_models)
        for key, value in report.items():
            print(f"{key}: {value}")
        return 1 if any(v.startswith("PROBLEM") for v in report.values()) else 0
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

"""CLI: `python -m app.cli migrate` / `create-invite` / `create-admin`."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from datetime import timedelta

from sqlmodel import select

from app.auth.passwords import hash_password
from app.config import get_settings
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Upgrade the database schema to the latest revision")
    inv = sub.add_parser("create-invite", help="Create a single-use signup invite code")
    inv.add_argument(
        "--days", type=int, default=None, help="Validity in days (default from config)"
    )
    adm = sub.add_parser("create-admin", help="Create an admin user")
    adm.add_argument("--email", required=True)
    adm.add_argument("--password", help="Prompted if omitted")
    args = parser.parse_args(argv)
    if args.cmd == "migrate":
        run_migrations(get_engine())
        print("database schema is up to date")
        return 0
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

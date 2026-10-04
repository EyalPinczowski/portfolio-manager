"""Shared dependency aliases (re-exported for convenience)."""

from app.auth.deps import AuthDep, DbDep, SettingsDep, UserDep

__all__ = ["AuthDep", "DbDep", "SettingsDep", "UserDep"]

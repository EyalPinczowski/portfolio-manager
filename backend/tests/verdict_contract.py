"""Contract check: no response model may expose a buy/sell verdict unless its route is gated.

`find_violations(app, allowlist)` walks the app's OpenAPI schema, follows every `$ref` reachable
from each operation's responses, and flags field names that look like a verdict. A field may be
allowlisted as "SchemaName.field_name", but only if EVERY route that returns that schema
declares the `require_launch_gate` dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.launchgate import require_launch_gate

VERDICT_TOKENS = frozenset(
    {
        "verdict", "verdicts", "recommendation", "recommendations", "recommend", "recommended",
        "action", "actions", "buy", "buys", "sell", "sells", "hold", "advice", "suggestion",
        "suggestions", "stance", "conviction", "upgrade", "downgrade",
    }
)  # fmt: skip
VERDICT_PHRASES = ("signal_strength", "signalstrength", "buy_idea", "buyidea")
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def verdict_token(field: str) -> str | None:
    """The offending token if `field` looks like a verdict, else None."""
    flat = field.lower().replace("-", "_")
    for phrase in VERDICT_PHRASES:
        if phrase in flat:
            return phrase
    for part in _WORD.findall(field.replace("_", " ").replace("-", " ")):
        if part.lower() in VERDICT_TOKENS:
            return part.lower()
    return None


@dataclass(frozen=True)
class Violation:
    method: str
    path: str
    schema: str
    field: str
    reason: str

    def __str__(self) -> str:
        return f"{self.method} {self.path}: {self.schema}.{self.field}: {self.reason}"


def _refs(node: Any) -> list[str]:
    """Every component-schema name directly referenced anywhere inside `node`."""
    found: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.append(ref.rsplit("/", 1)[1])
        for value in node.values():
            found.extend(_refs(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_refs(value))
    return found


def reachable_schemas(components: dict[str, Any], roots: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen or name not in components:
            continue
        seen[name] = None
        stack.extend(_refs(components[name]))
    return list(seen)


def _walk_dependants(dep: Any) -> list[Any]:
    out = [dep.call]
    for sub in dep.dependencies:
        out.extend(_walk_dependants(sub))
    return out


def route_is_gated(app: FastAPI, path: str, method: str) -> bool:
    for route in app.routes:
        if (
            isinstance(route, APIRoute)
            and route.path_format == path
            and method.upper() in route.methods
        ):
            return any(call is require_launch_gate for call in _walk_dependants(route.dependant))
    return False


def find_violations(app: FastAPI, allowlist: dict[str, str] | None = None) -> list[Violation]:
    allow = allowlist or {}
    spec = app.openapi()
    components: dict[str, Any] = spec.get("components", {}).get("schemas", {})
    out: list[Violation] = []
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            roots = _refs(op.get("responses", {}))
            gated = route_is_gated(app, path, method)
            for schema in reachable_schemas(components, roots):
                for field in components[schema].get("properties", {}):
                    token = verdict_token(field)
                    if token is None:
                        continue
                    key = f"{schema}.{field}"
                    if key not in allow:
                        out.append(
                            Violation(
                                method.upper(),
                                path,
                                schema,
                                field,
                                f"verdict-like field ('{token}')",
                            )
                        )
                    elif not gated:
                        out.append(
                            Violation(
                                method.upper(),
                                path,
                                schema,
                                field,
                                "allowlisted, but the route does not depend on require_launch_gate",
                            )
                        )
    return out

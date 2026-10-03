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
        # Added in Phase 2.0 after the re-review showed these all slipped through:
        "rating", "ratings", "outlook", "target", "targets", "bullish", "bearish", "trim",
        "add", "strong", "opinion", "opinions", "call", "calls", "signal", "grade", "grades",
    }
)  # fmt: skip
VERDICT_PHRASES = ("signal_strength", "signalstrength", "buy_idea", "buyidea")
# Words glued together or in capitals (`STRONGBUY`, `bullish`, `sellnow`) never split into tokens, so
# these stems are also searched inside the flattened name. `hold` is not here (it is in `holding`).
VERDICT_STEMS = ("buy", "sell", "bullish", "bearish")
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def value_token(value: str) -> str | None:
    """The offending token if an enum/const VALUE (not a field name) reads like a verdict."""
    return verdict_token(value)


def verdict_token(field: str) -> str | None:
    """The offending token if `field` looks like a verdict, else None."""
    flat = field.lower().replace("-", "_")
    for phrase in VERDICT_PHRASES:
        if phrase in flat:
            return phrase
    for part in _WORD.findall(re.sub(r"[^A-Za-z0-9]+", " ", field)):
        if part.lower() in VERDICT_TOKENS:
            return part.lower()
    squashed = re.sub(r"[^a-z0-9]", "", flat)
    for stem in VERDICT_STEMS:
        if stem in squashed:
            return stem
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


def _enum_values(node: Any) -> list[tuple[str, str]]:
    """(property path, string value) for every `enum`/`const` inside a schema (Literal and Enum)."""
    found: list[tuple[str, str]] = []

    def walk(n: Any, where: str) -> None:
        if isinstance(n, dict):
            for key in ("enum",):
                for v in n.get(key, []) if isinstance(n.get(key), list) else []:
                    if isinstance(v, str):
                        found.append((where, v))
            if isinstance(n.get("const"), str):
                found.append((where, n["const"]))
            for k, v in n.items():
                if k == "properties" and isinstance(v, dict):
                    for prop, sub in v.items():
                        walk(sub, prop)
                else:
                    walk(v, where)
        elif isinstance(n, list):
            for item in n:
                walk(item, where)

    walk(node, "<schema>")
    return found


def is_untyped(node: Any) -> bool:
    """True for `Any`, a bare `dict`, or `dict[str, Any]` anywhere inside a property/response schema."""
    if not isinstance(node, dict):
        return False
    if "$ref" in node:
        return False
    for key in ("anyOf", "oneOf", "allOf"):
        if key in node and any(is_untyped(sub) for sub in node[key]):
            return True
    if node.get("type") == "array":
        return is_untyped(node.get("items", {}))
    bare = not any(k in node for k in ("type", "anyOf", "oneOf", "allOf", "enum", "const"))
    if node.get("type") == "object" or bare:  # `Any` renders as a node with no type at all
        if "properties" in node:
            return False
        extra = node.get("additionalProperties", True)
        if extra is True or extra == {}:
            return True
        return is_untyped(extra)
    return False


def find_violations(
    app: FastAPI,
    allowlist: dict[str, str] | None = None,
    untyped_allowlist: dict[str, str] | None = None,
    used_untyped: set[str] | None = None,
    benign: dict[str, str] | None = None,
) -> list[Violation]:
    """Verdict-like field names, verdict-like enum VALUES, and untyped (`dict[str, Any]`) payloads.

    `allowlist` ("Schema.field") is for verdict fields and only legal on gated routes.
    `untyped_allowlist` is for `dict[str, Any]` fields ("Schema.field") and untyped responses
    ("METHOD /path"); each entry needs a reason, and `used_untyped` collects the entries that were
    needed so a test can fail on stale ones (also those of `benign`).
    `benign` ("Schema.field") lists verdict-looking names or enum values that are NOT verdicts, each
    with the reason, for example the user's own recorded trade type.
    """
    allow = allowlist or {}
    untyped_ok = untyped_allowlist or {}
    harmless = benign or {}
    spec = app.openapi()
    components: dict[str, Any] = spec.get("components", {}).get("schemas", {})
    out: list[Violation] = []

    def untyped(key: str, method: str, path: str, schema: str, field: str, what: str) -> None:
        if key in untyped_ok:
            if used_untyped is not None:
                used_untyped.add(key)
            return
        out.append(Violation(method.upper(), path, schema, field, what))

    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            roots = _refs(op.get("responses", {}))
            gated = route_is_gated(app, path, method)
            route_key = f"{method.upper()} {path}"
            for code, resp in op.get("responses", {}).items():
                content = resp.get("content", {}).get("application/json")
                if (
                    str(code).startswith("2")
                    and content is not None
                    and is_untyped(content.get("schema", {}))
                ):
                    untyped(
                        route_key, method, path, "<response>", code, "untyped response (Any/dict)"
                    )
            for schema in reachable_schemas(components, roots):
                body = components[schema]
                for field, prop in body.get("properties", {}).items():
                    if is_untyped(prop):
                        untyped(
                            f"{schema}.{field}", method, path, schema, field,
                            "untyped field (dict[str, Any]/Any)",
                        )  # fmt: skip
                    token = verdict_token(field)
                    if token is None:
                        continue
                    key = f"{schema}.{field}"
                    if key in harmless:
                        if used_untyped is not None:
                            used_untyped.add(key)
                        continue
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
                for where, value in _enum_values(body):
                    vtoken = value_token(value)
                    if vtoken is None:
                        continue
                    key = f"{schema}.{where}"
                    if key in harmless:
                        if used_untyped is not None:
                            used_untyped.add(key)
                        continue
                    if key in allow and gated:
                        continue
                    reason = f"verdict-like enum value {value!r} ('{vtoken}')"
                    if key in allow:
                        reason += (
                            "; allowlisted, but the route does not depend on require_launch_gate"
                        )
                    out.append(Violation(method.upper(), path, schema, where, reason))
    return out

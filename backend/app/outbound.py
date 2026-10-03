"""The one function every outgoing message passes through.

Telegram messages, `Notification` rows and the weekly review all call `release_text(text)`.

While the launch gate is closed the gate is by **type**: only `TemplateText` may leave. It is built
by `render(template, **fields)` from a fixed template and typed fields (`Symbol`, `CurrencyCode`,
numbers, dates, `Movement`); free text, even neutral, is refused with `OutboundBlocked`, so user
data (a symbol like `BUY-USD`) and LLM output cannot carry a verdict out. The template itself is
also scanned for verdict words (`app.verdict_words`: NFKC, zero-width characters, look-alike
letters, -ing forms, Hebrew) as defence in depth. The "Not financial advice." disclaimer is ignored.

`tests/test_outbound_gate.py` fails if any module creates a `Notification` outside
`make_notification`, sends a Telegram message without going through here, or calls `render` with
anything but a constant template. Once the gate opens, verdict text must be built from a
`Verdict` that `LaunchGate.release()` returned.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from app.config import DISCLAIMER, Settings, get_settings
from app.launchgate import LaunchGate, get_launch_gate
from app.verdict_words import verdict_words_in_text

if TYPE_CHECKING:
    from app.models import Notification


class OutboundBlocked(ValueError):
    """The text contains verdict words while the launch gate is closed."""

    def __init__(self, words: list[str]) -> None:
        super().__init__(
            "outgoing text blocked while the launch gate is closed: " + ", ".join(words)
        )
        self.words = words


def verdict_words_in(text: str, settings: Settings | None = None) -> list[str]:
    """Verdict words in `text` (NFKC, invisible characters, look-alikes, inflections, Hebrew)."""
    s = settings or get_settings()
    return verdict_words_in_text(text, s.outbound_verdict_words, ignore=(DISCLAIMER,))


# ---------------------------------------------------------------- typed, template-built text
_MINT: Final = object()
_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9.^=\-]{0,23}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class TemplateText(str):
    """Outgoing text built only by `render()` from a fixed template and typed fields.

    You cannot construct one directly (`TemplateText("Buy now")` raises). While the launch gate is
    closed this is the only text the outbound channels accept.
    """

    __slots__ = ()

    def __new__(cls, value: str, _mint: object = None) -> TemplateText:
        if _mint is not _MINT:
            raise TypeError("TemplateText is built with outbound.render(), not directly")
        return super().__new__(cls, value)


class Symbol(str):
    """A ticker or pair (letters, digits, `.`, `^`, `=`, `-`, at most 24 characters)."""

    __slots__ = ()

    def __new__(cls, value: str) -> Symbol:
        if not _SYMBOL.fullmatch(value):
            raise ValueError("not a ticker symbol")
        return super().__new__(cls, value)


class CurrencyCode(str):
    """A three-letter upper-case currency code."""

    __slots__ = ()

    def __new__(cls, value: str) -> CurrencyCode:
        if not _CURRENCY.fullmatch(value):
            raise ValueError("not a currency code")
        return super().__new__(cls, value)


class Movement(StrEnum):
    """Direction words for price rules. They describe where the price is, never what to do."""

    ABOVE = "rose to or above"
    BELOW = "fell to or below"
    REACHED = "reached"


if TYPE_CHECKING:
    Field = Symbol | CurrencyCode | Movement | int | float | Decimal | date | datetime
else:
    Field = (Symbol, CurrencyCode, Movement, int, float, Decimal, date, datetime)


def _field_text(name: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, Field):
        raise TypeError(
            f"template field {name!r}: free text is not allowed ({type(value).__name__})"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"template field {name!r} is not finite")
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def render(
    template: str,
    *,
    gate: LaunchGate | None = None,
    settings: Settings | None = None,
    **fields: Any,
) -> TemplateText:
    """Fill a fixed template with typed fields.

    The template (not the fields) is scanned for verdict words; fields cannot carry words because
    they are tickers, currency codes, numbers, dates or `Movement` values. A template with a
    verdict word is refused while the gate is closed, an unknown or missing placeholder is a bug.
    """
    check_template(template, gate=gate, settings=settings)
    names = set(_PLACEHOLDER.findall(template))
    if names != set(fields):
        raise ValueError(f"template placeholders {sorted(names)} != fields {sorted(fields)}")
    out = _PLACEHOLDER.sub(lambda m: _field_text(m.group(1), fields[m.group(1)]), template)
    return TemplateText(out, _MINT)


def join_lines(*parts: TemplateText) -> TemplateText:
    """Join already-rendered texts (a title and a body) into one message."""
    if not all(isinstance(p, TemplateText) for p in parts):
        raise TypeError("join_lines takes only TemplateText parts")
    return TemplateText("\n".join(parts), _MINT)


def check_template(
    template: str, *, gate: LaunchGate | None = None, settings: Settings | None = None
) -> None:
    words = verdict_words_in(template, settings)
    if words and not (gate or get_launch_gate()).evaluate().open:
        raise OutboundBlocked(words)


def release_text(
    text: str, *, gate: LaunchGate | None = None, settings: Settings | None = None
) -> str:
    """Return `text` when it may go out, else raise `OutboundBlocked`.

    Gate closed: only `TemplateText` passes (free text is refused outright; the template was
    scanned when it was rendered). Gate open: any text passes.
    """
    if isinstance(text, TemplateText):
        return text
    open_ = (gate or get_launch_gate()).evaluate().open
    if open_:
        return text
    raise OutboundBlocked(verdict_words_in(text, settings) or ["<free text>"])


def make_notification(
    user_id: int,
    kind: str,
    title: str,
    body: str,
    *,
    gate: LaunchGate | None = None,
    settings: Settings | None = None,
) -> Notification:
    """The one place a `Notification` row is built; both texts go through `release_text`."""
    from app.models import Notification

    return Notification(
        user_id=user_id,
        kind=kind,
        title=release_text(title, gate=gate, settings=settings),
        body=release_text(body, gate=gate, settings=settings),
    )

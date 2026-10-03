"""The one function every outgoing message passes through.

Telegram messages, `Notification` rows and the weekly review all call `release_text(text)`. While the
launch gate is closed, text that reads like a buy/sell verdict (whole words from
`Settings.outbound_verdict_words`, constants and f-strings included) is refused with
`OutboundBlocked`. The "Not financial advice." disclaimer is ignored by the scan.

`tests/test_outbound_gate.py` fails if any module creates a `Notification` or sends a Telegram
message without going through here. Once the gate opens, verdict text must be built from a
`Verdict` that `LaunchGate.release()` returned.
"""

from __future__ import annotations

import re

from app.config import DISCLAIMER, Settings, get_settings
from app.launchgate import LaunchGate, get_launch_gate


class OutboundBlocked(ValueError):
    """The text contains verdict words while the launch gate is closed."""

    def __init__(self, words: list[str]) -> None:
        super().__init__(
            "outgoing text blocked while the launch gate is closed: " + ", ".join(words)
        )
        self.words = words


def verdict_words_in(text: str, settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    cleaned = text.replace(DISCLAIMER, " ")
    found: list[str] = []
    for word in s.outbound_verdict_words:
        if re.search(rf"\b{re.escape(word)}(?:s|ed)?\b", cleaned, flags=re.IGNORECASE):
            found.append(word)
    return found


def release_text(
    text: str, *, gate: LaunchGate | None = None, settings: Settings | None = None
) -> str:
    """Return `text` unchanged when it may go out, else raise `OutboundBlocked`."""
    words = verdict_words_in(text, settings)
    if not words:
        return text
    if (gate or get_launch_gate()).evaluate().open:
        return text
    raise OutboundBlocked(words)

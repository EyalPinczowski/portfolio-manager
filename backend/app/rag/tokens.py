"""A cheap, config-driven token estimator (no tokenizer dependency). Hebrew packs fewer characters
per token than English, so the two scripts are counted separately. It over-estimates slightly on
purpose: a budget built on it is a safe upper bound."""

from __future__ import annotations

import math

from app.config import Settings, get_settings


def estimate_tokens(text: str, settings: Settings | None = None) -> int:
    if not text:
        return 0
    s = settings or get_settings()
    hebrew = sum(1 for ch in text if "֐" <= ch <= "׿")
    other = len(text) - hebrew
    return max(
        1,
        math.ceil(hebrew / s.rag_chars_per_token_hebrew + other / s.rag_chars_per_token_latin),
    )

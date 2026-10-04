"""`PublicFacts`: the only thing an AI provider may be shown about a stock.

It holds public market data and the computed chart numbers, and nothing about the user: no portfolio,
holdings, amounts, notes or questions. Prompts are built from this typed object, so personal data
cannot reach a free provider by construction (the scrubber in `app/llm` stays as defence in depth).
The template summary is the default path; an LLM is optional and always falls back to the template.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.analyze.schemas import ChartReport, ScoutReport
from app.llm.base import LLMError, LLMProvider, LLMRequest
from app.verdict_words import verdict_words_in_text

log = logging.getLogger(__name__)


class PublicFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    symbol: str
    name: str
    market: str
    asset_type: str
    sector: str
    country: str
    price: float | None = None
    currency: str | None = None
    price_as_of: datetime | None = None
    score: float | None = None  # null when no signal has data
    confidence: float = 0.0
    indicators: dict[str, float] = Field(default_factory=dict)
    levels: list[str] = Field(default_factory=list)  # "support 101.2 (-3.1%)"
    reasons: list[str] = Field(default_factory=list)


def public_facts(scout: ScoutReport, chart: ChartReport) -> PublicFacts:
    reasons = [r for line in chart.breakdown if line.available for r in line.reasons][:12]
    return PublicFacts(
        symbol=scout.symbol,
        name=scout.name_en,
        market=scout.market,
        asset_type=scout.asset_type,
        sector=scout.sector,
        country=scout.country,
        price=scout.price.price if scout.price else None,
        currency=scout.price.currency if scout.price else None,
        price_as_of=scout.price.as_of if scout.price else None,
        score=chart.score if chart.available else None,
        confidence=chart.confidence,
        indicators=dict(chart.indicators),
        levels=[f"{lv.kind} {lv.price:g} ({lv.distance_pct:+.1f}%)" for lv in chart.levels],
        reasons=reasons,
    )


def template_summary(f: PublicFacts) -> str:
    """The default summary: plain text built from the computed numbers only."""
    head = f"{f.symbol} ({f.name}), {f.asset_type} on {f.market}."
    if f.score is None:
        return f"{head} No chart signal has enough data yet, so no score is shown."
    parts = [
        head,
        f"Chart score {f.score:+.0f} (confidence {f.confidence:.0%}) from the technical and pattern signals.",
    ]
    if f.reasons:
        parts.append(" ".join(f.reasons[:3]))
    if f.levels:
        parts.append("Nearby levels: " + ", ".join(f.levels[:4]) + ".")
    return " ".join(parts)


def build_prompt(f: PublicFacts) -> str:
    """The prompt for an optional AI summary: public data only, numbers copied from the facts."""
    return (
        "Write a short neutral summary (max 90 words) of this stock's chart for a retail investor. "
        "Use only the numbers below, do not add numbers, do not give an instruction to trade.\n"
        + f.model_dump_json(exclude_none=True)
    )


def summarize(f: PublicFacts, provider: LLMProvider | None = None) -> tuple[str, bool]:
    """(text, llm_used). Any provider failure or empty answer falls back to the template."""
    if provider is None:
        return template_summary(f), False
    try:
        out = provider.complete(LLMRequest(role="analyze_summary", prompt=build_prompt(f)))
    except LLMError:
        log.info("analyze summary: provider failed, using the template")
        return template_summary(f), False
    text = out.text.strip()
    if not text or verdict_words_in_text(text):  # an instruction to trade never goes out
        return template_summary(f), False
    return text, True

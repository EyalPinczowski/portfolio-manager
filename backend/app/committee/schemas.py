"""Typed hand-offs of the RAG-backed committee roles (validated Pydantic models, never free text).

Field and value names avoid verdict words on purpose: the CIO output is a bounded score
adjustment plus answers to the Bear's risks, not a buy/sell call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(extra="forbid", allow_inf_nan=False)
ProfileTopic = Literal["business", "segments", "management", "competitors"]
Tone = Literal["positive", "neutral", "negative"]
Stance = Literal["rebutted", "accepted", "unresolved"]


class ProfileClaim(BaseModel):
    model_config = STRICT
    topic: ProfileTopic
    text: str = Field(min_length=1, max_length=600)
    chunk_ids: list[int] = Field(default_factory=list)


class CompanyProfile(BaseModel):
    model_config = STRICT
    claims: list[ProfileClaim] = Field(default_factory=list)


class NewsItem(BaseModel):
    model_config = STRICT
    text: str = Field(min_length=1, max_length=600)
    tone: Tone = "neutral"
    chunk_ids: list[int] = Field(default_factory=list)


class NewsReport(BaseModel):
    model_config = STRICT
    items: list[NewsItem] = Field(default_factory=list)


class BearRisk(BaseModel):
    model_config = STRICT
    text: str = Field(min_length=1, max_length=600)
    severity: int = Field(ge=1, le=5)
    chunk_ids: list[int] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)  # keys of the PublicFacts used
    what_would_invalidate: str = Field(default="", max_length=400)


class BearCase(BaseModel):
    model_config = STRICT
    risks: list[BearRisk] = Field(default_factory=list)


class RiskResponse(BaseModel):
    model_config = STRICT
    risk_index: int = Field(ge=0)
    stance: Stance
    reason: str = Field(min_length=1, max_length=600)
    chunk_ids: list[int] = Field(default_factory=list)


class CIOAssessment(BaseModel):
    """What the model returns. The adjusted score is computed by code, never by the model."""

    model_config = STRICT
    adjustment: float = Field(ge=-100, le=100)  # tightened to the configured cap after parsing
    adjustment_reason: str = Field(default="", max_length=600)
    responses: list[RiskResponse] = Field(default_factory=list)


class Citation(BaseModel):
    chunk_id: int
    doc_type: str
    source_url: str
    as_of: datetime


class RoleResult[T: BaseModel](BaseModel):
    role: str
    value: T
    source: Literal["llm", "cache", "template"]
    status: Literal["ok", "no_coverage"]
    confidence: float = Field(ge=0, le=1)
    citations: list[Citation] = Field(default_factory=list)
    prompt_tokens: int = 0
    budget: int = 0
    notes: list[str] = Field(default_factory=list)


class CIOResult(BaseModel):
    base_score: float | None
    adjustment: float
    adjusted_score: float | None
    assessment: CIOAssessment


class CommitteeReport(BaseModel):
    symbol: str
    profile: RoleResult[CompanyProfile]
    news: RoleResult[NewsReport]
    bear: RoleResult[BearCase]
    cio: RoleResult[CIOAssessment]
    cio_score: CIOResult

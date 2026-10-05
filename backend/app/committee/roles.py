"""The four RAG-backed committee roles: company profile, news, bear, CIO.

Flow of every role: retrieve public chunks under the role budget (`Retriever`), build the prompt
from `PublicFacts` + chunks only (`rag.prompt.build_prompt`), call `structured_call` (cache ->
provider chain -> validate -> retry once -> template), then check the answer: every cited chunk must
be one that was in the prompt, every number must appear in the prompt, and no verdict word may
appear. A check that fails means the template answer is used. No chunks means no LLM call at all:
the result is empty with `confidence=0` (never invented).

There is no user id, portfolio or free-text parameter anywhere in this module: the roles see public
facts only, so a free provider cannot receive personal data by construction. No `Verdict` is made
here; the CIO only moves the deterministic score by a bounded amount.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.analyze.public_facts import PublicFacts
from app.committee.schemas import (
    BearCase,
    BearRisk,
    CIOAssessment,
    CIOResult,
    Citation,
    CommitteeReport,
    CompanyProfile,
    NewsItem,
    NewsReport,
    ProfileClaim,
    RiskResponse,
    RoleResult,
)
from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.structured import structured_call
from app.models import DocChunk
from app.rag.prompt import BuiltPrompt, PromptRejected, build_prompt
from app.rag.retriever import Hit, RetrievalResult, Retriever
from app.verdict_words import verdict_words_in_text

log = logging.getLogger(__name__)

# What each role retrieves: a fixed query and the doc types it may see (not user-controllable).
ROLE_RETRIEVAL: dict[str, tuple[str, list[str]]] = {
    "company_profile": (
        "company business segments revenue management CEO competitors products",
        ["profile", "filing"],
    ),
    "news": ("company announced results revenue quarter guidance news", ["news", "transcript"]),
    "bear": (
        "company risk regulation debt litigation competition decline investigation",
        ["filing", "news", "transcript"],
    ),
    "cio": ("company risk guidance results management", ["filing", "news", "transcript"]),
}
SYSTEMS: dict[str, str] = {
    "company_profile": "You are a company-profile analyst. Reply with JSON only.",
    "news": "You classify and summarise retrieved news neutrally. Reply with JSON only.",
    "bear": (
        "You are the Bear analyst: list risks against owning this stock, from the given facts and "
        "passages only. Reply with JSON only."
    ),
    "cio": (
        "You are the CIO. You may adjust the deterministic score only by the allowed amount and "
        "must answer every Bear risk by its index. Reply with JSON only."
    ),
}
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _citations(hits: Sequence[Hit]) -> list[Citation]:
    return [
        Citation(chunk_id=h.chunk_id, doc_type=h.doc_type, source_url=h.source_url, as_of=h.as_of)
        for h in hits
    ]


def _report_chunk_ids(reports: Sequence[BaseModel]) -> set[int]:
    """Every chunk id the typed reports cite (they are the only passages a k=0 role sees)."""
    found: set[int] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "chunk_ids" and isinstance(value, list):
                    found.update(i for i in value if isinstance(i, int))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for r in reports:
        walk(r.model_dump())
    return found


def _hits_by_id(db: Session, symbol: str, ids: set[int]) -> list[Hit]:
    """The cited chunks of this symbol, for citations and templates (never put in a prompt)."""
    if not ids:
        return []
    rows = db.exec(
        select(DocChunk).where(col(DocChunk.id).in_(ids), DocChunk.symbol == symbol.strip().upper())
    ).all()
    return [
        Hit(
            chunk_id=r.id, symbol=r.symbol, market=r.market, doc_type=r.doc_type,
            source_url=r.source_url, as_of=r.as_of, text=r.text, token_count=r.token_count,
            score=0.0,
        )
        for r in sorted(rows, key=lambda r: r.id or 0)
        if r.id is not None
    ]  # fmt: skip


def _first_sentence(text: str, limit: int = 300) -> str:
    one = " ".join(text.split())
    return _SENTENCE.split(one, maxsplit=1)[0][:limit]


def numbers_grounded(texts: Sequence[str], prompt_text: str) -> bool:
    """Every number in the answer text also appears in the prompt (rounding aside)."""
    known = {n.replace(",", ".") for n in _NUM.findall(prompt_text)}
    return all(n.replace(",", ".") in known for t in texts for n in _NUM.findall(t))


def _clean_text(texts: Sequence[str], prompt_text: str) -> bool:
    return not any(verdict_words_in_text(t) for t in texts) and numbers_grounded(texts, prompt_text)


# ------------------------------------------------------------------ templates (the default path)
def _profile_template(hits: Sequence[Hit]) -> CompanyProfile:
    return CompanyProfile(
        claims=[
            ProfileClaim(topic="business", text=_first_sentence(h.text), chunk_ids=[h.chunk_id])
            for h in hits[:3]
            if h.text.strip()
        ]
    )


def _news_template(hits: Sequence[Hit]) -> NewsReport:
    return NewsReport(
        items=[
            NewsItem(text=_first_sentence(h.text), tone="neutral", chunk_ids=[h.chunk_id])
            for h in hits[:4]
            if h.text.strip()
        ]
    )


def _bear_template(facts: PublicFacts, hits: Sequence[Hit]) -> BearCase:
    risks: list[BearRisk] = []
    if facts.score is None:
        risks.append(
            BearRisk(
                text="No chart signal has enough data, so the picture is incomplete.",
                severity=2,
                fact_refs=["score"],
                what_would_invalidate="More price history becoming available.",
            )
        )
    elif facts.score < 0:
        risks.append(
            BearRisk(
                text="The combined chart score is negative.",
                severity=3,
                fact_refs=["score"],
                what_would_invalidate="The chart score turning positive.",
            )
        )
    if facts.confidence < 0.5:
        risks.append(
            BearRisk(
                text="Signal confidence is low.",
                severity=2,
                fact_refs=["confidence"],
                what_would_invalidate="Confidence rising as more data arrives.",
            )
        )
    for h in hits[:2]:
        risks.append(
            BearRisk(
                text=_first_sentence(h.text), severity=2, chunk_ids=[h.chunk_id],
                what_would_invalidate="A later filing or news item that contradicts this.",
            )
        )  # fmt: skip
    return BearCase(risks=risks)


def _cio_template(bear: BearCase) -> CIOAssessment:
    return CIOAssessment(
        adjustment=0.0,
        adjustment_reason="No adjustment: the deterministic score stands.",
        responses=[
            RiskResponse(
                risk_index=i,
                stance="unresolved",
                reason="No assessment available; left unresolved.",
            )
            for i in range(len(bear.risks))
        ],
    )


# ------------------------------------------------------------------ the shared runner
def _run[T: BaseModel](
    *,
    role: str,
    model_cls: type[T],
    db: Session,
    facts: PublicFacts,
    providers: Sequence[LLMProvider] | None,
    settings: Settings,
    now: datetime | None,
    reports: Sequence[BaseModel],
    template: Callable[[Sequence[Hit]], T],
    check: Callable[[T, BuiltPrompt, PublicFacts], T | None],
    news_dependent: bool = False,
    on_demand: bool = True,
    user_id: int | None = None,
) -> RoleResult[T]:
    query, doc_types = ROLE_RETRIEVAL[role]
    passages_off = settings.committee_role_k[role] == 0  # reads the reports, not raw passages
    found = (
        RetrievalResult(status="ok", hits=[], tokens=0, budget=settings.rag_role_budgets[role])
        if passages_off
        else Retriever(db, settings=settings).search(
            facts.symbol, query, doc_types, k=settings.committee_role_k[role], role=role, now=now
        )
    )
    notes: list[str] = []
    hits = found.hits
    if passages_off:
        hits = _hits_by_id(db, facts.symbol, _report_chunk_ids(reports))
    if found.no_coverage and role in ("company_profile", "news"):
        # nothing to summarise: no LLM call, an empty answer with confidence 0
        return RoleResult[T](
            role=role, value=template(hits), source="template", status="no_coverage",
            confidence=0.0, budget=found.budget, notes=["no retrieved text for this stock"],
        )  # fmt: skip
    try:
        built = build_prompt(role, facts, [] if passages_off else list(hits), settings, reports)
    except PromptRejected as exc:
        notes.append(f"prompt rejected: {exc}")
        return RoleResult[T](
            role=role, value=template(hits), source="template",
            status="no_coverage" if found.no_coverage else "ok", confidence=0.0,
            budget=settings.rag_role_budgets[role], notes=notes,
        )  # fmt: skip
    cited = {i for i in built.cited_chunk_ids}
    used = list(hits) if passages_off else [h for h in hits if h.chunk_id in cited]
    res = structured_call(
        role=role,
        model_cls=model_cls,
        system=SYSTEMS[role],
        prompt=built.text,
        template=lambda: template(used),
        cache_scope="global",
        priority="on_demand" if on_demand else "batch",
        news_dependent=news_dependent,
        reuse_unchanged=True,
        providers=providers,
        settings=settings,
        now=now,
        user_id=user_id,
    )
    value, source = res.value, res.source
    if res.source != "template":
        checked = check(res.value, built, facts)
        if checked is None:
            notes.append("answer failed the citation/grounding check; template used")
            value, source = template(used), "template"
        else:
            value = checked
    notes.extend(res.notes)
    covered = bool(used)
    confidence = (
        0.0
        if not covered and role in ("company_profile", "news")
        else _confidence(role, source, covered, facts)
    )
    return RoleResult[T](
        role=role, value=value, source=source, status="ok" if covered or role in ("bear", "cio") else "no_coverage",
        confidence=confidence, citations=_citations(used), prompt_tokens=built.tokens,
        budget=built.budget, notes=notes,
    )  # fmt: skip


def _confidence(role: str, source: str, covered: bool, facts: PublicFacts) -> float:
    if role in ("bear", "cio"):
        base = facts.confidence
        return round(base * (1.0 if covered else 0.5), 3)
    return 0.7 if source != "template" else 0.4


def _ids_ok(ids: Sequence[int], allowed: set[int]) -> bool:
    return bool(ids) and all(i in allowed for i in ids)


# ------------------------------------------------------------------ the roles
def company_profile(
    db: Session,
    facts: PublicFacts,
    *,
    providers: Sequence[LLMProvider] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    user_id: int | None = None,
) -> RoleResult[CompanyProfile]:
    s = settings or get_settings()

    def check(v: CompanyProfile, b: BuiltPrompt, f: PublicFacts) -> CompanyProfile | None:
        allowed = set(b.cited_chunk_ids)
        keep = [c for c in v.claims if _ids_ok(c.chunk_ids, allowed)][: s.committee_max_claims]
        if len(keep) < len(v.claims):
            log.info("company_profile: dropped %d uncited claims", len(v.claims) - len(keep))
        if not keep or not _clean_text([c.text for c in keep], b.text):
            return None
        return CompanyProfile(claims=keep)

    return _run(
        role="company_profile", model_cls=CompanyProfile, db=db, facts=facts, providers=providers,
        settings=s, now=now, reports=(), template=_profile_template, check=check, user_id=user_id,
    )  # fmt: skip


def news(
    db: Session,
    facts: PublicFacts,
    *,
    providers: Sequence[LLMProvider] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    user_id: int | None = None,
) -> RoleResult[NewsReport]:
    s = settings or get_settings()

    def check(v: NewsReport, b: BuiltPrompt, f: PublicFacts) -> NewsReport | None:
        allowed = set(b.cited_chunk_ids)
        keep = [i for i in v.items if _ids_ok(i.chunk_ids, allowed)][: s.committee_max_claims]
        if not keep or not _clean_text([i.text for i in keep], b.text):
            return None
        return NewsReport(items=keep)

    return _run(
        role="news", model_cls=NewsReport, db=db, facts=facts, providers=providers, settings=s,
        now=now, reports=(), template=_news_template, check=check, news_dependent=True,
        user_id=user_id,
    )  # fmt: skip


def bear(
    db: Session,
    facts: PublicFacts,
    news_report: NewsReport | None = None,
    *,
    providers: Sequence[LLMProvider] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    user_id: int | None = None,
) -> RoleResult[BearCase]:
    s = settings or get_settings()
    fact_keys = {"score", "confidence", *facts.indicators}

    def check(v: BearCase, b: BuiltPrompt, f: PublicFacts) -> BearCase | None:
        allowed = set(b.cited_chunk_ids)
        keep = [
            r
            for r in v.risks
            if (r.chunk_ids or r.fact_refs)
            and all(i in allowed for i in r.chunk_ids)
            and all(k in fact_keys for k in r.fact_refs)
        ][: s.committee_max_claims]
        keep.sort(key=lambda r: -r.severity)
        texts = [t for r in keep for t in (r.text, r.what_would_invalidate) if t]
        if not keep or not _clean_text(texts, b.text):
            return None
        return BearCase(risks=keep)

    return _run(
        role="bear", model_cls=BearCase, db=db, facts=facts, providers=providers, settings=s,
        now=now, reports=[news_report] if news_report else [],
        template=lambda hits: _bear_template(facts, hits), check=check, news_dependent=True,
    )  # fmt: skip


def cio(
    db: Session,
    facts: PublicFacts,
    bear_case: BearCase,
    reports: Sequence[BaseModel] = (),
    *,
    providers: Sequence[LLMProvider] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    user_id: int | None = None,
) -> RoleResult[CIOAssessment]:
    """The CIO's answer to the Bear. The score adjustment is capped by config and zero when there
    is no score; the adjusted score itself is computed by `apply_adjustment`, not by the model."""
    s = settings or get_settings()
    cap = s.committee_cio_max_adjustment

    def check(v: CIOAssessment, b: BuiltPrompt, f: PublicFacts) -> CIOAssessment | None:
        allowed = set(b.cited_chunk_ids) | _report_chunk_ids([bear_case, *reports])
        if abs(v.adjustment) > cap or (f.score is None and v.adjustment != 0):
            return None
        if v.adjustment != 0 and not v.adjustment_reason.strip():
            return None  # a reason for every adjustment
        if sorted(r.risk_index for r in v.responses) != list(range(len(bear_case.risks))):
            return None  # every Bear risk answered exactly once
        if any(not all(i in allowed for i in r.chunk_ids) for r in v.responses):
            return None
        texts = [v.adjustment_reason, *(r.reason for r in v.responses)]
        extra = f" {abs(v.adjustment):g} {cap:g} " + " ".join(str(i) for i in range(20))
        return v if _clean_text([t for t in texts if t], b.text + extra) else None

    return _run(
        role="cio", model_cls=CIOAssessment, db=db, facts=facts, providers=providers, settings=s,
        now=now, reports=[bear_case, *reports], template=lambda hits: _cio_template(bear_case),
        check=check, news_dependent=True, user_id=user_id,
    )  # fmt: skip


def apply_adjustment(
    facts: PublicFacts, assessment: CIOAssessment, settings: Settings | None = None
) -> CIOResult:
    """Base score plus the (capped) adjustment. Pure arithmetic; not a verdict."""
    s = settings or get_settings()
    if facts.score is None:
        return CIOResult(
            base_score=None, adjustment=0.0, adjusted_score=None, assessment=assessment
        )
    adj = max(
        -s.committee_cio_max_adjustment, min(s.committee_cio_max_adjustment, assessment.adjustment)
    )
    return CIOResult(
        base_score=facts.score,
        adjustment=adj,
        adjusted_score=max(-100.0, min(100.0, facts.score + adj)),
        assessment=assessment,
    )


def run_committee(
    db: Session,
    facts: PublicFacts,
    *,
    providers: Sequence[LLMProvider] | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    user_id: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> CommitteeReport:
    """The four roles in order. `user_id` (no name: prompts are public) makes the per-user LLM
    budget and per-minute limit apply. After `committee_deadline_seconds` the roles not yet run use
    their templates (no more model calls) and say so in their notes."""
    s = settings or get_settings()
    start = clock()
    late = False

    def kw() -> dict[str, object]:
        nonlocal late
        late = late or clock() - start > s.committee_deadline_seconds
        return {
            "providers": [] if late else providers,
            "settings": s,
            "now": now,
            "user_id": user_id,
        }

    def mark[T: BaseModel](res: RoleResult[T], skipped: bool) -> RoleResult[T]:
        if skipped and res.source == "template":
            res.notes.append(
                f"time limit of {s.committee_deadline_seconds:g} s reached: template used, no model call"
            )
        return res

    profile = company_profile(db, facts, **kw())  # type: ignore[arg-type]
    nw = mark(news(db, facts, **kw()), late)  # type: ignore[arg-type]
    br = mark(bear(db, facts, nw.value, **kw()), late)  # type: ignore[arg-type]
    c = mark(cio(db, facts, br.value, [nw.value], **kw()), late)  # type: ignore[arg-type]
    return CommitteeReport(
        symbol=facts.symbol, profile=profile, news=nw, bear=br, cio=c,
        cio_score=apply_adjustment(facts, c.value, s),
    )  # fmt: skip

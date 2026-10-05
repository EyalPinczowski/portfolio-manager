"""`structured_call`: cache -> provider chain -> validate -> retry once -> template.

The **template path is the default**: with no API key, with `llm_enabled=false`, with an empty
token bucket or when no provider returns a valid answer, the caller's `template()` produces the
result and the `fallbacks` counter in `llm_usage` goes up. Roles never see a vendor error.

Order for one call:
1. the response cache (keyed by the hash of role, prompts, fenced inputs, output schema, cache scope,
   provider and model; an answer from any provider or model for the same prompt is accepted, the
   primary's first; a hit is counted in the ledger);
2. for each provider in order: admission (daily budget with room kept for on-demand calls, the
   user's daily cap, role/user/provider per-minute buckets; refused -> next provider), call it,
   record the request and tokens in the ledger, validate the JSON against the output model; on
   a validation error retry once with the error attached (needs another admission);
3. nothing valid -> `template()`, `fallbacks += 1` under the first provider that was meant to answer.

Providers scrub personal data inside `complete`; nothing here can bypass that.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse, LLMUnavailableError
from app.llm.cache import (
    get_cached_any,
    input_hash,
    model_hash,
    prompt_hash,
    put_cached,
)
from app.llm.ledger import (
    SessionFactory,
    TokenBucket,
    quota_add,
    quota_used,
    record_usage,
    requests_today,
)
from app.llm.providers import build_providers
from app.llm.untrusted import UntrustedText
from app.model_probe import active_model
from app.strictjson import strict_loads

log = logging.getLogger("llm")

Source = Literal["cache", "llm", "template"]
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    source: Source
    provider: str  # "template" when no model answered
    model: str
    model_hash: str
    prompt_hash: str
    input_hash: str
    attempts: int = 0  # provider requests made for this call
    notes: list[str] = field(default_factory=list)  # why each step was skipped (no payloads)


def parse_output[T: BaseModel](text: str, model_cls: type[T]) -> T:
    """JSON text -> validated model. NaN/Infinity and code fences are handled; errors are ValueErrors."""
    m = _FENCE.match(text)
    obj = strict_loads(m.group(1) if m else text)
    return model_cls.model_validate(obj)


def _error_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}"
            for e in exc.errors()[:8]
        )
    return f"not valid JSON ({type(exc).__name__})"


Priority = Literal["on_demand", "batch"]


def _scope_ok(scope: str, untrusted: Sequence[UntrustedText]) -> None:
    if not re.fullmatch(r"global|user:\d+", scope):
        raise ValueError('cache_scope must be "global" or "user:<id>"')
    if scope == "global" and any(b.source == "user" for b in untrusted):
        raise ValueError("text a user wrote can only be cached under that user's scope")


def _admission(
    provider: LLMProvider,
    *,
    role: str,
    user_id: int | None,
    priority: Priority,
    s: Settings,
    bkt: TokenBucket,
    now: datetime | None,
    session_factory: SessionFactory | None,
    request: LLMRequest | None = None,
) -> str | None:
    """Why this provider may not be called now (None: go ahead, and the tokens are taken).

    Checks, cheapest and least harmful first: the provider's daily budget (batch work gets only a
    fraction of it, so on-demand questions always have headroom), the user's daily cap, then the
    per-minute buckets: role and user sub-buckets before the provider's own, so a refused request
    never burns a provider token."""
    name = provider.name
    used = requests_today(name, session_factory=session_factory)
    limit = s.llm_daily_budget * (s.llm_batch_daily_fraction if priority == "batch" else 1.0)
    if used >= limit:
        return f"{name}: daily budget reached for {priority} calls ({used} of {int(limit)})"
    if user_id is not None and quota_used(f"user:{user_id}", session_factory=session_factory) >= (
        s.llm_user_daily_budget
    ):
        return f"{name}: the user's daily budget is used up"
    if not bkt.try_acquire(f"{name}|role:{role}", now, s.llm_role_rpm):
        return f"{name}: role '{role}' is rate limited"
    if user_id is not None and not bkt.try_acquire(f"{name}|user:{user_id}", now, s.llm_user_rpm):
        return f"{name}: the user is rate limited"
    estimate = getattr(provider, "estimate_request_tokens", None)
    if request is not None and estimate is not None:  # a vendor that limits tokens per minute
        model = request.model or provider.model
        reason: str | None = provider.precheck(request, model)  # type: ignore[attr-defined]
        if reason is not None:
            return reason
        need = estimate(request, model)
        if not bkt.try_acquire(f"{name}|tpm", now, s.groq_tokens_per_minute, float(need)):
            return f"{name}: about {need} tokens do not fit this minute's token limit"
    if not bkt.try_acquire(name, now):
        return f"{name}: rate limit bucket empty"
    return None


def structured_call[T: BaseModel](
    *,
    role: str,
    model_cls: type[T],
    system: str,
    prompt: str,
    template: Callable[[], T],
    cache_scope: str,
    untrusted: Sequence[UntrustedText] = (),
    user_id: int | None = None,
    known_names: Sequence[str] = (),
    priority: Priority = "batch",
    news_dependent: bool = False,
    providers: Sequence[LLMProvider] | None = None,
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
    bucket: TokenBucket | None = None,
    now: datetime | None = None,
    use_cache: bool = True,
) -> StructuredResult[T]:
    """One role's answer: cache, then the provider chain, then the template.

    `cache_scope` is required: `"global"` for answers that depend only on public data, `"user:<id>"`
    for anything shaped by a user's data (text a user wrote can only be cached under their scope).
    `news_dependent` selects `llm_cache_ttl_news_hours` for the cache lifetime. `priority` is
    `"on_demand"` for a person waiting for the answer and `"batch"` for background jobs: batch calls
    may only use part of the daily budget. `untrusted` blocks are fenced; user ones are scrubbed.
    """
    s = settings or get_settings()
    _scope_ok(cache_scope, untrusted)
    schema = model_cls.model_json_schema()
    blocks = [(b.label, b.source, b.text) for b in untrusted]
    p_hash = prompt_hash(system, prompt)
    notes: list[str] = []
    chain = list(build_providers(s) if providers is None else providers)
    ttl = s.llm_cache_ttl_news_hours if news_dependent else s.llm_cache_ttl_hours

    routes = s.llm_role_models.get(role, {})

    def model_of(provider: LLMProvider) -> str:
        """The model this role uses on this provider (routing setting; empty = the provider's own)."""
        return routes.get(provider.name) or provider.model

    def key_for(provider_name: str, model: str) -> str:
        return input_hash(
            role, system, prompt, schema, scope=cache_scope, provider=provider_name, model=model,
            untrusted=blocks,
        )  # fmt: skip

    # the key reported when nothing answers: the first provider that was meant to
    key = key_for(chain[0].name, model_of(chain[0])) if chain else key_for("template", "template")

    if use_cache:
        # The same prompt answered (and validated) by another provider or model is as good: look at
        # the chain first, then at every other model the settings know, so a failover day is not
        # paid for again once the primary is back.
        pairs: list[tuple[str, str]] = [(p.name, model_of(p)) for p in chain]
        for name in s.llm_provider_order:
            if name not in ("gemini", "groq"):
                continue
            extra = [
                routes.get(name),
                active_model(name, s),  # type: ignore[arg-type]
                getattr(s, f"{name}_model"),
                *getattr(s, f"{name}_model_fallbacks"),
            ]
            pairs += [(name, m) for m in extra if m]
        keys = list(dict.fromkeys(key_for(n, m) for n, m in pairs))
        hit = get_cached_any(keys, ttl, now, session_factory)
        if hit is not None:
            try:
                result = StructuredResult(
                    value=parse_output(hit.response, model_cls),
                    source="cache",
                    provider=hit.provider,
                    model=hit.model,
                    model_hash=model_hash(hit.provider, hit.model),
                    prompt_hash=p_hash,
                    input_hash=key_for(hit.provider, hit.model),
                )
            except (ValueError, ValidationError):
                notes.append("cached answer no longer validates; ignored")
            else:
                record_usage(hit.provider, hit.model, cache_hits=1, session_factory=session_factory)
                return result

    bkt = bucket or TokenBucket(s, session_factory)
    attempts = 0
    first_meant: tuple[str, str] | None = None

    def spend(provider: LLMProvider, request: LLMRequest) -> LLMResponse | None:
        """One bucket token + one request. The response, or None (reason noted)."""
        nonlocal attempts
        refused = _admission(
            provider, role=role, user_id=user_id, priority=priority, s=s, bkt=bkt, now=now,
            session_factory=session_factory, request=request,
        )  # fmt: skip
        if refused is not None:
            notes.append(refused)
            return None
        attempts += 1
        used_model = request.model or provider.model
        try:
            response = provider.complete(request)
        except LLMUnavailableError as exc:
            attempts -= 1  # nothing was sent
            notes.append(str(exc))
            return None
        except LLMError as exc:
            record_usage(provider.name, used_model, requests=1, session_factory=session_factory)
            if user_id is not None:
                quota_add(f"user:{user_id}", session_factory=session_factory)
            notes.append(str(exc))
            return None
        record_usage(
            provider.name, used_model, requests=1, tokens=response.tokens,
            tokens_in=response.tokens_in, tokens_out=response.tokens_out,
            tokens_cached=response.tokens_cached, session_factory=session_factory,
        )  # fmt: skip
        if user_id is not None:
            quota_add(f"user:{user_id}", session_factory=session_factory)
        return response

    for provider in chain:
        used_model = model_of(provider)
        first_meant = first_meant or (provider.name, used_model)
        request = LLMRequest(
            role=role, system=system, prompt=prompt, untrusted=list(untrusted),
            user_id=user_id, known_names=list(known_names), json_schema=schema,
            max_output_tokens=s.llm_role_max_output_tokens.get(role, s.llm_max_output_tokens),
            model=routes.get(provider.name),
        )  # fmt: skip
        response = spend(provider, request)
        for retry in (False, True):
            if response is None:
                break
            try:
                value = parse_output(response.text, model_cls)
            except (ValueError, ValidationError) as exc:
                summary = _error_summary(exc)
                notes.append(f"{provider.name}: invalid answer ({summary})")
                if retry or response.strict:
                    # a strict-schema answer that still fails is a semantic error: a second call
                    # on the same model would not repair it, so go on to the next provider
                    response = None
                    break
                response = spend(
                    provider,
                    request.model_copy(
                        update={
                            "prompt": f"{prompt}\n\nYour previous answer was rejected: {summary}. "
                            "Reply again with valid JSON that follows the schema."
                        }
                    ),
                )
                continue
            answered = key_for(provider.name, used_model)
            put_cached(
                answered,
                role,
                provider.name,
                used_model,
                value.model_dump_json(),
                now,
                session_factory,
            )
            return StructuredResult(
                value=value,
                source="llm",
                provider=provider.name,
                model=used_model,
                model_hash=model_hash(provider.name, used_model),
                prompt_hash=p_hash,
                input_hash=answered,
                attempts=attempts,
                notes=notes,
            )

    # ---- template: the default path ----
    if first_meant is None:
        name = next(iter(s.llm_provider_order), "template")
        first_meant = (name, active_model(name, s) if name in ("gemini", "groq") else "template")  # type: ignore[arg-type]
        notes.append("no LLM provider is configured (missing key or llm_enabled=false)")
    record_usage(first_meant[0], first_meant[1], fallbacks=1, session_factory=session_factory)
    log.info("llm fallback to template for role %s (%s)", role, "; ".join(notes))
    return StructuredResult(
        value=template(),
        source="template",
        provider="template",
        model="template",
        model_hash=model_hash("template", "template"),
        prompt_hash=p_hash,
        input_hash=key,
        attempts=attempts,
        notes=notes,
    )

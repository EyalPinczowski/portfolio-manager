"""`structured_call`: cache -> provider chain -> validate -> retry once -> template.

The **template path is the default**: with no API key, with `llm_enabled=false`, with an empty
token bucket or when no provider returns a valid answer, the caller's `template()` produces the
result and the `fallbacks` counter in `llm_usage` goes up. Roles never see a vendor error.

Order for one call:
1. the response cache (keyed by the hash of role, prompts and output schema);
2. for each provider in order: take a token from the shared bucket (none -> next provider), call
   it, record the request and tokens in the ledger, validate the JSON against the output model; on
   a validation error retry once with the error attached (needs another token);
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
from app.llm.base import LLMError, LLMProvider, LLMRequest, LLMUnavailableError
from app.llm.cache import get_cached, input_hash, model_hash, prompt_hash, put_cached
from app.llm.ledger import SessionFactory, TokenBucket, record_usage
from app.llm.providers import build_providers
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


def structured_call[T: BaseModel](
    *,
    role: str,
    model_cls: type[T],
    system: str,
    prompt: str,
    template: Callable[[], T],
    providers: Sequence[LLMProvider] | None = None,
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
    bucket: TokenBucket | None = None,
    now: datetime | None = None,
    use_cache: bool = True,
) -> StructuredResult[T]:
    s = settings or get_settings()
    schema = model_cls.model_json_schema()
    key = input_hash(role, system, prompt, schema)
    p_hash = prompt_hash(system, prompt)
    notes: list[str] = []

    if use_cache:
        hit = get_cached(key, s.llm_cache_ttl_hours, now, session_factory)
        if hit is not None:
            try:
                return StructuredResult(
                    value=parse_output(hit.response, model_cls),
                    source="cache",
                    provider=hit.provider,
                    model=hit.model,
                    model_hash=model_hash(hit.provider, hit.model),
                    prompt_hash=p_hash,
                    input_hash=key,
                )
            except (ValueError, ValidationError):
                notes.append("cached answer no longer validates; ignored")

    chain = list(build_providers(s) if providers is None else providers)
    bkt = bucket or TokenBucket(s, session_factory)
    attempts = 0
    first_meant: tuple[str, str] | None = None

    def spend(provider: LLMProvider, request: LLMRequest) -> str | None:
        """One bucket token + one request. The answer text, or None (reason noted)."""
        nonlocal attempts
        if not bkt.try_acquire(provider.name, now):
            notes.append(f"{provider.name}: rate limit bucket empty")
            return None
        attempts += 1
        try:
            response = provider.complete(request)
        except LLMUnavailableError as exc:
            attempts -= 1  # nothing was sent
            notes.append(str(exc))
            return None
        except LLMError as exc:
            record_usage(provider.name, provider.model, requests=1, session_factory=session_factory)
            notes.append(str(exc))
            return None
        record_usage(
            provider.name, provider.model, requests=1, tokens=response.tokens,
            session_factory=session_factory,
        )  # fmt: skip
        return response.text

    for provider in chain:
        first_meant = first_meant or (provider.name, provider.model)
        request = LLMRequest(
            role=role, system=system, prompt=prompt, json_schema=schema,
            max_output_tokens=s.llm_max_output_tokens,
        )  # fmt: skip
        text = spend(provider, request)
        for retry in (False, True):
            if text is None:
                break
            try:
                value = parse_output(text, model_cls)
            except (ValueError, ValidationError) as exc:
                summary = _error_summary(exc)
                notes.append(f"{provider.name}: invalid answer ({summary})")
                if retry:
                    text = None
                    break
                text = spend(
                    provider,
                    request.model_copy(
                        update={
                            "prompt": f"{prompt}\n\nYour previous answer was rejected: {summary}. "
                            "Reply again with valid JSON that follows the schema."
                        }
                    ),
                )
                continue
            put_cached(
                key,
                role,
                provider.name,
                provider.model,
                value.model_dump_json(),
                now,
                session_factory,
            )
            return StructuredResult(
                value=value,
                source="llm",
                provider=provider.name,
                model=provider.model,
                model_hash=model_hash(provider.name, provider.model),
                prompt_hash=p_hash,
                input_hash=key,
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

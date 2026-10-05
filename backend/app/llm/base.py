"""LLM provider protocol and the base class that scrubs personal data before anything is sent.

A role never talks to a vendor directly: it calls `structured_call` (see `structured.py`), which asks a
provider. Every concrete provider extends `BaseLLMProvider` and implements only `_send`; `complete`
is `@final` and runs the scrubber first, so the scrubber cannot be skipped by a role or by an
adapter (`tests/test_llm_scrub.py` fails if a subclass overrides it).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Protocol, final, runtime_checkable

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.llm.scrub import PersonalDataScrubber
from app.llm.untrusted import UNTRUSTED_RULE, UntrustedText, fence, limit_text

log = logging.getLogger("llm")


class LLMRequest(BaseModel):
    role: str  # which committee role or feature asks (for logs and metrics only)
    system: str = ""
    prompt: str  # written by our code (template + provider facts): not personal-data scrubbed
    untrusted: list[UntrustedText] = Field(default_factory=list)  # fenced; user text is scrubbed
    user_id: int | None = None  # the requesting user: only their own names are masked
    known_names: list[str] = Field(default_factory=list)  # e.g. owner names captured at import
    json_schema: dict[str, Any] | None = None  # JSON schema the answer must follow
    max_output_tokens: int | None = None
    model: str | None = None  # route this request to a specific model id (per-role routing)


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    tokens: int = 0  # total tokens the vendor reported (0 when it did not)
    tokens_in: int = 0  # prompt tokens
    tokens_out: int = 0  # answer tokens (thinking tokens included)
    tokens_cached: int = 0  # prompt tokens served from the vendor's cache
    strict: bool = False  # the vendor enforced the JSON schema (a retry cannot repair a bad answer)


class LLMError(Exception):
    """A provider call failed. Messages never contain the prompt, the key or the response body."""


class LLMUnavailableError(LLMError):
    """The provider cannot be used at all right now (no key, disabled)."""


class LLMRateLimitedError(LLMError):
    """The vendor answered 429 (or its quota is exhausted)."""


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Send a request (after scrubbing) and return the raw text. Raises `LLMError`."""


class BaseLLMProvider(ABC):
    name: ClassVar[str]

    def __init__(
        self,
        settings: Settings | None = None,
        scrubber: PersonalDataScrubber | None = None,
        model: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.scrubber = scrubber or PersonalDataScrubber(self.settings)
        self._model = model

    @property
    def model(self) -> str:
        """The configured id (a startup probe may have swapped it for a fallback that exists)."""
        return self._model or self._default_model()

    @abstractmethod
    def _default_model(self) -> str: ...

    @final
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Scrub, fence and send. `system` and `prompt` get the baseline scrub (e-mails, tokens,
        keys), so a role that interpolates a secret cannot leak it; numbers, names and tickers in
        them are provider data and stay. `untrusted` blocks are capped and fenced; those from a
        user also get the full personal-data scrub for the requesting user."""
        s = self.settings
        counts: dict[str, int] = {}

        def merge(res_counts: dict[str, int]) -> None:
            for k, v in res_counts.items():
                counts[k] = counts.get(k, 0) + v

        system = self.scrubber.scrub_baseline(request.system)
        prompt = self.scrubber.scrub_baseline(request.prompt)
        merge(system.counts)
        merge(prompt.counts)
        system_text, prompt_text = system.text, prompt.text
        if request.untrusted:
            blocks: list[str] = []
            for block in request.untrusted:
                body = limit_text(
                    block.text,
                    max_chars=s.llm_untrusted_max_chars,
                    max_urls=s.llm_untrusted_max_urls,
                    url_chars=s.llm_untrusted_url_chars,
                )
                if block.source == "user":
                    res = self.scrubber.scrub_user_text(
                        body, user_id=request.user_id, names=request.known_names
                    )
                else:
                    res = self.scrubber.scrub_baseline(body)
                merge(res.counts)
                blocks.append(fence(block, res.text))
            system_text = f"{system_text}\n\n{UNTRUSTED_RULE}".strip()
            prompt_text = f"{prompt_text}\n\n" + "\n".join(blocks)
        if counts:
            log.info("scrubbed personal data before sending (%s)", counts)  # counts only
        return self._send(
            request.model_copy(
                update={"system": system_text, "prompt": prompt_text, "untrusted": []}
            )
        )

    @abstractmethod
    def _send(self, request: LLMRequest) -> LLMResponse:
        """Transport only. The request has already been scrubbed."""

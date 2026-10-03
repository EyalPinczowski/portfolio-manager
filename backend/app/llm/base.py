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

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.llm.scrub import PersonalDataScrubber

log = logging.getLogger("llm")


class LLMRequest(BaseModel):
    role: str  # which committee role or feature asks (for logs and metrics only)
    system: str = ""
    prompt: str
    json_schema: dict[str, Any] | None = None  # JSON schema the answer must follow
    max_output_tokens: int | None = None


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    tokens: int = 0  # total tokens the vendor reported (0 when it did not)


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
        system = self.scrubber.scrub(request.system)
        prompt = self.scrubber.scrub(request.prompt)
        masked = {
            k: system.counts.get(k, 0) + prompt.counts.get(k, 0)
            for k in {*system.counts, *prompt.counts}
        }
        if masked:
            log.info("scrubbed personal data before sending (%s)", masked)  # counts only
        return self._send(request.model_copy(update={"system": system.text, "prompt": prompt.text}))

    @abstractmethod
    def _send(self, request: LLMRequest) -> LLMResponse:
        """Transport only. The request has already been scrubbed."""

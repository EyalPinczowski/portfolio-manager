"""LLM foundation (Phase 2.0-C): provider protocol, scrubber, quota ledger, cache, `structured_call`."""

from app.llm.base import (
    BaseLLMProvider,
    LLMError,
    LLMProvider,
    LLMRateLimitedError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
)
from app.llm.ledger import TokenBucket, record_usage, usage_for_day
from app.llm.providers import GeminiProvider, GroqProvider, build_providers
from app.llm.scrub import PersonalDataScrubber, ScrubResult
from app.llm.structured import StructuredResult, structured_call
from app.llm.untrusted import UntrustedText

__all__ = [
    "BaseLLMProvider",
    "GeminiProvider",
    "GroqProvider",
    "LLMError",
    "LLMProvider",
    "LLMRateLimitedError",
    "LLMRequest",
    "LLMResponse",
    "LLMUnavailableError",
    "PersonalDataScrubber",
    "ScrubResult",
    "StructuredResult",
    "TokenBucket",
    "UntrustedText",
    "build_providers",
    "record_usage",
    "structured_call",
    "usage_for_day",
]

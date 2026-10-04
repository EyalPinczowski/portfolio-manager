"""A scripted provider for tests and offline runs. It extends `BaseLLMProvider`, so the scrubber
runs exactly as it does for a real vendor, and `seen` records what would have been sent."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import BaseLLMProvider, LLMError, LLMRequest, LLMResponse
from app.llm.scrub import PersonalDataScrubber


class FakeLLMProvider(BaseLLMProvider):
    name = "fake"

    def __init__(
        self,
        responses: list[str | Exception] | None = None,
        *,
        name: str = "fake",
        model: str = "fake-1",
        tokens: int = 25,
        settings: Settings | None = None,
        scrubber: PersonalDataScrubber | None = None,
    ) -> None:
        super().__init__(settings, scrubber, model)
        self.name = name  # type: ignore[misc]
        self.responses = list(responses or [])
        self.tokens = tokens
        self.seen: list[LLMRequest] = []

    def _default_model(self) -> str:
        return "fake-1"

    def _send(self, request: LLMRequest) -> LLMResponse:
        self.seen.append(request)
        if not self.responses:
            raise LLMError("fake provider has no scripted response left")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, provider=self.name, model=self.model, tokens=self.tokens)

"""A deterministic mock LLM for the evals. It extends `BaseLLMProvider`, so the real scrubber runs,
and `seen` records exactly what a vendor would have received. The mode picks how it misbehaves."""

from __future__ import annotations

import json
import re
from typing import ClassVar

from app.llm.base import BaseLLMProvider, LLMError, LLMRequest, LLMResponse

CHUNK_ID = re.compile(r"^\[c(\d+)\] ", re.MULTILINE)  # passage headers, not the rule text


class MockLLM(BaseLLMProvider):
    name: ClassVar[str] = "mock"
    privacy: str | None = None  # "no_training" marks a provider that may see portfolio data

    def __init__(self, mode: str = "good", privacy: str | None = None) -> None:
        super().__init__(model="mock-1")
        self.mode = mode
        self.privacy = privacy
        self.seen: list[LLMRequest] = []

    def _default_model(self) -> str:
        return "mock-1"

    # -- helpers
    def _ids(self, prompt: str) -> list[int]:
        return [int(i) for i in CHUNK_ID.findall(prompt)]

    def _send(self, request: LLMRequest) -> LLMResponse:
        self.seen.append(request)
        if self.mode == "error":
            raise LLMError("mock vendor down")
        if self.mode == "garbage":
            return LLMResponse(text="not json at all", provider="mock", model="mock-1", tokens=5)
        return LLMResponse(
            text=json.dumps(self._answer(request)), provider="mock", model="mock-1", tokens=40
        )

    def _answer(self, r: LLMRequest) -> dict[str, object]:
        ids = self._ids(r.prompt)
        cite = ids[:1] if self.mode != "bad_cite" else [999999]
        text = "The company reports results and faces supervision."
        if self.mode == "verdict":
            text = "You should buy this stock now."
        if self.mode == "number":
            text = "Revenue grew 73.1337 percent."
        role = r.role
        if role == "company_profile":
            return {"claims": [{"topic": "business", "text": text, "chunk_ids": cite}]}
        if role == "news":
            return {"items": [{"text": text, "tone": "neutral", "chunk_ids": cite}]}
        if role == "bear":
            return {
                "risks": [
                    {"text": text, "severity": 3, "chunk_ids": cite, "fact_refs": [],
                     "what_would_invalidate": "A contradicting filing."}
                ]
            }  # fmt: skip
        if role == "cio":
            n = len(re.findall(r'"severity"', r.prompt))
            adj = {"big_adjust": 30.0, "no_score": 5.0}.get(self.mode, -5.0)
            return {
                "adjustment": adj,
                "adjustment_reason": "Evidence is mixed.",
                "responses": [
                    {"risk_index": i, "stance": "accepted", "reason": "Supported by the passages.",
                     "chunk_ids": cite if self.mode != "bad_cite" else []}
                    for i in range(n)
                ],
            }  # fmt: skip
        # ask_portfolio
        tools = re.findall(r'"tool": "(\w+)"', r.prompt)
        cites = [f"tool:{t}" for t in tools[:1]]
        if self.mode == "bad_cite":
            cites = ["tool:get_secrets"]
        body = "Your holdings are listed in the results."
        if self.mode == "number":
            body = "Your portfolio is worth 987654321 ILS."
        if self.mode == "verdict":
            body = "You should sell everything."
        return {"text": body, "cites": cites}

"""Scripted LLM for tests: returns canned objects per schema (or via a callable), never touches the GPU."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from workbench.llm.client import BaseLLM, LLMStats, LLMUnavailable

T = TypeVar("T", bound=BaseModel)


class FakeLLM(BaseLLM):
    name = "fake"

    def __init__(self, responses: dict[str, Any] | None = None, text: str = "Scripted summary.",
                 available: bool = True) -> None:
        self.responses = responses or {}
        self.text = text
        self._available = available
        self.stats = LLMStats()
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    def structured(self, system: str, user: str, schema: type[T], *, max_tokens=None, temperature=None, purpose: str = "") -> T:
        if not self._available:
            raise LLMUnavailable("fake llm disabled")
        self.calls.append({"purpose": purpose, "schema": schema.__name__, "user": user})
        self.stats.record("fake", 0.0, 0, 0, True, purpose)
        resp: Any = self.responses.get(schema.__name__)
        if isinstance(resp, Callable):
            resp = resp(system, user)
        if resp is None:
            raise LLMUnavailable(f"fake llm has no script for {schema.__name__}")
        return resp if isinstance(resp, schema) else schema.model_validate(resp)

    def complete(self, system: str, user: str, *, max_tokens=None, temperature=None, purpose: str = "") -> str:
        if not self._available:
            raise LLMUnavailable("fake llm disabled")
        self.calls.append({"purpose": purpose, "schema": None, "user": user})
        self.stats.record("fake", 0.0, 0, 0, True, purpose)
        return self.text

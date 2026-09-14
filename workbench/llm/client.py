"""Ollama chat client with JSON-schema constrained output.

TODO(build): share HTTP plumbing with src/extractor.py (httpx, /api/chat, format=schema).
Lessons from the knowledge layer: probe the model first, small models echo prompt
examples, ~3 tok/s for deepseek-r1:7b on the 4 GB GPU -> use qwen3:4b for routing.
"""
from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, base_url: str, model: str, num_ctx: int, temperature: float, timeout: int):
        self.base_url, self.model = base_url, model
        self.num_ctx, self.temperature, self.timeout = num_ctx, temperature, timeout

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        raise NotImplementedError

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError

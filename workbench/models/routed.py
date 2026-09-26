"""RoutedLLM: the BaseLLM the agents already call, choosing the model per call.

An agent says what a call is *for* (``purpose="compose_answer"``); the router turns that into
a task kind and picks the best installed model for it. Nothing in the agents changes. The
chosen model rides on every call's stats entry and on the routing log, so a trace can say
which model handled which step.
"""
from __future__ import annotations

import logging
import time
from typing import TypeVar

from pydantic import BaseModel

from workbench.llm.client import BaseLLM, LLMStats, LLMUnavailable, OllamaClient
from workbench.models.router import ModelRouter, kind_of_purpose

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class RoutedLLM(BaseLLM):
    name = "routed"

    def __init__(self, client: OllamaClient, router: ModelRouter, *, budget: str = "normal") -> None:
        self.client = client
        self.router = router
        self.budget = budget
        self.stats: LLMStats = client.stats
        self.last_model: str | None = None
        self.router.note_resident(None)
        self._context: dict[str, str | None] = {"session": None, "run_id": None}

    # ------------------------------------------------------------------ attribution
    @property
    def model(self) -> str:
        """The model most recently used — what the trace attributes a stage to."""
        return self.last_model or self.client.model

    @property
    def default_model(self) -> str:
        return self.client.model

    @property
    def vision_model(self) -> str | None:
        return self.client.vision_model

    def set_context(self, *, session: str | None = None, run_id: str | None = None) -> None:
        self._context = {"session": session, "run_id": run_id}

    def available(self) -> bool:
        return self.client.available()

    def has_model(self, model: str) -> bool:
        return self.client.has_model(model)

    def unload(self) -> None:
        self.client.unload()

    # ------------------------------------------------------------------ routing
    def choose(self, purpose: str, *, needs_vision: bool = False) -> str:
        decision = self.router.route(kind_of_purpose(purpose), purpose=purpose, needs_vision=needs_vision,
                                     budget=self.budget, session=self._context.get("session"), run_id=self._context.get("run_id"))
        chosen = decision.chosen or (self.client.vision_model if needs_vision else self.client.model)
        if not self.client.has_model(chosen):
            fallback = self.client.vision_model if needs_vision else self.client.model
            logger.warning("routed model %s is not pulled; falling back to %s", chosen, fallback)
            chosen = fallback or self.client.model
        return chosen

    def _after(self, model: str) -> None:
        self.last_model = model
        self.router.note_resident(model)

    # ------------------------------------------------------------------ BaseLLM
    def structured(self, system: str, user: str, schema: type[T], *, max_tokens: int | None = None,
                   temperature: float | None = None, purpose: str = "") -> T:
        if not self.available():
            raise LLMUnavailable("Ollama is not reachable")
        model = self.choose(purpose)
        out = self.client.structured(system, user, schema, max_tokens=max_tokens, temperature=temperature,
                                     purpose=purpose, model=model)
        self._after(model)
        return out

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 temperature: float | None = None, purpose: str = "") -> str:
        if not self.available():
            raise LLMUnavailable("Ollama is not reachable")
        model = self.choose(purpose)
        out = self.client.complete(system, user, max_tokens=max_tokens, temperature=temperature, purpose=purpose, model=model)
        self._after(model)
        return out

    def describe_image(self, image_path: str, prompt: str, *, max_tokens: int = 600) -> str:
        model = self.choose("vision", needs_vision=True)
        t0 = time.time()
        out = self.client.describe_image(image_path, prompt, max_tokens=max_tokens, model=model)
        self._after(model)
        logger.info("vision via %s in %.1fs", model, time.time() - t0)
        return out

    # the ResourceManager pokes at these on the raw client
    @property
    def _client(self):
        return self.client._client

"""Local LLM access through Ollama.

The workbench uses the LLM as a *structured function*: ``structured(system, user, Schema)``
returns a validated pydantic object. Ollama's grammar-constrained decoding (``format`` =
JSON schema) guarantees syntactically valid JSON, which is far more reliable on 2-4B models
than generate-then-repair loops. Thinking is disabled (``think=False``) so no tokens are
spent on chain-of-thought inside a 4k context.

Three implementations share the interface:
- OllamaClient   real calls; one model resident, sequential
- NullLLM        LLM-free mode (RWB_LLM=off or Ollama down); ``available()`` is False
- FakeLLM        scripted answers for unit tests (workbench/llm/fake.py)

Lessons carried over from the knowledge layer: probe the server first, small models echo
prompt examples (so prompts hold schemas, not examples), truncated JSON at num_predict.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Raised when a call is attempted and no LLM is available; agents catch it and degrade."""


class LLMOutputError(RuntimeError):
    """Raised when the model's output could not be parsed/validated after retries."""


@dataclass
class LLMStats:
    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_seconds: float = 0.0
    last_model: str = ""
    history: list[dict] = field(default_factory=list)

    def record(self, model: str, seconds: float, prompt_tokens: int, completion_tokens: int, ok: bool, purpose: str) -> None:
        self.calls += 1
        self.failures += 0 if ok else 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_seconds += seconds
        self.last_model = model
        self.history.append({
            "model": model, "seconds": round(seconds, 2), "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens, "ok": ok, "purpose": purpose,
        })
        if len(self.history) > 200:
            del self.history[:-200]


class BaseLLM:
    name: str = "base"
    stats: LLMStats

    def available(self) -> bool:
        return False

    def structured(self, system: str, user: str, schema: type[T], *, max_tokens: int | None = None,
                   temperature: float | None = None, purpose: str = "") -> T:
        raise LLMUnavailable(f"{self.name}: no LLM available")

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 temperature: float | None = None, purpose: str = "") -> str:
        raise LLMUnavailable(f"{self.name}: no LLM available")

    def describe_image(self, image_path: str, prompt: str, *, max_tokens: int = 600) -> str:
        raise LLMUnavailable(f"{self.name}: no vision model available")


class NullLLM(BaseLLM):
    name = "null"

    def __init__(self) -> None:
        self.stats = LLMStats()


def _approx_tokens(text: str) -> int:
    return int(len(text) / 3.6) + 1


def _extract_json(text: str) -> str:
    """Strip code fences / prose around a JSON object."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    if t.startswith("{") and t.endswith("}"):
        return t
    m = re.search(r"\{.*\}", t, flags=re.S)
    return m.group(0) if m else t


class OllamaClient(BaseLLM):
    name = "ollama"

    def __init__(self, base_url: str, model: str, *, num_ctx: int = 4096, keep_alive: str = "10m",
                 timeout: int = 240, temperature: float = 0.1, think: bool = False,
                 num_predict_short: int = 512, num_predict_long: int = 1200, max_retries: int = 1,
                 vision_model: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.vision_model = vision_model
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.timeout = timeout
        self.temperature = temperature
        self.think = think
        self.num_predict_short = num_predict_short
        self.num_predict_long = num_predict_long
        self.max_retries = max_retries
        self.stats = LLMStats()
        self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=5.0))
        self._lock = threading.Lock()              # one request at a time: a 4 GB card cannot parallelise
        self._avail_cache: tuple[float, bool] = (0.0, False)
        self._models_cache: list[str] = []

    # ------------------------------------------------------------------ availability
    def available(self) -> bool:
        ts, ok = self._avail_cache
        if time.time() - ts < 30:
            return ok
        try:
            r = self._client.get("/api/tags", timeout=3.0)
            r.raise_for_status()
            self._models_cache = [m["name"] for m in r.json().get("models", [])]
            ok = True
        except Exception as exc:  # server down
            logger.warning("Ollama not reachable at %s: %s", self.base_url, exc)
            ok = False
        self._avail_cache = (time.time(), ok)
        return ok

    def has_model(self, model: str) -> bool:
        if not self.available():
            return False
        names = set(self._models_cache)
        return model in names or f"{model}:latest" in names

    # ------------------------------------------------------------------ prompt budgeting
    def fit_prompt(self, system: str, user: str, reserve_tokens: int) -> str:
        """Trim the *user* text so system + user + reserve fits in num_ctx."""
        budget = self.num_ctx - reserve_tokens - _approx_tokens(system) - 64
        if budget <= 200:
            budget = 200
        if _approx_tokens(user) <= budget:
            return user
        max_chars = int(budget * 3.6)
        return user[:max_chars] + "\n...[truncated to fit context]"

    # ------------------------------------------------------------------ core call
    def _chat(self, messages: list[dict], *, fmt: Any, num_predict: int, temperature: float, model: str | None = None,
              purpose: str = "") -> dict:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "num_predict": num_predict, "temperature": temperature},
        }
        if fmt is not None:
            payload["format"] = fmt
        t0 = time.time()
        with self._lock:
            r = self._client.post("/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        dt = time.time() - t0
        self.stats.record(
            payload["model"], dt, int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0),
            ok=True, purpose=purpose,
        )
        logger.info("llm %s %s: %.1fs, %s->%s tok", payload["model"], purpose or "-", dt,
                    data.get("prompt_eval_count"), data.get("eval_count"))
        return data

    def structured(self, system: str, user: str, schema: type[T], *, max_tokens: int | None = None,
                   temperature: float | None = None, purpose: str = "") -> T:
        if not self.available():
            raise LLMUnavailable("Ollama is not reachable")
        num_predict = max_tokens or self.num_predict_short
        user = self.fit_prompt(system, user, num_predict)
        json_schema = schema.model_json_schema()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                data = self._chat(messages, fmt=json_schema, num_predict=num_predict,
                                  temperature=self.temperature if temperature is None else temperature, purpose=purpose)
                content = data.get("message", {}).get("content", "")
                if data.get("done_reason") == "length":
                    logger.warning("llm output truncated at num_predict=%s (%s)", num_predict, purpose)
                obj = json.loads(_extract_json(content))
                return schema.model_validate(obj)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                self.stats.failures += 1
                messages = messages[:2] + [
                    {"role": "assistant", "content": content if "content" in locals() else ""},
                    {"role": "user", "content": f"The JSON was invalid: {str(exc)[:300]}. Return only a corrected JSON object."},
                ]
            except httpx.HTTPError as exc:
                last_error = exc
                self.stats.failures += 1
                self._avail_cache = (0.0, False)
                break
        raise LLMOutputError(f"structured call failed ({purpose}): {last_error}")

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 temperature: float | None = None, purpose: str = "") -> str:
        if not self.available():
            raise LLMUnavailable("Ollama is not reachable")
        num_predict = max_tokens or self.num_predict_long
        user = self.fit_prompt(system, user, num_predict)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            data = self._chat(messages, fmt=None, num_predict=num_predict,
                              temperature=self.temperature if temperature is None else temperature, purpose=purpose)
        except httpx.HTTPError as exc:
            self.stats.failures += 1
            self._avail_cache = (0.0, False)
            raise LLMOutputError(f"completion failed ({purpose}): {exc}") from exc
        return data.get("message", {}).get("content", "").strip()

    def describe_image(self, image_path: str, prompt: str, *, max_tokens: int = 600) -> str:
        import base64
        from pathlib import Path

        model = self.vision_model or self.model
        if not self.available():
            raise LLMUnavailable("Ollama is not reachable")
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        messages = [{"role": "user", "content": prompt, "images": [b64]}]
        data = self._chat(messages, fmt=None, num_predict=max_tokens, temperature=0.1, model=model, purpose="vision")
        return data.get("message", {}).get("content", "").strip()

    def unload(self) -> None:
        """Ask Ollama to free the model immediately (useful before the knowledge layer runs)."""
        try:
            self._client.post("/api/generate", json={"model": self.model, "keep_alive": 0})
        except Exception:
            pass


def build_llm(cfg) -> BaseLLM:
    """Factory used by the orchestrator: honours RWB_LLM=off and a dead Ollama."""
    from workbench.config import WorkbenchConfig  # local import to avoid cycles

    assert isinstance(cfg, WorkbenchConfig)
    if not cfg.llm.enabled:
        return NullLLM()
    client = OllamaClient(
        cfg.llm.base_url, cfg.llm.model, num_ctx=cfg.llm.num_ctx, keep_alive=cfg.llm.keep_alive,
        timeout=cfg.llm.timeout_seconds, temperature=cfg.llm.temperature, think=cfg.llm.think,
        num_predict_short=cfg.llm.num_predict_short, num_predict_long=cfg.llm.num_predict_long,
        max_retries=cfg.llm.max_retries, vision_model=cfg.llm.vision_model,
    )
    if not client.available():
        logger.warning("Ollama unavailable; running in LLM-free mode")
        return NullLLM()
    if not client.has_model(cfg.llm.model):
        logger.warning("Model %s not pulled; run `ollama pull %s`. Running in LLM-free mode.", cfg.llm.model, cfg.llm.model)
        return NullLLM()
    return client

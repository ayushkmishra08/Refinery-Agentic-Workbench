"""ResourceManager: nothing stays loaded that is not needed.

- LLM: kept resident while requests are active; unloaded from VRAM ``idle_unload_seconds``
  after the last request finishes (``keep_alive=0`` to Ollama). ``RWB_KEEP_WARM=1`` disables
  the idle unload for demo sessions where latency matters more than free VRAM.
- Vision model: loaded only for an image, released immediately after the call.
- Embedder / reranker (CPU on the 4 GB profile, GPU on larger ones): loaded lazily on first
  use by a task type that needs semantic retrieval; released after the idle window.
- Every load/unload is recorded so the audit block and the status agent can report it.
"""
from __future__ import annotations

import gc
import logging
import os
import threading
import time

from workbench.llm.client import BaseLLM, OllamaClient

logger = logging.getLogger(__name__)


class ResourceManager:
    def __init__(self, llm: BaseLLM, store, idle_unload_seconds: int = 45, keep_warm: bool | None = None) -> None:
        self.llm = llm
        self.store = store                                  # IndexStore (may hold embedder/reranker)
        self.idle_unload_seconds = idle_unload_seconds
        env = os.getenv("RWB_KEEP_WARM", "")
        self.keep_warm = keep_warm if keep_warm is not None else env.lower() in ("1", "true", "on", "yes")
        self._active = 0
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.events: list[dict] = []
        self.llm_loaded = False

    # ------------------------------------------------------------------ request lifecycle
    def begin_request(self) -> None:
        with self._lock:
            self._active += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def end_request(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            if self._active == 0 and not self.keep_warm:
                self._timer = threading.Timer(self.idle_unload_seconds, self.release_idle)
                self._timer.daemon = True
                self._timer.start()

    def note_llm_use(self) -> None:
        self.llm_loaded = True

    # ------------------------------------------------------------------ release
    def release_idle(self) -> None:
        with self._lock:
            if self._active > 0:
                return
        freed: list[str] = []
        if isinstance(self.llm, OllamaClient) and self.llm_loaded:
            self.llm.unload()
            self.llm_loaded = False
            freed.append(f"llm:{self.llm.model}")
        store = self.store
        emb = getattr(store, "_embedder", None)
        if emb is not None and getattr(emb, "_model", None) is not None and getattr(emb, "device", "cpu") != "cpu":
            emb._model = None
            freed.append("embedder")
        rr = getattr(store, "_reranker", None)
        if rr is not None and getattr(rr, "_model", None) is not None and getattr(rr, "device", "cpu") != "cpu":
            rr._model = None
            freed.append("reranker")
        if freed:
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            self.events.append({"ts": time.time(), "released": freed})
            logger.info("released idle resources: %s", freed)

    def release_all(self) -> None:
        """Shutdown path: free everything regardless of activity."""
        with self._lock:
            self._active = 0
            if self._timer:
                self._timer.cancel()
        self.llm_loaded = True
        self.release_idle()

    # ------------------------------------------------------------------ vision (load, use, free)
    def describe_image(self, image_path: str, prompt: str, max_tokens: int = 600) -> str:
        if not isinstance(self.llm, OllamaClient):
            raise RuntimeError("vision requires the Ollama client")
        model = self.llm.vision_model or self.llm.model
        text = self.llm.describe_image(image_path, prompt, max_tokens=max_tokens)
        if model != self.llm.model:
            try:  # free the vision model straight away; the text model comes back on the next call
                self.llm._client.post("/api/generate", json={"model": model, "keep_alive": 0})
                self.events.append({"ts": time.time(), "released": [f"vision:{model}"]})
            except Exception:
                pass
        return text

    def status(self) -> dict:
        return {
            "active_requests": self._active,
            "llm": getattr(self.llm, "model", self.llm.name),
            "llm_loaded": self.llm_loaded,
            "keep_warm": self.keep_warm,
            "idle_unload_seconds": self.idle_unload_seconds,
            "embedder_loaded": bool(getattr(getattr(self.store, "_embedder", None), "_model", None)),
            "reranker_loaded": bool(getattr(getattr(self.store, "_reranker", None), "_model", None)),
            "recent": self.events[-5:],
        }

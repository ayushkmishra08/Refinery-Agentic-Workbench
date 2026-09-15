"""Progress events streamed to the frontend (SSE) while a request runs."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class ProgressEvent(BaseModel):
    event: str            # phase_started | phase_finished | agent_started | agent_finished | plan_created | replan | llm_call | warning | final | error
    phase: str | None = None
    agent: str | None = None
    step_id: str | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


EventSink = Callable[[ProgressEvent], None]


class EventBus:
    """Fan-out of progress events to zero or more sinks (SSE queue, audit log, CLI)."""

    def __init__(self) -> None:
        self._sinks: list[EventSink] = []
        self.history: list[ProgressEvent] = []

    def subscribe(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def emit(self, event: str, **kw: Any) -> ProgressEvent:
        ev = ProgressEvent(event=event, **kw)
        self.history.append(ev)
        for s in list(self._sinks):
            try:
                s(ev)
            except Exception:  # a broken sink must never break the run
                pass
        return ev

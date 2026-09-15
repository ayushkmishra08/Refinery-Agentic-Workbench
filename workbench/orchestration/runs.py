"""RunState + RunRegistry: live state of every request so the API can stream progress and the
status agent can answer "btw" questions without touching the run itself."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from workbench.core.events import ProgressEvent
from workbench.core.result import FinalResponse


class RunState(BaseModel):
    run_id: str
    session_id: str
    request_text: str
    started: float = Field(default_factory=time.time)
    finished: float | None = None
    phase: str = "queued"
    task_type: str | None = None
    safety_status: str | None = None
    entities: list[str] = Field(default_factory=list)
    goal: str | None = None
    step_status: list[dict] = Field(default_factory=list)      # {step_id, agent, goal, depends_on, status, summary}
    current_step: dict | None = None
    recent_events: list[dict] = Field(default_factory=list)
    llm_calls: int = 0
    llm_seconds: float = 0.0
    safety_flags: int = 0
    resources: dict = Field(default_factory=dict)
    final_status: str | None = None
    error: str | None = None
    final: FinalResponse | None = None

    def note_event(self, ev: ProgressEvent) -> None:
        self.recent_events.append({"t": ev.ts - self.started, "event": ev.event, "agent": ev.agent, "message": ev.message[:160] if ev.message else ""})
        if len(self.recent_events) > 60:
            del self.recent_events[:-60]
        if ev.event == "phase_started" and ev.phase:
            self.phase = ev.phase
        if ev.event == "agent_started":
            self.current_step = {"agent": ev.agent, "goal": ev.message, "step_id": ev.step_id}
            for s in self.step_status:
                if s["step_id"] == ev.step_id:
                    s["status"] = "running"
        if ev.event == "agent_finished":
            for s in self.step_status:
                if s["step_id"] == ev.step_id:
                    s["status"] = "done" if ev.data.get("ok", True) else "failed"
                    s["summary"] = ev.message
            self.current_step = None
            self.llm_calls += int(ev.data.get("llm_calls", 0))
            self.llm_seconds += float(ev.data.get("llm_seconds", 0.0) or 0.0)
        if ev.event == "llm_call":
            pass


class RunRegistry:
    def __init__(self, keep: int = 50) -> None:
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()
        self._keep = keep
        self.subscribers: dict[str, list[Any]] = {}

    def create(self, session_id: str, text: str) -> RunState:
        rs = RunState(run_id=f"run-{uuid.uuid4().hex[:8]}", session_id=session_id, request_text=text)
        with self._lock:
            self._runs[rs.run_id] = rs
            if len(self._runs) > self._keep:
                oldest = sorted(self._runs.values(), key=lambda r: r.started)[: len(self._runs) - self._keep]
                for o in oldest:
                    if o.finished:
                        self._runs.pop(o.run_id, None)
        return rs

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def latest(self, session_id: str | None = None) -> RunState | None:
        runs = [r for r in self._runs.values() if session_id is None or r.session_id == session_id]
        return max(runs, key=lambda r: r.started) if runs else None

    def active(self) -> list[RunState]:
        return [r for r in self._runs.values() if not r.finished]

    def all(self) -> list[RunState]:
        return sorted(self._runs.values(), key=lambda r: -r.started)

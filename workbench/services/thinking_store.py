"""Thinking traces: the phase-by-phase reasoning of one run, collected and saved as JSON.

``ThinkingTraceCollector`` subscribes to the same ProgressEvents the CLI renders, so the
saved file and the terminal always agree. ``ThinkingStore`` writes one file per run to
``data/workbench/thinking/`` named ``{timestamp}_{audit_id}.json``, for post-mortem analysis
of how the agents arrived at an answer.

The file is a superset of what the terminal shows: it keeps every LLM call, the full plan
DAG, per-phase and per-agent timings, and the answer preview.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workbench.core.events import ProgressEvent


class PhaseTrace:
    """One pipeline phase and the agents that ran inside it."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.start_ts = time.time()
        self.end_ts: float | None = None
        self.agents: list[dict[str, Any]] = []
        self.note: str = ""

    def finish(self, note: str = "") -> None:
        self.end_ts = self.end_ts or time.time()
        self.note = note or self.note

    @property
    def duration_ms(self) -> int:
        return int(((self.end_ts or time.time()) - self.start_ts) * 1000)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "note": self.note,
            "llm_calls": sum(a.get("llm_calls", 0) for a in self.agents),
            "agents": self.agents,
        }


class ThinkingTraceCollector:
    """Accumulates the full thinking trace of one run from its ProgressEvents."""

    def __init__(self, query: str, *, llm_model: str = "", backend: str = "", profile: str = "") -> None:
        self.query = query
        self.llm_model = llm_model
        self.backend = backend
        self.profile = profile
        self.start_ts = time.time()
        self.phases: list[PhaseTrace] = []
        self.llm_calls_detail: list[dict[str, Any]] = []
        self.plan: dict | None = None
        self.events: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.replans: list[str] = []
        # filled from the FinalResponse by record_response()
        self.audit_id = ""
        self.response_id = ""
        self.status = ""
        self.task_type = ""
        self.entities: list[str] = []
        self.confidence: dict | None = None
        self.requires_human_review = False
        self.review_reason: str | None = None
        self.safety_flags: list[dict] = []
        self.llm_calls = 0
        self.evidence_count = 0
        self.answer_markdown = ""

    # ------------------------------------------------------------------ event sink
    def on_event(self, ev: ProgressEvent) -> None:
        """Record one progress event. Safe to subscribe alongside the terminal display."""
        self.events.append({"event": ev.event, "phase": ev.phase, "agent": ev.agent, "step_id": ev.step_id, "message": ev.message, "ts": round(ev.ts - self.start_ts, 3)})
        if ev.event == "phase_started":
            self._close_phase()
            self.phases.append(PhaseTrace(ev.phase or ev.message))
        elif ev.event == "phase_finished":
            if self.phases:
                self.phases[-1].finish(ev.message)
        elif ev.event == "agent_started":
            self._agent(ev.agent, ev.step_id)["goal"] = ev.message
            self._agent(ev.agent, ev.step_id)["approach"] = ev.thinking or ""
        elif ev.event == "agent_finished":
            data = ev.data or {}
            entry = self._agent(ev.agent, ev.step_id)
            entry.update({
                "thinking": ev.thinking or "",
                "decision": ev.decision or ev.message,
                "summary": ev.message,
                "model_used": ev.model,
                "ok": data.get("ok", True),
                "duration_ms": data.get("duration_ms", 0),
                "llm_calls": data.get("llm_calls", 0),
                "blocks": data.get("blocks", 0),
                "evidence": data.get("evidence", 0),
                "missing": data.get("missing", []),
            })
        elif ev.event == "llm_call":
            self.llm_calls_detail.append({"agent": ev.agent, "purpose": ev.message, "model": ev.model, "prompt": (ev.data or {}).get("prompt"), "at_ms": int((ev.ts - self.start_ts) * 1000)})
        elif ev.event == "plan_created":
            self.plan = {"rationale": ev.message, "steps": (ev.data or {}).get("steps", [])}
        elif ev.event == "replan":
            self.replans.append(ev.message)
        elif ev.event == "warning":
            self.warnings.append(ev.message)

    def _agent(self, agent: str | None, step_id: str | None) -> dict:
        """The entry for this agent/step in the current phase, created on first use."""
        if not self.phases:
            self.phases.append(PhaseTrace("0 Pre-flight"))
        phase = self.phases[-1]
        key = (agent or "agent", step_id or "")
        for entry in phase.agents:
            if (entry["name"], entry["step_id"]) == key and not entry["decision"]:
                return entry            # still open: agent_started created it, agent_finished fills it in
        entry = {"name": key[0], "step_id": key[1], "goal": "", "approach": "", "thinking": "", "decision": "", "model_used": None, "duration_ms": 0}
        phase.agents.append(entry)
        return entry

    def _close_phase(self) -> None:
        if self.phases:
            self.phases[-1].finish()

    # ------------------------------------------------------------------ final response
    def record_response(self, resp) -> ThinkingTraceCollector:
        """Copy the parts of the FinalResponse worth keeping next to the reasoning."""
        self._close_phase()
        self.audit_id = resp.audit_trail_id
        self.response_id = resp.response_id
        self.status = resp.status
        self.task_type = resp.task_type.value
        self.entities = list(resp.entities)
        self.confidence = {"score": resp.confidence.score, "level": resp.confidence.level, "basis": resp.confidence.basis, "uncertainties": resp.confidence.uncertainties}
        self.requires_human_review = resp.requires_human_review
        self.review_reason = resp.review_reason
        self.safety_flags = [{"severity": f.severity, "message": f.message, "requires_authorization": f.requires_authorization} for f in resp.safety_flags]
        self.llm_calls = resp.llm_calls
        self.evidence_count = len(resp.evidence)
        self.answer_markdown = resp.answer_markdown
        self.warnings = list(dict.fromkeys(self.warnings + list(resp.warnings)))
        if self.plan and resp.plan:
            final = {s.step_id: s for s in resp.plan.steps}
            for spec in self.plan["steps"]:
                step = final.get(spec.get("id"))
                spec["status"] = step.status.value if step else "replaced"
                spec["summary"] = step.note if step else None
        return self

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "audit_id": self.audit_id,
            "response_id": self.response_id,
            "task_type": self.task_type,
            "status": self.status,
            "entities": self.entities,
            "total_duration_ms": int((time.time() - self.start_ts) * 1000),
            "runtime": {"llm_model": self.llm_model, "backend": self.backend, "profile": self.profile, "llm_calls": self.llm_calls, "evidence": self.evidence_count},
            "phases": [p.to_dict() for p in self.phases],
            "plan": self.plan,
            "llm_calls_detail": self.llm_calls_detail,
            "replans": self.replans,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "governance": {"requires_human_review": self.requires_human_review, "review_reason": self.review_reason, "safety_flags": self.safety_flags},
            "answer_markdown": self.answer_markdown,
            "events": self.events,
        }


class ThinkingStore:
    """Writes one thinking trace per run to ``data/workbench/thinking/``."""

    def __init__(self, thinking_dir: Path) -> None:
        self.dir = Path(thinking_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, trace: ThinkingTraceCollector) -> Path:
        path = self.dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{trace.audit_id or 'unknown'}.json"
        path.write_text(json.dumps(trace.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return path

    def latest(self, limit: int = 10) -> list[Path]:
        return sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]

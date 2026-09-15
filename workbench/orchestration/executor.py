"""Task Executor (Phase 3/4): runs the plan's ready steps in order, sequentially.

Sequential on purpose: a 4 GB GPU serves one LLM request at a time and the deterministic
steps take milliseconds, so parallel branches would only add locking. Emits agent_started /
agent_finished events, stores each AgentResult in services.prior_results, and stops early
with a ReplanRequested signal when a required step asks for it.
"""
from __future__ import annotations

import logging
import time

from workbench.agents.base import AgentServices
from workbench.agents.registry import load_agent
from workbench.core.context import ContextPackage
from workbench.core.plan import Plan, StepStatus
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult
from workbench.orchestration import narration

logger = logging.getLogger(__name__)


class ReplanRequested(Exception):
    def __init__(self, step_id: str, reason: str) -> None:
        super().__init__(f"{step_id}: {reason}")
        self.step_id = step_id
        self.reason = reason


class Executor:
    def __init__(self, services: AgentServices) -> None:
        self.s = services
        self._cache: dict[str, object] = {}

    def agent(self, key: str):
        if key not in self._cache:
            self._cache[key] = load_agent(key)(self.s)
        return self._cache[key]

    def run(self, plan: Plan, request: StructuredRequest, context: ContextPackage, on_step=None) -> dict[str, AgentResult]:
        results = self.s.prior_results
        guard = 0
        while not plan.is_complete() and guard < 50:
            guard += 1
            ready = plan.ready_steps()
            if not ready:
                # dependencies failed: skip what cannot run
                for s in plan.steps:
                    if s.status == StepStatus.PENDING:
                        s.mark(StepStatus.SKIPPED, "dependency failed")
                break
            for step in ready:
                step.mark(StepStatus.RUNNING)
                self.s.events.emit("agent_started", phase="4 Execution", agent=step.agent, step_id=step.step_id, message=step.goal,
                                   thinking=narration.step_start(step), data={"mode": step.inputs.get("mode"), "depends_on": step.depends_on})
                t0 = time.time()
                llm_t0 = getattr(getattr(self.s.llm, "stats", None), "total_seconds", 0.0)
                try:
                    res = self.agent(step.agent).run(request, context, step)
                except Exception as exc:  # registry / construction failure
                    logger.exception("step %s crashed", step.step_id)
                    res = AgentResult(agent=step.agent, step_id=step.step_id, ok=False, summary=f"crashed: {exc}", needs_replan=not step.optional, replan_reason=str(exc))
                res.duration_ms = res.duration_ms or int((time.time() - t0) * 1000)
                results[step.step_id] = res
                step.mark(StepStatus.DONE if res.ok else StepStatus.FAILED, res.summary[:160] if res.summary else None)
                llm = self.s.llm
                self.s.events.emit("agent_finished", phase="4 Execution", agent=step.agent, step_id=step.step_id, message=res.summary,
                                   thinking=narration.step_finished(step, res),
                                   decision=res.summary or ("failed" if not res.ok else "no result"),
                                   model=(getattr(llm, "model", None) or getattr(llm, "name", None)) if res.llm_calls else None,
                                   data={"ok": res.ok, "duration_ms": res.duration_ms, "llm_calls": res.llm_calls, "blocks": len(res.blocks), "evidence": len(res.evidence), "missing": res.missing,
                                         "llm_seconds": round(getattr(getattr(llm, "stats", None), "total_seconds", 0.0) - llm_t0, 2)})
                if on_step:
                    on_step(step, res)
                if res.needs_replan and not step.optional:
                    raise ReplanRequested(step.step_id, res.replan_reason or "step requested replanning")
        return results

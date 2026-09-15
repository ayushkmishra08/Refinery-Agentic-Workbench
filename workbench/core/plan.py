"""Phase 3 contract: the Plan (a DAG of PlanSteps) the executor runs.

Plans are produced by templates per TaskType (deterministic) and, for PLANNING and
complex requests, refined by the LLM. A lookup is one or two steps; troubleshooting
is eight to ten dependent steps. Verification and governance are appended by the
orchestrator, never by the planner.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    step_id: str
    agent: str                                    # key in workbench.agents.registry
    goal: str                                     # human-readable task title (shown in the plan block)
    inputs: dict = Field(default_factory=dict)    # agent-specific parameters, e.g. {"mode": "prerequisites"}
    depends_on: list[str] = Field(default_factory=list)
    safety_sensitive: bool = False
    optional: bool = False                        # failure does not trigger replan
    status: StepStatus = StepStatus.PENDING
    note: str | None = None

    def mark(self, status: StepStatus, note: str | None = None) -> None:
        self.status = status
        if note:
            self.note = note


class Plan(BaseModel):
    plan_id: str
    goal: str
    steps: list[PlanStep]
    rationale: str = ""
    template: str = ""                            # which template produced it
    iteration: int = 0                            # replan counter
    llm_refined: bool = False

    def step(self, step_id: str) -> PlanStep:
        return next(s for s in self.steps if s.step_id == step_id)

    def ready_steps(self) -> list[PlanStep]:
        done = {s.step_id for s in self.steps if s.status in (StepStatus.DONE, StepStatus.SKIPPED)}
        return [
            s for s in self.steps
            if s.status == StepStatus.PENDING and all(d in done for d in s.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(s.status in (StepStatus.DONE, StepStatus.SKIPPED, StepStatus.FAILED) for s in self.steps)

    def failed_required(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == StepStatus.FAILED and not s.optional]

    def validate_dag(self) -> list[str]:
        """Return a list of problems (empty = valid)."""
        problems: list[str] = []
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            problems.append("duplicate step ids")
        known = set(ids)
        for s in self.steps:
            for d in s.depends_on:
                if d not in known:
                    problems.append(f"{s.step_id} depends on unknown step {d}")
        # cycle check (Kahn)
        indeg = {s.step_id: len(s.depends_on) for s in self.steps}
        children: dict[str, list[str]] = {s.step_id: [] for s in self.steps}
        for s in self.steps:
            for d in s.depends_on:
                if d in children:
                    children[d].append(s.step_id)
        queue = [i for i, n in indeg.items() if n == 0]
        seen = 0
        while queue:
            cur = queue.pop()
            seen += 1
            for c in children[cur]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    queue.append(c)
        if seen != len(self.steps):
            problems.append("plan contains a dependency cycle")
        return problems

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for s in self.steps:
            label = s.goal.replace('"', "'")
            lines.append(f'  {s.step_id}["{label}<br/><i>{s.agent}</i>"]')
        for s in self.steps:
            for d in s.depends_on:
                lines.append(f"  {d} --> {s.step_id}")
        return "\n".join(lines)

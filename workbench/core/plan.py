"""Phase 3: plan + DAG produced by the Planner and executed by the executor."""
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
    agent: str                                   # key from workbench.agents.registry
    goal: str
    inputs: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    safety_sensitive: bool = False
    status: StepStatus = StepStatus.PENDING


class Plan(BaseModel):
    plan_id: str
    steps: list[PlanStep]
    rationale: str = ""
    iteration: int = 0                           # replan counter

    def ready_steps(self) -> list[PlanStep]:
        done = {s.step_id for s in self.steps if s.status == StepStatus.DONE}
        return [
            s for s in self.steps
            if s.status == StepStatus.PENDING and all(d in done for d in s.depends_on)
        ]

"""TaskClassifierAgent (Phase 0/1).

UserRequest -> TaskType (+secondary), intent, confidence. Small fast model; routing-matrix columns as labels.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class TaskClassifierAgent(BaseAgent):
    name = "task_classifier"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

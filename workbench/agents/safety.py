"""SafetyAgent (cross-cutting).

Runs at Phase 0 gate, during planning, on every safety-sensitive execution path and at Phase 5. Emits SafetyFlags; can block.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class SafetyAgent(BaseAgent):
    name = "safety"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

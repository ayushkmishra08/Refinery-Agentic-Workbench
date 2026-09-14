"""GovernanceAgent (Phase 5).

Evidence validation, policy/permission gate, confidence manager, HITL decision, audit-trail finalisation -> FinalResponse.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class GovernanceAgent(BaseAgent):
    name = "governance"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

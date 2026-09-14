"""VerificationAgent (Phase 4).

Checks every statement in a result against its Evidence (grounding), units, context keys; failure -> needs_replan.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class VerificationAgent(BaseAgent):
    name = "verification"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

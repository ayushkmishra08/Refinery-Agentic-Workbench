"""ProcedureAgent (Phase 2/4).

Retrieve Procedure -> HAS_STEP -> NEXT chains, preconditions, standing instructions; output ordered steps with per-step evidence.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class ProcedureAgent(BaseAgent):
    name = "procedure"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

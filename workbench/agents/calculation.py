"""CalculationAgent (Phase 4).

Selects deterministic calculators, pulls spec claims with correct context key, never does arithmetic in the LLM.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class CalculationAgent(BaseAgent):
    name = "calculation"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

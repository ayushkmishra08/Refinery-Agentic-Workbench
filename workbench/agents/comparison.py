"""ComparisonAgent (Phase 4).

Side-by-side of entities / scenarios / revisions on matched context keys (BH vs PG, design vs enhanced).
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class ComparisonAgent(BaseAgent):
    name = "comparison"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

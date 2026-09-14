"""ReportAgent (Phase 4/5).

Report preparation / generation: structured markdown with citations, saved under data/workbench/reports/.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class ReportAgent(BaseAgent):
    name = "report"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

"""DiagnosticAgent (Phase 4).

Troubleshooting: symptom -> upset/safety chunks + graph neighbourhood (FEEDS/CONTROLLED_BY) -> ranked causes with evidence.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class DiagnosticAgent(BaseAgent):
    name = "diagnostic"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

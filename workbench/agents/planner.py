"""PlannerAgent (Phase 3).

StructuredRequest + ContextPackage -> Plan (DAG of PlanSteps over agents/tools). Plan validation and replan loop live in orchestration.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class PlannerAgent(BaseAgent):
    name = "planner"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

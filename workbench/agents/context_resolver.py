"""ContextResolverAgent (Phase 0/1).

Resolve entity/equipment tags (src.entity_identity canonical form), scenario / operating mode, ambiguity detection, evidence-requirement planning -> StructuredRequest.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class ContextResolverAgent(BaseAgent):
    name = "context_resolver"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

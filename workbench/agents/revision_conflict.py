"""RevisionConflictAgent (Phase 2/4).

Surfaces CONFLICTS_WITH / CORROBORATES pairs, applies revision & authority resolution, states both values when unresolved.
"""
from __future__ import annotations

from workbench.agents.base import BaseAgent
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult


class RevisionConflictAgent(BaseAgent):
    name = "revision_conflict"

    def run(self, request: StructuredRequest, context: ContextPackage, inputs: dict) -> AgentResult:
        raise NotImplementedError("build phase: see docs/PLAN.md")

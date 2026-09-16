"""Agent registry: plan step ``agent`` key -> class. Twelve named agents plus the four Phase 2 retrieval specialists."""
from __future__ import annotations

import importlib

AGENTS: dict[str, tuple[str, str]] = {
    # Phase 0/1
    "task_classifier": ("workbench.agents.task_classifier", "TaskClassifierAgent"),
    "context_resolver": ("workbench.agents.context_resolver", "ContextResolverAgent"),
    # Phase 2 specialist retrieval
    "lookup": ("workbench.agents.retrieval", "LookupAgent"),
    "graph": ("workbench.agents.retrieval", "GraphAgent"),
    "explanation": ("workbench.agents.retrieval", "ExplanationAgent"),
    "cross_document": ("workbench.agents.retrieval", "CrossDocumentAgent"),
    # Phase 3
    "planner": ("workbench.agents.planner", "PlannerAgent"),
    # Phase 4 execution
    "procedure": ("workbench.agents.procedure", "ProcedureAgent"),
    "diagnostic": ("workbench.agents.diagnostic", "DiagnosticAgent"),
    "calculation": ("workbench.agents.calculation", "CalculationAgent"),
    "comparison": ("workbench.agents.comparison", "ComparisonAgent"),
    "revision_conflict": ("workbench.agents.revision_conflict", "RevisionConflictAgent"),
    "report": ("workbench.agents.report", "ReportAgent"),
    "verification": ("workbench.agents.verification", "VerificationAgent"),
    # Phase 5 composition
    "answer_composer": ("workbench.agents.composer", "AnswerComposerAgent"),
    # cross-cutting / Phase 5
    "safety": ("workbench.agents.safety", "SafetyAgent"),
    "governance": ("workbench.agents.governance", "GovernanceAgent"),
}

TWELVE = ["task_classifier", "context_resolver", "planner", "procedure", "diagnostic", "calculation", "comparison", "safety", "revision_conflict", "report", "verification", "governance"]
# Phase 5 runs the composer between verification and governance for every answered request;
# it is not a plan step, so it is not part of TWELVE.


def load_agent(key: str):
    module, cls = AGENTS[key]
    return getattr(importlib.import_module(module), cls)


def describe_agents() -> list[dict]:
    out = []
    for key in AGENTS:
        cls = load_agent(key)
        out.append({"key": key, "class": cls.__name__, "phase": getattr(cls, "phase", ""), "description": getattr(cls, "description", "")})
    return out

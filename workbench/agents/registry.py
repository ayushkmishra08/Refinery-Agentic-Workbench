from __future__ import annotations

import importlib

AGENTS: dict[str, tuple[str, str]] = {
    "task_classifier": ("workbench.agents.task_classifier", "TaskClassifierAgent"),
    "context_resolver": ("workbench.agents.context_resolver", "ContextResolverAgent"),
    "planner": ("workbench.agents.planner", "PlannerAgent"),
    "procedure": ("workbench.agents.procedure", "ProcedureAgent"),
    "diagnostic": ("workbench.agents.diagnostic", "DiagnosticAgent"),
    "calculation": ("workbench.agents.calculation", "CalculationAgent"),
    "comparison": ("workbench.agents.comparison", "ComparisonAgent"),
    "safety": ("workbench.agents.safety", "SafetyAgent"),
    "revision_conflict": ("workbench.agents.revision_conflict", "RevisionConflictAgent"),
    "report": ("workbench.agents.report", "ReportAgent"),
    "verification": ("workbench.agents.verification", "VerificationAgent"),
    "governance": ("workbench.agents.governance", "GovernanceAgent"),
}


def load_agent(key: str):
    module, cls = AGENTS[key]
    return getattr(importlib.import_module(module), cls)

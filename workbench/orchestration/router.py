"""Routing Matrix (bottom of docs/architecture/agent_workflow.png).
Maps TaskType -> ordered primary agents. The Planner starts from this and may add steps."""
from workbench.core.request import TaskType

ROUTING_MATRIX: dict[TaskType, list[str]] = {
    TaskType.LOOKUP:          ["context_resolver"],                                   # retrieval only
    TaskType.MULTI_HOP:       ["context_resolver"],                                   # graph retrieval
    TaskType.PROCEDURE:       ["procedure", "safety"],
    TaskType.TROUBLESHOOTING: ["diagnostic", "procedure", "safety"],
    TaskType.LIMITS:          ["calculation", "revision_conflict"],
    TaskType.SAFETY:          ["safety"],
    TaskType.COMPARISON:      ["comparison", "revision_conflict"],
    TaskType.CONFLICT:        ["revision_conflict"],
    TaskType.PLANNING:        ["planner"],
    TaskType.REPORT:          ["report", "verification"],
    TaskType.AMBIGUOUS:       ["context_resolver"],                                   # ask back
    TaskType.CROSS_DOCUMENT:  ["comparison", "revision_conflict"],
}
ALWAYS_AFTER = ["verification", "governance"]

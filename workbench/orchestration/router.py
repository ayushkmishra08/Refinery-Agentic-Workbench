"""Routing matrix and plan templates.

The routing matrix (bottom of docs/architecture/agent_workflow.png) says which primary
agents serve each TaskType. The templates turn that into a concrete DAG per request, sized
to the task: a lookup is two steps, troubleshooting is nine. The Planner starts from the
template and may prune (missing entity -> no procedure step) or, for PLANNING/complex
requests, refine with the LLM. Verification and Governance are appended by the orchestrator.

Retrieval routes (cheapest that answers the question):
  claims   -> lookup / limits / comparison / conflict / provenance      (no text search)
  graph    -> multi_hop                                                (relations first)
  proc     -> procedure                                                (procedure index, no vectors)
  hybrid   -> troubleshooting / explanation / safety / cross_document  (BM25 + vectors + reranker)
"""
from __future__ import annotations

from workbench.core.plan import Plan, PlanStep
from workbench.core.request import StructuredRequest, TaskType

ROUTING_MATRIX: dict[TaskType, list[str]] = {
    TaskType.LOOKUP: ["lookup"],
    TaskType.MULTI_HOP: ["graph"],
    TaskType.PROCEDURE: ["procedure", "safety"],
    TaskType.TROUBLESHOOTING: ["diagnostic", "procedure", "safety"],
    TaskType.LIMITS: ["calculation", "revision_conflict", "safety"],
    TaskType.EXPLANATION: ["explanation"],
    TaskType.SAFETY: ["safety"],
    TaskType.COMPARISON: ["comparison", "revision_conflict"],
    TaskType.CONFLICT: ["revision_conflict"],
    TaskType.PROVENANCE: ["revision_conflict"],
    TaskType.PLANNING: ["planner"],
    TaskType.REPORT: ["report"],
    TaskType.CROSS_DOCUMENT: ["cross_document"],
    TaskType.AMBIGUOUS: ["context_resolver"],
}

RETRIEVAL_ROUTE: dict[TaskType, str] = {
    TaskType.LOOKUP: "claims", TaskType.LIMITS: "claims", TaskType.COMPARISON: "claims", TaskType.CONFLICT: "claims",
    TaskType.PROVENANCE: "claims", TaskType.MULTI_HOP: "graph", TaskType.PROCEDURE: "proc", TaskType.TROUBLESHOOTING: "hybrid",
    TaskType.EXPLANATION: "hybrid", TaskType.SAFETY: "hybrid", TaskType.CROSS_DOCUMENT: "hybrid", TaskType.REPORT: "hybrid",
    TaskType.PLANNING: "hybrid", TaskType.AMBIGUOUS: "none",
}

# Task types whose answers may lead to an operational action -> Safety agent reviews the result
SAFETY_REVIEWED = {TaskType.PROCEDURE, TaskType.TROUBLESHOOTING, TaskType.LIMITS, TaskType.SAFETY, TaskType.PLANNING}


def _s(step_id: str, agent: str, goal: str, deps: list[str] | None = None, safety: bool = False, optional: bool = False, **inputs) -> PlanStep:
    return PlanStep(step_id=step_id, agent=agent, goal=goal, depends_on=deps or [], safety_sensitive=safety, optional=optional, inputs=inputs)


def template_plan(req: StructuredRequest, plan_id: str) -> Plan:
    t = req.task_type
    has_entity = bool(req.entity_uids())
    has_value = bool(req.quantities)
    steps: list[PlanStep] = []
    goal = req.intent or req.original.text

    if t == TaskType.LOOKUP:
        steps = [_s("lookup", "lookup", "Retrieve the documented values for the requested property")]
        if has_entity:
            steps.append(_s("conflicts", "revision_conflict", "Check whether other sections give a different value", ["lookup"], optional=True, mode="check"))

    elif t == TaskType.MULTI_HOP:
        steps = [
            _s("graph", "graph", "Traverse the engineering graph (flow path / instruments / connected equipment)"),
            _s("support", "explanation", "Collect the manual text that describes the connections", ["graph"], optional=True, mode="support"),
        ]

    elif t == TaskType.PROCEDURE:
        steps = [
            _s("find", "procedure", "Find the documented procedure(s) for the equipment and action", mode="find"),
            _s("prereq", "procedure", "Extract prerequisites and pre-checks", ["find"], mode="prerequisites"),
            _s("steps", "procedure", "Retrieve the ordered steps with evidence", ["find"], mode="steps"),
            _s("refs", "cross_document", "Find referenced procedures and standing instructions", ["find"], optional=True, mode="for_procedure"),
            _s("safety", "safety", "Verify safety precautions and add documented warnings", ["prereq", "steps"], safety=True, mode="review_procedure"),
        ]

    elif t == TaskType.TROUBLESHOOTING:
        steps = [
            _s("identify", "lookup", "Identify the equipment and the process variable involved", mode="identify"),
            _s("normal", "calculation", "Retrieve the normal operating range for the variable", ["identify"], optional=True, mode="range"),
            _s("upset", "diagnostic", "Find the documented upset / deviation section", ["identify"], mode="find_upset"),
            _s("causes", "diagnostic", "Extract possible causes", ["upset"], mode="causes"),
            _s("checks", "diagnostic", "Extract diagnostic checks and their order", ["causes"], mode="checks"),
            _s("actions", "diagnostic", "Extract corrective actions", ["causes"], mode="actions"),
            _s("topology", "graph", "Add upstream/downstream equipment and instruments to consider", ["identify"], optional=True, mode="neighbors"),
            _s("procs", "procedure", "Find related procedures (changeover / restart)", ["causes"], optional=True, mode="find"),
            _s("safety", "safety", "Check safety restrictions on the recommended actions", ["actions", "checks"], safety=True, mode="review_actions"),
        ]

    elif t == TaskType.LIMITS:
        steps = [
            _s("claims", "lookup", "Retrieve normal / minimum / maximum / design values", mode="limits"),
            _s("revision", "revision_conflict", "Identify the applicable revision and any differing values", ["claims"], optional=True, mode="check"),
        ]
        if has_value:
            steps.append(_s("compare", "calculation", "Compare the given value with the documented envelope", ["claims"], mode="check_value"))
            steps.append(_s("safety", "safety", "Flag consequences of operating outside the envelope", ["compare"], safety=True, mode="review_limits"))
        else:
            steps.append(_s("consequences", "explanation", "Find documented consequences of deviation", ["claims"], optional=True, mode="consequences"))

    elif t == TaskType.EXPLANATION:
        steps = [
            _s("context", "explanation", "Retrieve the process description and operating principle", mode="why"),
            _s("topology", "graph", "Add the relevant process topology", optional=True, mode="neighbors"),
        ]

    elif t == TaskType.SAFETY:
        steps = [
            _s("safety", "safety", "Retrieve documented precautions, isolation, PPE, interlocks", safety=True, mode="answer"),
            _s("procs", "procedure", "Find related safety / isolation procedures", ["safety"], optional=True, mode="find", types=["safety", "isolation", "maintenance", "emergency"]),
            _s("refs", "cross_document", "Find standing instructions and referenced documents", ["safety"], optional=True, mode="for_safety"),
        ]

    elif t == TaskType.COMPARISON:
        steps = [
            _s("gather", "comparison", "Gather comparable values for each subject on matched context", mode="gather"),
            _s("diff", "comparison", "Align and highlight differences", ["gather"], mode="diff"),
            _s("revision", "revision_conflict", "Check revision / authority of differing values", ["gather"], optional=True, mode="check"),
        ]

    elif t in (TaskType.CONFLICT, TaskType.PROVENANCE):
        steps = [
            _s("claims", "revision_conflict", "Collect every documented value with its source", mode="collect"),
            _s("resolve", "revision_conflict", "Rank by revision and authority; explain which to trust", ["claims"], mode="resolve"),
        ]

    elif t == TaskType.PLANNING:
        steps = [
            _s("scope", "lookup", "Identify the equipment, instruments and scope of the plan", mode="identify"),
            _s("procs", "procedure", "Retrieve the relevant procedures and prerequisites", ["scope"], mode="find"),
            _s("limits", "lookup", "Retrieve the operating envelope and trip conditions", ["scope"], optional=True, mode="limits"),
            _s("causes", "diagnostic", "Retrieve documented causes and diagnostic checks", ["scope"], optional=True, mode="causes"),
            _s("safety", "safety", "Retrieve isolation and safety constraints", ["scope"], safety=True, mode="answer"),
            _s("gaps", "planner", "Identify missing evidence / data still required", ["procs", "limits", "causes", "safety"], mode="gaps"),
            _s("plan", "planner", "Assemble the work plan as an ordered task DAG", ["gaps"], mode="assemble"),
        ]

    elif t == TaskType.REPORT:
        steps = [
            _s("scope", "lookup", "Identify the report subject(s)", mode="identify"),
            _s("facts", "lookup", "Collect documented values", ["scope"], optional=True, mode="limits"),
            _s("topology", "graph", "Collect connected equipment and flow paths", ["scope"], optional=True, mode="neighbors"),
            _s("text", "explanation", "Collect descriptive text", ["scope"], mode="support"),
            _s("procs", "procedure", "Collect related procedures", ["scope"], optional=True, mode="find"),
            _s("conflicts", "revision_conflict", "Check for conflicting values", ["facts"], optional=True, mode="check"),
            _s("report", "report", "Assemble the report", ["facts", "topology", "text", "procs", "conflicts"], mode="assemble"),
        ]

    elif t == TaskType.CROSS_DOCUMENT:
        steps = [
            _s("sections", "cross_document", "Find documents and sections that cover the topic", mode="find"),
            _s("refs", "cross_document", "Follow cross references, referenced documents and standing instructions", ["sections"], mode="follow"),
        ]

    else:  # AMBIGUOUS
        steps = [_s("clarify", "context_resolver", "Ask for the missing context", mode="clarify")]

    return Plan(plan_id=plan_id, goal=goal, steps=steps, template=t.value)

"""Human-readable reasoning text ("thinking") for every stage of the pipeline.

Each function turns the structured output of one stage into a few plain sentences an
engineer can read: what the stage looked at, what it decided, and why. The orchestrator
and the executor attach the text to the ProgressEvent, so the CLI thinking display, the
SSE stream and the saved thinking trace all show the same words.

Nothing here touches the terminal, the filesystem or the knowledge layer: these are pure
functions over the contracts in ``workbench.core``. Keep them cheap — they run inline in
the request path.
"""
from __future__ import annotations

from workbench.core.context import ContextPackage
from workbench.core.plan import Plan, PlanStep, StepStatus
from workbench.core.request import ClassifierOutput, SafetyStatus, StructuredRequest, TaskType
from workbench.core.result import AgentResult, FinalResponse
from workbench.orchestration.router import RETRIEVAL_ROUTE, ROUTING_MATRIX, SAFETY_REVIEWED

ROUTE_REASON = {
    "claims": "documented values live on entity claims, so the claim index answers this without any text search",
    "graph":  "the question is about connections, so the engineering graph is traversed before any text is read",
    "proc":   "the procedure index holds ordered steps with pages, so no vector search is needed",
    "hybrid": "the answer is in prose, so BM25 + vectors + reranker search the manual text",
    "inventory": "the question is about what exists rather than about one item, so the entity index is listed instead of searched",
    "none":   "the request is ambiguous, so nothing is retrieved until it is clarified",
}
ACTION_MEANING = {
    "startup": "start-up / commissioning procedures",
    "restart": "start-up and emergency-restart procedures",
    "shutdown": "shutdown and emergency procedures",
    "changeover": "changeover procedures",
    "isolation": "isolation and maintenance procedures",
    "maintenance": "isolation and maintenance procedures",
    "inspection": "maintenance procedures",
    "bypass": "a request to defeat a protection — restricted",
}


def _fmt_list(items, limit: int = 4) -> str:
    items = [str(i) for i in items]
    head = ", ".join(items[:limit])
    return head + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


# ------------------------------------------------------------------ Phase 0/1


def classification(out: ClassifierOutput) -> str:
    """Why the classifier chose this task type."""
    lines: list[str] = []
    ranked = sorted(out.rule_scores.items(), key=lambda kv: -kv[1])
    if out.signals and not out.signals[0].startswith("no pattern"):
        lines.append(f'Matched phrase patterns: "{_fmt_list(out.signals, 3)}".')
    else:
        lines.append("No high-precision phrase pattern matched the wording.")
    if ranked:
        lines.append("Rule scores: " + ", ".join(f"{t}={s:.1f}" for t, s in ranked[:4]) + ".")
    if out.secondary:
        lines.append(f"Kept as secondary type(s): {_fmt_list([t.value for t in out.secondary])} — their agents are appended to the plan.")
    if out.method == "rules":
        lines.append(f"Rules were decisive ({out.confidence:.2f}), so the LLM was not called.")
    elif out.method == "rules+llm":
        lines.append(f"Rules were unsure, so the small LLM was asked for a second opinion; combined confidence {out.confidence:.2f}.")
    lines.append(f"Intent: {out.intent}.")
    return "\n".join(lines)


def resolution(req: StructuredRequest) -> str:
    """What the context resolver anchored the request to."""
    lines: list[str] = []
    if req.entities:
        for e in req.entities[:4]:
            tag = f" [{e.canonical_tag}]" if e.canonical_tag else ""
            lines.append(f'Resolved "{e.mention}" -> {e.name}{tag}, {e.entity_type or "entity"} (via {e.method}, {e.confidence:.2f}).')
            if e.candidates:
                lines.append(f"  Other candidates sharing that name: {_fmt_list(e.candidates, 3)}.")
    elif req.unresolved_mentions:
        lines.append(f"No equipment tag resolved; treating {_fmt_list(req.unresolved_mentions, 3)} as a claim subject instead.")
    elif req.subject_type:
        lines.append(f"No single item named; the request is about the {req.subject_type.lower()} class as a whole.")
    else:
        lines.append("No specific equipment named — the request is scoped to the unit as a whole.")
    if req.resolved_from_session:
        lines.append("The pronoun was bound to the equipment from the previous turn in this session.")
    if req.subject_type and req.entities:
        lines.append(f"Equipment class in the wording: {req.subject_type}.")
    if req.scope:
        lines.append(f"Scope of the question: {req.scope}.")
    if req.parameter:
        lines.append(f"Parameter of interest: {req.parameter}.")
    if req.quantities:
        lines.append("Values in the request: " + _fmt_list(q.raw for q in req.quantities) + ".")
    if req.symptom:
        lines.append(f'Symptom parsed: "{req.symptom.variable}" is {req.symptom.direction}.')
    if req.action:
        lines.append(f"Action detected: {req.action} -> look for {ACTION_MEANING.get(req.action, req.action + ' procedures')}.")
    if req.scenario:
        lines.append(f"Crude / operating case: {req.scenario}.")
    if req.operating_mode:
        lines.append(f"Operating mode: {req.operating_mode}.")
    if req.safety_status == SafetyStatus.RESTRICTED:
        lines.append(f"Safety gate: RESTRICTED ({_fmt_list(req.safety_signals, 2)}) — the answer may only describe the documented authorization path, and a human must review it.")
    elif req.safety_status == SafetyStatus.SENSITIVE:
        lines.append("Safety gate: sensitive — the Safety agent will review the answer before it is released.")
    else:
        lines.append("Safety gate: clear.")
    if req.ambiguities:
        lines.append(f"Missing context: {_fmt_list(req.ambiguities)} -> the request is reclassified as ambiguous and a clarification is asked instead of guessing.")
    return "\n".join(lines)


# ------------------------------------------------------------------ Phase 2


def retrieval(req: StructuredRequest, pkg: ContextPackage) -> str:
    """Which retrieval route ran and what it brought back."""
    lines = [f"Route '{pkg.route}' chosen for a {req.task_type.value} request — {ROUTE_REASON.get(pkg.route, 'default route')}."]
    found = [
        (len(pkg.claims), "claim(s)"), (len(pkg.relations), "relationship(s)"), (len(pkg.procedures), "procedure(s)"),
        (len(pkg.chunks), "text passage(s)"), (len(pkg.sections), "section(s)"), (len(pkg.conflicts), "conflict(s)"),
        (len(pkg.standing_instructions), "standing instruction(s)"), (len(pkg.glossary), "glossary term(s)"),
        (len(pkg.entities) if pkg.route == "inventory" else 0, "listed entity(ies)"),
    ]
    got = [f"{n} {label}" for n, label in found if n]
    lines.append("Retrieved " + (", ".join(got) if got else "nothing") + f" in {pkg.timing_ms} ms.")
    names = {e.entity_uid: e.name for e in pkg.entities}
    twins = [f"{names.get(uid, uid)} + {len(g)} tag twin(s)" for uid, g in pkg.entity_groups.items() if g]
    if twins:
        lines.append("Merged tag twins (the same equipment under a second tag): " + _fmt_list(twins, 2) + ".")
    if pkg.gaps:
        lines.append("Gap: " + "; ".join(pkg.gaps[:2]) + ".")
    return "\n".join(lines)


# ------------------------------------------------------------------ Phase 3


def planning(req: StructuredRequest, plan: Plan) -> str:
    """How the plan DAG was built."""
    primary = ROUTING_MATRIX.get(req.task_type, [])
    lines = [f"Routing matrix for {req.task_type.value} -> primary agents: {', '.join(primary) or 'none'}."]
    lines.append(f"Template '{plan.template}' expands to {len(plan.steps)} step(s): " + " -> ".join(s.step_id for s in plan.steps) + ".")
    if req.secondary_task_types:
        lines.append(f"Extended for the secondary type(s) {_fmt_list([t.value for t in req.secondary_task_types])}.")
    if not req.entity_uids():
        lines.append("No entity resolved, so entity-specific steps were pruned from the template.")
    if plan.llm_refined:
        lines.append("The LLM refined the step list for this planning request; the template steps are kept as the floor.")
    safety_steps = [s.step_id for s in plan.steps if s.safety_sensitive]
    if safety_steps:
        lines.append(f"Safety-sensitive step(s): {', '.join(safety_steps)} — they run after the content steps they review.")
    elif req.task_type in SAFETY_REVIEWED:
        lines.append("Safety review is expected for this task type but no safety step survived pruning.")
    return "\n".join(lines)


# ------------------------------------------------------------------ Phase 4


def step_start(step: PlanStep) -> str:
    """What an execution step is about to do. The goal itself is the event message."""
    mode = step.inputs.get("mode")
    lines = [step.goal + (f"  (mode: {mode})" if mode else "")]
    notes = []
    if step.depends_on:
        notes.append("consumes " + ", ".join(step.depends_on))
    if step.optional:
        notes.append(step.note or "optional: a failure does not trigger a replan")
    if step.safety_sensitive:
        notes.append("safety-sensitive: findings become safety flags on the answer")
    if notes:
        lines.append("; ".join(notes).capitalize() + ".")
    return "\n".join(lines)


def step_finished(step: PlanStep, res: AgentResult) -> str:
    """What an execution step found, from its own trace."""
    lines = [t for t in res.trace if not t.startswith("llm skipped")][-3:]
    skipped = [t for t in res.trace if t.startswith("llm skipped")]
    if skipped:
        lines.append(skipped[-1] + " — falling back to the deterministic path.")
    if res.evidence:
        pages = sorted({e.page for e in res.evidence if e.page})
        if pages:
            lines.append(f"Cited {len(res.evidence)} piece(s) of evidence from p.{_fmt_list(pages, 6)}.")
    if res.confidence.basis:
        lines.append(f"Step confidence {res.confidence.score:.2f} — {res.confidence.basis}.")
    if res.missing:
        lines.append("Could not satisfy: " + _fmt_list(res.missing, 3) + ".")
    if res.needs_replan:
        lines.append(f"Requesting a replan: {res.replan_reason}.")
    if not res.ok:
        lines.append("Step failed; dependent steps will be skipped.")
    return "\n".join(lines)


# ------------------------------------------------------------------ Phase 5


def verification(res: AgentResult) -> str:
    """How the verification agent checked the draft answer."""
    content = res.content or {}
    lines = [f"Checked every statement's numbers and wording against its cited evidence: {res.summary}."]
    if content.get("unknown_tags"):
        lines.append("Tags mentioned but absent from the knowledge base: " + _fmt_list(content["unknown_tags"], 5) + " — marked unverified.")
    for t in res.trace[-2:]:
        lines.append(t)
    return "\n".join(lines)


def composition(res) -> str:
    """How the retrieved material became the released prose."""
    content = res.content
    if not content.get("composed"):
        return "Composition produced nothing; the block rendering stands as the answer.\n" + "\n".join(res.trace[-2:])
    sections = content.get("brief_sections") or []
    lines = [f"Built a {content.get('brief_chars', 0)}-character brief from {len(sections)} section(s): " + _fmt_list(sections, 6) + "."]
    if content.get("source") == "model":
        lines.append("The model wrote the answer from that brief alone; every figure and tag in it was checked back against the brief.")
    else:
        lines.append("No model was available (or its attempt could not be grounded), so the answer was written from the same material by rule.")
    if content.get("grounding_failed"):
        lines.append("The model's wording was rejected for carrying a figure or tag the evidence does not contain.")
    if content.get("not_documented"):
        lines.append("Declared as not documented: " + _fmt_list(content["not_documented"], 3) + ".")
    for t in res.trace[-2:]:
        lines.append(t)
    return "\n".join(lines)


def governance(resp: FinalResponse) -> str:
    """How governance turned agent results into the released answer."""
    lines = [f"Aggregated confidence {resp.confidence.score:.2f} ({resp.confidence.level}) — {resp.confidence.basis}."]
    lines.append(f"Composed {len(resp.blocks)} render block(s) and relabelled {len(resp.evidence)} evidence item(s) with global citations.")
    if resp.safety_flags:
        by_sev = ", ".join(f"{sum(1 for f in resp.safety_flags if f.severity == s)} {s}" for s in ("danger", "warning", "caution", "info") if any(f.severity == s for f in resp.safety_flags))
        lines.append(f"Safety flags attached: {by_sev}.")
    lines.append(f"Human review: {'required — ' + (resp.review_reason or 'policy') if resp.requires_human_review else 'not required by policy'}.")
    if resp.warnings:
        lines.append("Carried warnings: " + _fmt_list(resp.warnings, 2) + ".")
    lines.append(f"Released with status '{resp.status}'.")
    return "\n".join(lines)


# ------------------------------------------------------------------ decisions (one-line outcomes)


def plan_shape(plan: Plan) -> str:
    done = sum(1 for s in plan.steps if s.status == StepStatus.DONE)
    return f"template '{plan.template}', {len(plan.steps)} steps" + (f", {done} done" if done else "")


def route_of(task_type: TaskType) -> str:
    return RETRIEVAL_ROUTE.get(task_type, "hybrid")

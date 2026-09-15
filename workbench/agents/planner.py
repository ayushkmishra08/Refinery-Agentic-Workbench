"""Planner Agent (Phase 3) — request -> Plan (DAG) sized to the task.

make_plan(): template per TaskType (router.template_plan) + extensions for secondary types
(compound requests) + pruning (no entity -> graph/procedure steps become optional) + validation.
For PLANNING requests with an LLM available, one structured call proposes extra tasks from a
fixed agent vocabulary; the merge is bounded (max 12 steps) and validated again.

As a plan step: mode ``gaps`` lists the evidence still missing; mode ``assemble`` renders the
work plan (PlanBlock) from what the other agents found.
"""
from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field

from workbench.agents.base import BaseAgent
from workbench.core.blocks import PlanBlock, PlanTask, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.plan import Plan, PlanStep, StepStatus
from workbench.core.request import StructuredRequest, TaskType
from workbench.core.result import AgentResult
from workbench.orchestration.router import template_plan

AGENT_VOCAB = {"lookup", "graph", "explanation", "cross_document", "procedure", "diagnostic", "calculation", "comparison", "safety", "revision_conflict", "report"}
NEEDS_ENTITY = {"graph", "procedure", "calculation"}


class LLMPlan(BaseModel):
    tasks: list[dict] = Field(default_factory=list, description="[{title, agent, depends_on_titles}] agent must be one of the allowed agents")
    rationale: str = ""


def extend_for_secondary(plan: Plan, req: StructuredRequest) -> None:
    """Compound requests: add steps for secondary task types that the template does not cover."""
    have = {s.agent for s in plan.steps}
    last = plan.steps[-1].step_id if plan.steps else None
    anchor = [s.step_id for s in plan.steps if s.agent in ("lookup", "procedure", "diagnostic")][:1] or ([last] if last else [])
    for t in req.secondary_task_types:
        if t == TaskType.PROCEDURE and "procedure" not in have:
            plan.steps.append(PlanStep(step_id="procs2", agent="procedure", goal="Retrieve the related procedure and its prerequisites", depends_on=anchor, inputs={"mode": "find"}, optional=True))
            plan.steps.append(PlanStep(step_id="steps2", agent="procedure", goal="Retrieve the procedure steps", depends_on=["procs2"], inputs={"mode": "steps"}, optional=True))
            have.add("procedure")
        elif t == TaskType.SAFETY and "safety" not in have:
            plan.steps.append(PlanStep(step_id="safety2", agent="safety", goal="Retrieve documented safety constraints", depends_on=anchor, inputs={"mode": "answer"}, safety_sensitive=True, optional=True))
            have.add("safety")
        elif t in (TaskType.CROSS_DOCUMENT, TaskType.PROVENANCE) and "cross_document" not in have:
            plan.steps.append(PlanStep(step_id="refs2", agent="cross_document", goal="Collect supporting documents and references", depends_on=anchor, inputs={"mode": "follow"}, optional=True))
            have.add("cross_document")
        elif t == TaskType.LIMITS and "calculation" not in have and req.entity_uids():
            plan.steps.append(PlanStep(step_id="limits2", agent="lookup", goal="Retrieve the operating envelope", depends_on=anchor, inputs={"mode": "limits"}, optional=True))
            have.add("calculation")
        elif t == TaskType.CONFLICT and "revision_conflict" not in have:
            plan.steps.append(PlanStep(step_id="conf2", agent="revision_conflict", goal="Check for conflicting documented values", depends_on=anchor, inputs={"mode": "check"}, optional=True))
            have.add("revision_conflict")
        elif t == TaskType.TROUBLESHOOTING and "diagnostic" not in have:
            plan.steps.append(PlanStep(step_id="upset2", agent="diagnostic", goal="Find the documented upset section", depends_on=anchor, inputs={"mode": "find_upset"}, optional=True))
            plan.steps.append(PlanStep(step_id="causes2", agent="diagnostic", goal="Extract documented causes and checks", depends_on=["upset2"], inputs={"mode": "causes"}, optional=True))
            have.add("diagnostic")
    if req.task_type != TaskType.PLANNING and TaskType.PLANNING in req.secondary_task_types and "planner" not in have:
        deps = [s.step_id for s in plan.steps if s.agent != "planner"]
        plan.steps.append(PlanStep(step_id="assemble2", agent="planner", goal="Assemble the findings into an ordered plan", depends_on=deps[-6:], inputs={"mode": "assemble"}, optional=True))
    if req.task_type != TaskType.REPORT and (TaskType.REPORT in req.secondary_task_types or req.original.options.get("want_report")) and "report" not in have:
        deps = [s.step_id for s in plan.steps if s.agent != "report"]
        plan.steps.append(PlanStep(step_id="report2", agent="report", goal="Assemble the report", depends_on=deps[-8:], inputs={"mode": "assemble"}, optional=True))


def prune(plan: Plan, req: StructuredRequest) -> None:
    has_entity = bool(req.entity_uids())
    for s in plan.steps:
        if s.agent in NEEDS_ENTITY and not has_entity and not req.unresolved_mentions:
            s.optional = True
            s.note = "no entity resolved; step is best-effort"


class PlannerAgent(BaseAgent):
    name = "planner"
    phase = "3 Planning"
    description = "Builds the execution DAG from templates, extends it for compound requests, refines planning requests with the LLM."

    # ------------------------------------------------------------------ plan construction (called by the orchestrator)
    def make_plan(self, req: StructuredRequest, result: AgentResult, iteration: int = 0) -> Plan:
        plan = template_plan(req, plan_id=f"plan-{uuid.uuid4().hex[:8]}")
        plan.iteration = iteration
        if req.task_type != TaskType.AMBIGUOUS:
            extend_for_secondary(plan, req)
        prune(plan, req)
        if req.task_type == TaskType.PLANNING and self.llm.available():
            self._refine(plan, req, result)
        problems = plan.validate_dag()
        if problems:
            result.trace.append(f"plan problems fixed: {problems}")
            plan.steps = [s for s in plan.steps if all(d in {x.step_id for x in plan.steps} for d in s.depends_on)]
        if len(plan.steps) > 12:
            keep = plan.steps[:12]
            ids = {s.step_id for s in keep}
            for s in keep:
                s.depends_on = [d for d in s.depends_on if d in ids]
            plan.steps = keep
        plan.rationale = f"template '{plan.template}' ({len(plan.steps)} steps)" + ("; extended for " + ", ".join(t.value for t in req.secondary_task_types) if req.secondary_task_types else "") + ("; LLM-refined" if plan.llm_refined else "")
        result.content["plan"] = plan.model_dump(mode="json")
        result.summary = plan.rationale
        return plan

    def _refine(self, plan: Plan, req: StructuredRequest, result: AgentResult) -> None:
        out = self.llm_json("planner", LLMPlan, result, max_tokens=400, purpose="plan_refine", request=req.original.text,
                            entities=", ".join(e.name for e in req.entities if e.name) or "none", agents=", ".join(sorted(AGENT_VOCAB)),
                            current_plan="\n".join(f"- {s.step_id}: {s.goal} [{s.agent}]" for s in plan.steps))
        if not out or not out.tasks:
            return
        existing_goals = {s.goal.lower() for s in plan.steps}
        existing_agents = {s.agent for s in plan.steps}
        text = req.original.text.lower()
        allowed = AGENT_VOCAB - {a for a, words in (('comparison', ('compar', 'versus', ' vs ')), ('report', ('report',)), ('revision_conflict', ('conflict', 'revision', 'trust'))) if not any(w in text for w in words)}
        title_to_id = {s.goal.lower(): s.step_id for s in plan.steps}
        added = 0
        for i, t in enumerate(out.tasks[:6]):
            title = str(t.get("title", "")).strip()
            agent = str(t.get("agent", "")).strip()
            if not title or agent not in allowed or agent in existing_agents or title.lower() in existing_goals:
                continue                                  # the template already covers this agent; duplicates only add noise
            existing_agents.add(agent)
            if any(w in title.lower() for w in ("verify", "govern", "final answer")):
                continue
            deps = [title_to_id[d.lower()] for d in (t.get("depends_on_titles") or []) if isinstance(d, str) and d.lower() in title_to_id] or ["scope"]
            sid = f"llm{i}"
            mode = {"procedure": "find", "diagnostic": "causes", "lookup": "limits", "safety": "answer", "cross_document": "follow", "explanation": "support", "graph": "neighbors", "calculation": "range", "revision_conflict": "check", "comparison": "gather", "report": "assemble"}[agent]
            plan.steps.insert(len(plan.steps) - 2, PlanStep(step_id=sid, agent=agent, goal=title[:90], depends_on=deps, inputs={"mode": mode}, optional=True, safety_sensitive=agent == "safety"))
            title_to_id[title.lower()] = sid
            added += 1
        if added:
            # the assemble step must run after the added tasks
            for s in plan.steps:
                if s.agent == "planner" and s.inputs.get("mode") in ("gaps", "assemble"):
                    s.depends_on = list(dict.fromkeys(s.depends_on + [x.step_id for x in plan.steps if x.step_id.startswith("llm")]))
            plan.llm_refined = True
            plan.rationale += f"; {added} task(s) proposed by the LLM"

    # ------------------------------------------------------------------ plan steps
    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "assemble")
        prior = [r for r in self.s.prior_results.values() if r.agent not in ("planner", "verification", "governance")]
        if mode == "gaps":
            missing = list(dict.fromkeys(m for r in prior for m in r.missing))
            reqs = list(request.evidence_requirements)
            satisfied = {r.agent for r in prior if r.ok and r.blocks and not r.missing}
            rows = [[m, "not documented"] for m in missing]
            for req_item in reqs:
                key = req_item.lower()
                if "procedure" in key and "procedure" not in satisfied:
                    rows.append([req_item, "no procedure matched"])
                if "envelope" in key and "lookup" not in satisfied and "calculation" not in satisfied:
                    rows.append([req_item, "no limits found"])
            if request.quantities == [] and request.task_type == TaskType.PLANNING and re.search(r"troubleshoot|investigat|trip", request.original.text, re.IGNORECASE):
                rows.append(["Current field readings (pressures, flows, temperatures) for the affected equipment", "operator input required"])
                rows.append(["Trip / alarm history from the DCS", "operator input required"])
            result.content["gaps"] = [r[0] for r in rows]
            if rows:
                result.blocks.append(TableBlock(id="gaps", title="Information still required", columns=["Item", "Why"], rows=rows))
            else:
                result.blocks.append(self.callout("All evidence requirements were met from the documents.", "success"))
            result.summary = f"{len(rows)} gap(s)"
            result.confidence = self.confidence(0.7, "gap analysis against evidence requirements")
            return
        # assemble: turn findings into an ordered work plan
        tasks: list[PlanTask] = []

        def add(title: str, agent: str, deps: list[str], summary: str | None = None, safety: bool = False) -> str:
            tid = f"t{len(tasks) + 1}"
            tasks.append(PlanTask(id=tid, title=title, agent=agent, depends_on=deps, status="pending", summary=summary, safety_sensitive=safety))
            return tid

        ents = ", ".join(e.name.split(" (")[0] for e in request.entities if e.name) or "the equipment"
        t_scope = add(f"Confirm scope: {ents}" + (f" — {request.action}" if request.action else ""), "lookup", [], summary=(self.s.result_of("lookup", "scope") or AgentResult(agent="lookup")).summary or None)
        deps_after_scope = [t_scope]
        safety_res = self.s.result_of("safety")
        t_safety = add("Obtain permits and apply documented safety constraints", "safety", [t_scope], summary=safety_res.summary if safety_res else "no documented constraints found", safety=True)
        proc_res = self.s.result_of("procedure")
        proc_tasks: list[str] = []
        if proc_res and proc_res.content.get("procedure_ids"):
            for pid in proc_res.content["procedure_ids"][:4]:
                p = self.knowledge.get_procedure(pid)
                if p:
                    proc_tasks.append(add(f"Execute documented procedure: {p.title[:70]} (p.{p.page_start}, {len(p.steps)} steps)", "procedure", [t_safety], summary=f"{p.procedure_type}", safety=p.procedure_type in ("isolation", "emergency", "changeover", "shutdown")))
        else:
            proc_tasks.append(add("Locate the governing procedure (none matched automatically)", "cross_document", [t_safety], summary="gap"))
        lim = self.s.result_of("lookup", "limits") or self.s.result_of("calculation")
        t_limits = add("Check the operating envelope and trip conditions before / after the work", "calculation", [t_scope], summary=lim.summary if lim else "no limits documented")
        diag = self.s.result_of("diagnostic")
        t_diag = None
        if diag:
            t_diag = add("Work through documented causes and diagnostic checks", "diagnostic", [t_limits], summary=diag.summary)
        gaps = self.s.result_of("planner", "gaps")
        t_gaps = add("Collect the information still required", "planner", [t_scope], summary=gaps.summary if gaps else None)
        final_deps = proc_tasks + [t_limits, t_gaps] + ([t_diag] if t_diag else [])
        add("Record results, close permits, report to shift in-charge", "report", final_deps)
        lines = ["graph TD"] + [f'  {t.id}["{t.title.replace(chr(34), chr(39))}<br/><i>{t.agent}</i>"]' for t in tasks] + [f"  {d} --> {t.id}" for t in tasks for d in t.depends_on]
        result.blocks.append(PlanBlock(id="workplan", title="Proposed work plan (from documented procedures and constraints)", goal=request.intent or request.original.text, tasks=tasks, mermaid="\n".join(lines)))
        result.content["workplan"] = [t.model_dump(mode="json") for t in tasks]
        result.summary = f"work plan with {len(tasks)} task(s)"
        result.confidence = self.confidence(0.7 if proc_res and proc_res.content.get("procedure_ids") else 0.45, "tasks derived from documented procedures, limits and safety items", ["the plan orders documented items; it is a proposal for review, not an authorization"])

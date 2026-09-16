"""Governance & Audit Agent (Phase 5) — policy gate, confidence, HITL, citations, final composition.

Takes every AgentResult, the plan and the verification score and produces the FinalResponse:
- policy: restricted requests never contain instructions; high-risk operational answers are
  flagged for human review;
- confidence: weighted mean of agent confidences x verification score, minus penalties for
  missing evidence;
- citations: evidence is de-duplicated and labelled [1], [2], ... in order of first use;
  every block's citation keys are rewritten to labels;
- composition: lead answer, primary blocks in plan order (or the report's order), safety,
  conflicts, plan (when the run had more than three steps), confidence, evidence, audit.
"""
from __future__ import annotations

import time
import uuid

from workbench.agents.base import BaseAgent, blocks_to_markdown
from workbench.core.blocks import (
    AuditBlock,
    AuditPhase,
    Block,
    CalloutBlock,
    ConfidenceBlock,
    PlanBlock,
    PlanTask,
    SafetyBlock,
    SafetyFlagItem,
)
from workbench.core.evidence import Confidence, SafetyFlag
from workbench.core.plan import Plan
from workbench.core.request import SafetyStatus, StructuredRequest, TaskType
from workbench.core.result import AgentResult, FinalResponse
from workbench.services.evidence_store import EvidenceStore

HIGH_RISK_PROCEDURE_TYPES = {"emergency", "isolation", "changeover", "shutdown"}


def relabel_block(block: Block, store: EvidenceStore) -> None:
    """Rewrite evidence keys to [n] labels inside a block (in place)."""
    block.citations = store.relabel_citations(block.citations)
    for attr in ("items", "steps", "prerequisites", "flags", "edges", "claims", "markers"):
        items = getattr(block, attr, None)
        if not items:
            continue
        for it in items:
            c = getattr(it, "citation", None)
            if c and not str(c).startswith("["):
                lab = store.label(c)
                it.citation = lab or None
    cells = getattr(block, "cells", None)
    if cells:
        for row in cells:
            for cell in row:
                if cell.citation and not cell.citation.startswith("["):
                    cell.citation = store.label(cell.citation)
    rc = getattr(block, "row_citations", None)
    if rc:
        block.row_citations = [store.relabel_citations(r) for r in rc]


class GovernanceAgent(BaseAgent):
    name = "governance"
    phase = "5 Governance / Response"
    description = "Applies policy, aggregates confidence, decides human review, labels evidence and composes the final response."

    def compose(self, request: StructuredRequest, plan: Plan | None, results: dict[str, AgentResult], audit_id: str, phases: list[AuditPhase],
                backend: str, started: float, warnings: list[str] | None = None) -> FinalResponse:
        store = EvidenceStore()
        ordered = [results[s.step_id] for s in (plan.steps if plan else []) if s.step_id in results] or list(results.values())
        verification = next((r for r in results.values() if r.agent == "verification"), None)
        vscore = float(verification.content.get("overall_score", 1.0)) if verification else 1.0
        report = next((r for r in ordered if r.agent == "report" and r.content.get("replace_blocks")), None)
        clarification = next((r for r in ordered if any(b.type == "clarification" for b in r.blocks)), None)
        restricted = request.safety_status == SafetyStatus.RESTRICTED

        # ---- evidence registry -----------------------------------------------------------
        for r in ordered:
            store.add_all(r.evidence)

        # ---- safety flags ---------------------------------------------------------------------
        flags: list[SafetyFlag] = []
        seen_msgs: set[str] = set()
        for r in ordered:
            for f in r.safety_flags:
                if f.message not in seen_msgs:
                    seen_msgs.add(f.message)
                    flags.append(f)

        # ---- confidence ---------------------------------------------------------------------
        weights = []
        for r in ordered:
            if r.agent in ("verification", "governance", "report"):
                continue
            step = plan.step(r.step_id) if plan and any(s.step_id == r.step_id for s in plan.steps) else None
            w = 0.5 if (step and step.optional) else 1.0
            if not r.ok:
                w *= 0.3
            weights.append((w, r.confidence.score))
        base = sum(w * s for w, s in weights) / sum(w for w, _ in weights) if weights else 0.3
        missing = [m for r in ordered for m in r.missing]
        penalty = min(0.25, 0.05 * len(missing))
        score = max(0.0, min(1.0, base * (0.6 + 0.4 * vscore) - penalty))
        uncertainties = list(dict.fromkeys([u for r in ordered for u in r.confidence.uncertainties if u] + [f"not documented: {m}" for m in missing[:4]]))
        if verification and verification.content.get("unknown_tags"):
            uncertainties.append("unverified tags: " + ", ".join(verification.content["unknown_tags"][:5]))
        conf = Confidence(score=round(score, 2), basis=f"agents {base:.2f} × verification {vscore:.2f}" + (f" − {penalty:.2f} missing-evidence penalty" if penalty else ""), uncertainties=uncertainties[:6])

        # ---- human review decision --------------------------------------------------------------
        review, reason = self._review_decision(request, ordered, flags)

        # ---- block composition ---------------------------------------------------------------
        blocks: list[Block] = []
        status = "answered"
        if clarification:
            if restricted:
                blocks.append(CalloutBlock(id="lead", level="danger", title="Restricted request", markdown="This asks to bypass or defeat a protective function. No bypass steps will be provided under any wording; once the equipment is named, only the documented authorization route and standing instructions are shown."))
            blocks.extend(clarification.blocks)
            status = "clarification"
        elif restricted:
            status = "restricted"
            safety_res = next((r for r in ordered if r.agent == "safety"), None)
            blocks.append(CalloutBlock(id="lead", level="danger", title="Restricted request", markdown="This request asks to bypass or defeat a protective function. No bypass steps are provided. The documented authorization route and applicable standing instructions are shown; a responsible person must review."))
            if safety_res:
                blocks.extend(safety_res.blocks)
        else:
            lead = self._lead(request, ordered)
            if lead:
                blocks.append(lead)
            if report:
                blocks.extend(report.blocks)
            else:
                safety_first = request.task_type == TaskType.SAFETY
                if safety_first:
                    for r in ordered:
                        if r.agent == "safety":
                            blocks.extend(r.blocks)
                for r in ordered:
                    if r.agent in ("verification", "governance", "safety"):
                        continue
                    blocks.extend(r.blocks)
                if not safety_first:
                    for r in ordered:
                        if r.agent == "safety":
                            blocks.extend(r.blocks)
        if flags and not any(b.type == "safety" for b in blocks):
            blocks.append(SafetyBlock(id="safety-summary", title="Safety flags", flags=[SafetyFlagItem(severity=f.severity, message=f.message, citation=(f.evidence[0].key() if f.evidence else None), requires_authorization=f.requires_authorization) for f in flags[:10]]))
        if verification and verification.blocks:
            blocks.extend(verification.blocks)
        if plan and len(plan.steps) > 3 and status == "answered":
            blocks.append(self._plan_block(plan, results))
        if review:
            blocks.append(CalloutBlock(id="hitl", level="warning", title="Human review required", markdown=reason or "Operational recommendation: confirm with the shift in-charge before acting."))
        blocks.append(ConfidenceBlock(id="confidence", score=conf.score, level=conf.level, basis=conf.basis, uncertainties=conf.uncertainties))
        # ---- relabel citations, append evidence --------------------------------------------------
        for b in blocks:
            relabel_block(b, store)
        if store.labelled():
            blocks.append(store.to_block())
        elapsed = int((time.time() - started) * 1000)
        llm_calls = sum(r.llm_calls for r in results.values())
        blocks.append(AuditBlock(id="audit", audit_id=audit_id, phases=phases, llm_calls=llm_calls, backend=backend))
        answer_md = blocks_to_markdown([b for b in blocks if b.type not in ("audit", "evidence")])
        resp = FinalResponse(
            response_id=f"resp-{uuid.uuid4().hex[:10]}", session_id=request.original.session_id, task_type=request.task_type,
            secondary_task_types=request.secondary_task_types, status=status, answer_markdown=answer_md, blocks=blocks,
            evidence=store.labelled(), confidence=conf, safety_flags=flags, requires_human_review=review, review_reason=reason,
            plan=plan, audit_trail_id=audit_id, entities=[e.name or e.mention for e in request.entities], timing_ms=elapsed, llm_calls=llm_calls,
            backend=backend, warnings=(warnings or []) + [f"not documented: {m}" for m in missing[:6]],
        )
        if status == "answered" and conf.score < self.cfg.governance.min_confidence_to_answer:
            resp.status = "needs_review"
            resp.warnings.append("confidence below the answer threshold; treat as a lead, not an answer")
        return resp

    # ------------------------------------------------------------------ helpers
    def _review_decision(self, request: StructuredRequest, ordered: list[AgentResult], flags: list[SafetyFlag]) -> tuple[bool, str | None]:
        if not self.cfg.governance.require_hitl_for_high_risk:
            return False, None
        if request.safety_status == SafetyStatus.RESTRICTED:
            return True, "Bypassing a protection requires documented authorization by the responsible person."
        for r in ordered:
            if r.agent == "calculation" and r.content.get("verdict") == "outside_design":
                return True, "The given value lies outside the documented design envelope."
            if r.agent == "procedure":
                for pid in r.content.get("procedure_ids", []):
                    p = self.knowledge.get_procedure(pid)
                    if p and p.procedure_type in HIGH_RISK_PROCEDURE_TYPES:
                        return True, f"The answer contains a {p.procedure_type} procedure; execution needs shift in-charge authorization and a permit."
        if any(f.severity == "danger" for f in flags):
            return True, "A documented DANGER-level precaution applies."
        if request.task_type == TaskType.PLANNING:
            return True, "Work plans are proposals; a responsible engineer must approve before execution."
        return False, None

    @staticmethod
    def _lead(request: StructuredRequest, ordered: list[AgentResult]) -> Block | None:
        gauge = next((b for r in ordered for b in r.blocks if b.type == "limit_gauge" and b.value is not None), None)
        if gauge:
            return CalloutBlock(id="lead", level={"within_normal": "success", "within_design": "warning", "outside_design": "danger", "unknown": "info"}[gauge.verdict],
                                markdown=f"**{gauge.value:g} {gauge.unit}** on {gauge.entity} ({gauge.parameter}): **{gauge.verdict.replace('_', ' ')}**. {gauge.message}")
        primary = next((r for r in ordered if r.agent not in ("verification", "governance", "safety", "revision_conflict", "cross_document") and r.blocks), None)
        if not primary:
            primary = next((r for r in ordered if r.agent in ("revision_conflict", "cross_document", "safety") and r.blocks and r.ok), None)
        if not primary:
            missing = [m for r in ordered for m in r.missing]
            return CalloutBlock(id="lead", level="warning", title="No documented answer", markdown="The documents do not contain what was asked" + (": " + "; ".join(missing[:3]) if missing else "") + ". UNKNOWN is reported rather than a guess.")
        if primary.blocks[0].type == "callout" and not primary.blocks[0].title:
            return None                     # the agent already opened with its own one-line lead
        ents = ", ".join(e.name for e in request.entities if e.name)
        summary = primary.summary or ""
        if request.task_type in (TaskType.LOOKUP, TaskType.LIMITS) and primary.agent in ("lookup", "calculation"):
            kpi = next((b for b in primary.blocks if b.type == "kpi"), None)
            if kpi and kpi.items:
                first = kpi.items[0]
                text = f"**{first.label}** = **{first.value} {first.unit or ''}**" + (f" ({first.qualifier})" if first.qualifier else "") + (f" for {ents}." if ents else ".")
                if len(kpi.items) > 1:
                    text += f" {len(kpi.items) - 1} further documented value(s) below."
                return CalloutBlock(id="lead", level="success", markdown=text)
        return CalloutBlock(id="lead", level="info", markdown=f"{request.intent or 'Result'}" + (f" — {ents}" if ents else "") + (f": {summary}" if summary else ""))

    @staticmethod
    def _plan_block(plan: Plan, results: dict[str, AgentResult]) -> PlanBlock:
        tasks = []
        for s in plan.steps:
            r = results.get(s.step_id)
            tasks.append(PlanTask(id=s.step_id, title=s.goal, agent=s.agent, depends_on=s.depends_on, status=s.status.value, summary=(r.summary if r else s.note), safety_sensitive=s.safety_sensitive))
        return PlanBlock(id="executed-plan", title=f"How this answer was produced ({len(plan.steps)} steps" + (f", replanned {plan.iteration}×" if plan.iteration else "") + ")", goal=plan.goal, tasks=tasks, mermaid=plan.to_mermaid())

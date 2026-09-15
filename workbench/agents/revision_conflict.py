"""Revision / Conflict Agent (Phase 2/4) — every documented value, its source, and which to trust.

Modes: check (after a lookup: surface conflicts if any), collect (provenance list), resolve
(rank by revision/authority and explain). Claims are grouped by context key: two values with
different roles / locations / scenarios are *different facts*, not a conflict; two values with
the same context key from different places are a potential conflict and are ranked, never
averaged or deleted.
"""
from __future__ import annotations

from collections import defaultdict

from workbench.agents.base import BaseAgent
from workbench.agents.retrieval import context_label, predicates_for
from workbench.core.blocks import ConflictBlock, ConflictClaim, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.knowledge import ClaimRecord
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest, TaskType
from workbench.core.result import AgentResult
from workbench.services.revision_resolver import explain_preference, rank_claims


class RevisionConflictAgent(BaseAgent):
    name = "revision_conflict"
    phase = "2/4 Revision / Conflict / Provenance"
    description = "Lists every documented value with its source and resolves differing values by revision and authority."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "check")
        claims = self._claims(request, context)
        if not claims:
            if mode != "check":
                result.missing.append("no documented values to compare")
                result.blocks.append(self.callout("No documented values were found for the subject, so provenance and conflicts cannot be shown.", "warning"))
            result.summary = "no values"
            result.confidence = self.confidence(0.3 if mode == "check" else 0.2, "no claims")
            return
        docs = {d.document_id: d for d in self.knowledge.documents()}
        groups: dict[str, list[ClaimRecord]] = defaultdict(list)
        for c in claims:
            groups[(c.subject_uid or c.subject.lower(), c.context_key)].append(c)
        if mode == "check":
            return self._check(groups, docs, result)
        if mode == "collect" or request.task_type == TaskType.PROVENANCE:
            self._collect(request, claims, docs, result)
        if mode == "resolve" or request.task_type == TaskType.CONFLICT:
            self._resolve(request, groups, docs, result)

    # ------------------------------------------------------------------ claims
    def _claims(self, request: StructuredRequest, context: ContextPackage) -> list[ClaimRecord]:
        rows: list[ClaimRecord] = []
        prior = self.s.result_of("lookup") or self.s.result_of("calculation")
        if prior and prior.content.get("claims"):
            rows = [ClaimRecord.model_validate(c) for c in prior.content["claims"]]
        if not rows:
            for uid in request.entity_uids():
                rows += context.claims_for(uid) or self.knowledge.entity_claims(uid)
        if not rows and request.unresolved_mentions:
            for m in request.unresolved_mentions[:2]:
                rows += self.knowledge.search_claims(subject=m, limit=60)
        preds = predicates_for(request.parameter)
        if preds:
            f = [c for c in rows if c.predicate in preds]
            rows = f or rows
        text = request.original.text.lower()
        loc = "discharge" if "discharge" in text else "suction" if "suction" in text else None
        if loc:
            f = [c for c in rows if (c.location or "").lower() == loc]
            rows = f or rows
        seen = set()
        out = []
        for c in rows:
            if c.claim_id not in seen:
                seen.add(c.claim_id)
                out.append(c)
        return out

    def _conflict_block(self, rows: list[ClaimRecord], docs, result: AgentResult, block_id: str) -> ConflictBlock:
        ranked = rank_claims(rows, docs)
        values = {round(c.numeric_value, 4) if c.numeric_value is not None else c.value for c in rows}
        distinct_places = {(c.document_id, c.page, c.chunk_id) for c in rows}
        if len(values) <= 1:
            status = "corroborated" if len(distinct_places) > 1 else "resolved"
        else:
            status = "resolved" if explain_preference(ranked[0], ranked[1], docs).find("neither can be preferred") < 0 else "unresolved"
        claims_out = []
        for c in ranked:
            key = self.cite_claim(result, c)
            claims_out.append(ConflictClaim(value=c.value, unit=c.unit, document_id=c.document_id, revision=c.revision, page=c.page, source=c.source,
                                            context=context_label(c) or None, citation=key, temporal_status=c.temporal_status))
            self.statement(result, f"{c.subject} {c.predicate.replace('_', ' ')} = {c.value} {c.unit or ''} ({c.document_id} p.{c.page}, {c.source})".strip(), [key])
        resolution = ""
        if status == "corroborated":
            resolution = f"The same value is stated in {len(distinct_places)} places; the sources corroborate each other."
        elif status in ("resolved", "unresolved") and len(values) > 1:
            resolution = explain_preference(ranked[0], ranked[1], docs)
            if status == "unresolved":
                resolution += " Both values are reported; a human decision (or a later revision) is needed."
        return ConflictBlock(id=block_id, title=f"{ranked[0].subject} — {ranked[0].predicate.replace('_', ' ')}" + (f" [{context_label(ranked[0])}]" if context_label(ranked[0]) else ""),
                             subject=ranked[0].subject, parameter=ranked[0].predicate.replace("_", " "), claims=claims_out, status=status,
                             preferred_index=0 if len(values) > 1 else None, resolution=resolution, citations=[c.citation for c in claims_out if c.citation])

    # ------------------------------------------------------------------ modes
    def _check(self, groups, docs, result) -> None:
        conflicts = [(k, rows) for k, rows in groups.items() if len(rows) > 1 and len({round(c.numeric_value, 4) for c in rows if c.numeric_value is not None}) > 1]
        if not conflicts:
            result.summary = "no conflicting values"
            result.confidence = self.confidence(0.8, "all values for the same context agree")
            return
        for i, (k, rows) in enumerate(conflicts[:4]):
            result.blocks.append(self._conflict_block(rows, docs, result, f"conflict-{i}"))
        result.content["conflicts"] = len(conflicts)
        result.summary = f"{len(conflicts)} potential conflict(s) surfaced"
        result.confidence = self.confidence(0.7, "same context key, different values, different places")

    def _collect(self, request, claims, docs, result) -> None:
        ranked = rank_claims(claims, docs)
        rows, cits = [], []
        for c in ranked[:40]:
            key = self.cite_claim(result, c)
            rows.append([c.subject, c.predicate.replace("_", " "), c.value, c.unit or "", context_label(c) or "—", c.document_id, c.revision or "?", f"p.{c.page}" if c.page else "", c.source, c.temporal_status or "current"])
            cits.append([key])
            self.statement(result, f"{c.subject} {c.predicate.replace('_', ' ')} = {c.value} {c.unit or ''} from {c.document_id} rev {c.revision or '?'} p.{c.page} ({c.source})".strip(), [key])
        result.blocks.append(TableBlock(id="provenance", title="Every documented value and its source", columns=["Subject", "Parameter", "Value", "Unit", "Context", "Document", "Revision", "Page", "Source type", "Status"], rows=rows, row_citations=cits))
        by_source = defaultdict(int)
        for c in ranked:
            by_source[c.source] += 1
        result.blocks.append(self.text_block("Source types: " + ", ".join(f"{k} ({v})" for k, v in by_source.items()) + ". *table* = equipment/specification table, *rule* = specification block or prose sentence, *llm* = model-extracted (none here unless stated)."))
        result.content["claims"] = [c.model_dump(mode="json") for c in ranked]
        result.summary = f"{len(ranked)} documented value(s) with provenance"
        result.confidence = self.confidence(0.85, "every value carries document, page and source")

    def _resolve(self, request, groups, docs, result) -> None:
        differing = [(k, rows) for k, rows in groups.items() if len(rows) > 1 and len({round(c.numeric_value, 4) if c.numeric_value is not None else c.value for c in rows}) > 1]
        same_param_diff_ctx: dict[str, list[ClaimRecord]] = defaultdict(list)
        for (subj, ctx), rows in groups.items():
            same_param_diff_ctx[(subj, rows[0].predicate)].extend(rows)
        multi_ctx = {k: v for k, v in same_param_diff_ctx.items() if len({c.context_key for c in v}) > 1}
        if differing:
            for i, (k, rows) in enumerate(differing[:5]):
                result.blocks.append(self._conflict_block(rows, docs, result, f"resolve-{i}"))
            result.content["resolved"] = len(differing)
        if multi_ctx:
            rows_t, cits = [], []
            for (subj, pred), cl in list(multi_ctx.items())[:6]:
                for c in rank_claims(cl, docs)[:6]:
                    key = self.cite_claim(result, c)
                    rows_t.append([c.subject, pred.replace("_", " "), c.value, c.unit or "", context_label(c) or "—", f"p.{c.page}"])
                    cits.append([key])
            result.blocks.append(TableBlock(id="different-context", title="Values that differ because their context differs (not conflicts)", columns=["Subject", "Parameter", "Value", "Unit", "Context", "Page"], rows=rows_t, row_citations=cits))
            result.blocks.append(self.callout("A different **role** (normal vs minimum vs design), **location** (suction vs discharge), **operating case** (Basrah vs Bombay High) or **pressure basis** (absolute vs gauge) makes these different facts about the same equipment, not contradictions.", "info"))
        if not differing and not multi_ctx:
            result.blocks.append(self.callout("All documented values for this parameter agree; there is nothing to resolve.", "success"))
        result.summary = f"{len(differing)} conflicting group(s) resolved, {len(multi_ctx)} parameter(s) with several contexts"
        result.confidence = self.confidence(0.8, "ranking by temporal status, document authority, revision and source type")

"""Procedure Agent (Phase 2/4) — documented procedures with prerequisites, ordered steps and evidence.

Modes: find (select the matching procedures), prerequisites (pre-checks / conditions),
steps (ordered steps with per-step evidence, warnings and equipment mentions).
Never composes steps; every step is a ProcedureStep from the knowledge layer with its page.
"""
from __future__ import annotations

import re

from workbench.agents.base import BaseAgent
from workbench.core.blocks import StepItem, StepsBlock, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.knowledge import ProcedureRecord
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult
from workbench.services.evidence_store import evidence_from_step

ACTION_TYPES = {"startup": ["startup", "commissioning"], "restart": ["startup", "emergency", "commissioning"], "shutdown": ["shutdown", "emergency"], "changeover": ["changeover"],
                "isolation": ["isolation", "maintenance", "shutdown"], "maintenance": ["isolation", "maintenance"], "steam_out": ["normal_operation"], "water_wash": ["maintenance", "normal_operation"],
                "sampling": ["sampling"], "inspection": ["maintenance", "isolation"]}
PREREQ_RE = re.compile(r"\b(ensure|make sure|confirm|check|verify|before|prior to|must be|should be|pre-?start|clearance|permit|line ?up|lined up|available|ready|in service|isolated|blinded|drained|purged|depressuri[sz]ed)\b", re.IGNORECASE)
WARN_RE = re.compile(r"\b(caution|warning|danger|do not|don't|never|must not|shall not|immediately|slowly|gradually|carefully|avoid|hazard|hot|toxic|h2s|fire|explosion|relief|psv|trip|alarm|isolat)\w*", re.IGNORECASE)
TAG_RE = re.compile(r"\b\d{1,3}-[A-Z]{1,4}-\d{1,5}(?:[A-Z](?:/[A-Z])*)?\b")


class ProcedureAgent(BaseAgent):
    name = "procedure"
    phase = "2/4 Procedure / Operations"
    description = "Finds documented procedures and returns prerequisites and ordered steps with evidence."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "find")
        self._step_types = step.inputs.get("types")
        procs = self._selected(request, context, result)
        if not procs:
            result.missing.append("no documented procedure for the requested equipment/action")
            entity = request.primary_entity
            what = f"**{entity.name}**" if entity and entity.name else "this equipment"
            result.blocks.append(self.callout(f"No documented procedure matches {what}" + (f" and the action *{request.action}*" if request.action else "") + ". The manual may describe it in prose; see the supporting passages if present. No steps are invented.", "warning"))
            result.summary = "no procedure found"
            result.confidence = self.confidence(0.15, "no procedure matched")
            result.needs_replan = not step.optional and mode == "find"
            result.replan_reason = "procedure not found" if result.needs_replan else None
            return
        result.content["procedure_ids"] = [p.procedure_id for p in procs]
        if mode == "find":
            rows = [[p.title[:80], p.procedure_type, f"{p.page_start}" + (f"–{p.page_end}" if p.page_end and p.page_end != p.page_start else ""), len(p.steps), p.section_path.split(" > ")[-2] if " > " in p.section_path else p.section_path] for p in procs]
            result.blocks.append(TableBlock(id="procedures", title="Matching documented procedures", columns=["Procedure", "Type", "Pages", "Steps", "Section"], rows=rows))
            for p in procs:
                self.statement(result, f"Procedure '{p.title[:60]}' ({p.procedure_type}) is documented on p.{p.page_start}", [self.cite(result, evidence_from_step(p, p.steps[0]))] if p.steps else [])
            result.summary = f"{len(procs)} procedure(s): " + "; ".join(p.title[:40] for p in procs[:2])
            result.confidence = self.confidence(min(0.9, 0.5 + 0.1 * procs[0].score), f"best match score {procs[0].score}")
        elif mode == "prerequisites":
            self._prerequisites(procs, result)
        else:
            self._steps(procs, request, result)

    # ------------------------------------------------------------------ selection
    def _selected(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> list[ProcedureRecord]:
        prior = self.s.result_of("procedure")
        if prior and prior.content.get("procedure_ids"):
            procs = [p for pid in prior.content["procedure_ids"] if (p := self.knowledge.get_procedure(pid))]
            if procs:
                return procs
        uids = request.entity_uids()
        wanted_types = ACTION_TYPES.get(request.action or "", None)
        step_types = getattr(self, "_step_types", None)
        if step_types:
            wanted_types = step_types
        query = " ".join(x for x in [request.action or "", request.original.text] if x)
        cands: list[ProcedureRecord] = list(context.procedures)
        if not cands:
            for uid in uids or [None]:
                cands += self.knowledge.procedures(entity_uid=uid, query=query, limit=8)
        if wanted_types:
            typed = [p for p in cands if p.procedure_type in wanted_types]
            if not typed:
                for uid in uids or [None]:
                    for wt in wanted_types:
                        typed += self.knowledge.procedures(entity_uid=uid, query=query, procedure_type=wt, limit=4)
                typed = [p for p in typed if p.procedure_type in wanted_types]
            if typed and step_types:
                cands = typed                                # a typed step never falls back to other procedure types
            elif typed:
                cands = typed + [p for p in cands if p not in typed]
            elif step_types:
                cands = []                                   # a typed request must not fall back to unrelated procedures
        if uids:
            ent = self.knowledge.get_entity(uids[0])
            if ent is not None:
                aliases = {a.lower() for a in ent.aliases if len(a) >= 4} | {ent.name.split(' (')[0].lower()}
                if ent.canonical_tag:
                    aliases.add(ent.canonical_tag.lower())
                    try:
                        from knowledge_layer.entity_identity import parse_tag
                        ident = parse_tag(ent.canonical_tag)
                        aliases.update(c.lower() for c in (ident.children if ident else []))
                    except Exception:
                        pass
                twins = set(context.entity_groups.get(uids[0], []))

                def mentions(p: ProcedureRecord) -> bool:
                    if set(p.applies_to) & ({uids[0]} | twins):
                        return True
                    head = f"{p.title} {p.section_path}".lower()
                    if any(a in head for a in aliases):
                        return True
                    body = ' '.join(s.text for s in p.steps).lower()
                    tags = {t.lower() for s in p.steps for t in s.tags}
                    return bool(tags & aliases) or any(a in body for a in aliases if ' ' in a or '-' in a)

                about = [p for p in cands if mentions(p)]
                cands = about                                # a procedure that never mentions the equipment is not its procedure
        # de-dup, keep score order, drop near-empty
        seen = set()
        out = []
        for p in sorted(cands, key=lambda p: -p.score):
            if p.procedure_id in seen or len(p.steps) < 2:
                continue
            seen.add(p.procedure_id)
            out.append(p)
        # keep the best and any others that are clearly relevant (>= 60% of the best score)
        if out:
            best = out[0].score or 1.0
            out = [p for p in out if p.score >= 0.6 * best][:3]
        result.trace.append(f"procedure candidates: {[(p.title[:30], p.score) for p in out]}")
        return out

    # ------------------------------------------------------------------ renderers
    def _step_item(self, p: ProcedureRecord, s, result: AgentResult, is_prereq: bool = False) -> StepItem:
        key = self.cite(result, evidence_from_step(p, s))
        warnings = sorted({m.group(0).lower() for m in WARN_RE.finditer(s.text)})[:4]
        mentions = list(dict.fromkeys(s.tags or TAG_RE.findall(s.text)))
        self.statement(result, s.text, [key])
        return StepItem(sequence=s.sequence, text=s.text.strip(), page=s.page, citation=key, is_prerequisite=is_prereq, warnings=warnings, mentions=mentions)

    def _prerequisites(self, procs: list[ProcedureRecord], result: AgentResult) -> None:
        items: list[StepItem] = []
        for p in procs:
            # a dedicated pre-check procedure in the same chapter beats keyword picking
            pre = [q for q in self.knowledge.procedures(query="pre startup checks clearance", procedure_type=p.procedure_type, limit=6)
                   if q.chapter_number == p.chapter_number and q.procedure_id != p.procedure_id and re.search(r"pre.?start|check|clearance|before", q.title, re.IGNORECASE)]
            for q in pre[:1]:
                items += [self._step_item(q, s, result, True) for s in q.steps[:12]]
            for s in p.steps:
                if PREREQ_RE.search(s.text) and (s.sequence <= max(3, len(p.steps) // 3) or re.search(r"\bbefore\b|\bprior to\b|\bensure\b|\bconfirm\b", s.text, re.IGNORECASE)):
                    items.append(self._step_item(p, s, result, True))
        seen = set()
        uniq = []
        for it in items:
            if it.text not in seen:
                seen.add(it.text)
                uniq.append(it)
        result.content["prerequisites"] = [i.text for i in uniq]
        if not uniq:
            result.blocks.append(self.callout("The matched procedure does not state explicit prerequisites; the first steps themselves are the entry conditions.", "info"))
            result.summary = "no explicit prerequisites"
            result.confidence = self.confidence(0.5, "no prerequisite sentences found")
            return
        p0 = procs[0]
        result.blocks.append(StepsBlock(id="prereqs", title=f"Prerequisites and pre-checks — {p0.title[:70]}", procedure_id=p0.procedure_id, procedure_type=p0.procedure_type, document_id=p0.document_id,
                                        section_path=p0.section_path, page_start=p0.page_start, page_end=p0.page_end, prerequisites=[], steps=uniq[:20], citations=[i.citation for i in uniq if i.citation]))
        result.summary = f"{len(uniq)} prerequisite(s)"
        result.confidence = self.confidence(0.75, "prerequisite sentences quoted from the procedure with page numbers")

    def _steps(self, procs: list[ProcedureRecord], request: StructuredRequest, result: AgentResult) -> None:
        for p in procs[:2]:
            steps = [self._step_item(p, s, result) for s in p.steps]
            result.blocks.append(StepsBlock(id=f"steps-{p.procedure_id[-8:]}", title=p.title[:90], procedure_id=p.procedure_id, procedure_type=p.procedure_type, document_id=p.document_id,
                                            section_path=p.section_path, page_start=p.page_start, page_end=p.page_end, steps=steps, citations=[s.citation for s in steps if s.citation]))
        result.content["steps"] = {p.procedure_id: [s.text for s in p.steps] for p in procs[:2]}
        n = sum(len(p.steps) for p in procs[:2])
        result.summary = f"{n} step(s) from {min(2, len(procs))} procedure(s)"
        result.confidence = self.confidence(min(0.92, 0.6 + 0.08 * procs[0].score), "verbatim ordered steps with page references", [f"{len(procs) - 2} further candidate procedure(s) not shown"] if len(procs) > 2 else [])

"""Comparison / Configuration Agent (Phase 4) — side-by-side on matched context.

Subjects can be: two or more entities; two operating scenarios (Basrah vs Bombay High, BH vs
PG mode); normal vs temporary operation; two procedures (startup vs shutdown); revisions (the
manual has one revision -> stated explicitly). Values are aligned by predicate + context so
that a suction pressure is never compared with a discharge pressure.
"""
from __future__ import annotations

import re
from collections import defaultdict

from workbench.agents.base import BaseAgent
from workbench.agents.retrieval import context_label, predicates_for
from workbench.core.blocks import ComparisonBlock, ComparisonCell, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.knowledge import ClaimRecord
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult
from workbench.services.evidence_store import evidence_from_step

SCENARIO_WORDS = [("basrah", "Basrah"), ("bombay high", "Bombay High"), ("bh mode", "BH mode"), ("pg mode", "PG mode"), ("kuwait", "Kuwait"), ("kirkuk", "Kirkuk"), ("design", "design"), ("enhanced", "enhanced")]


class ComparisonAgent(BaseAgent):
    name = "comparison"
    phase = "4 Execution (Configuration / Comparison)"
    description = "Aligns documented values or procedures for two or more subjects on matched context."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "gather")
        text = request.original.text.lower()
        scenarios = [name for word, name in SCENARIO_WORDS if word in text]
        if re.search(r"\bprocedure", text) and re.search(r"start", text) and re.search(r"shut", text):
            return self._procedures(request, context, result)
        if re.search(r"\brevision\b", text):
            return self._revisions(request, context, result)
        if re.search(r"\bnormal\b.*\btemporary\b|\btemporary\b.*\bnormal\b", text):
            return self._modes(request, context, result)
        if len(scenarios) >= 2 or (len(scenarios) == 1 and len(request.entity_uids()) <= 1 and re.search(r"what changes|compare", text)):
            return self._scenarios(request, scenarios, result)
        if len(request.entity_uids()) >= 2 or (request.primary_entity and request.primary_entity.canonical_tag):
            return self._entities(request, context, result, mode)
        result.missing.append("comparison subjects not identified")
        result.blocks.append(self.callout("The subjects to compare could not be identified (two equipment items, two crude cases, two procedures or two modes).", "warning"))
        result.summary = "no subjects"
        result.confidence = self.confidence(0.2, "no subjects")

    # ------------------------------------------------------------------ entities
    def _entities(self, request, context, result, mode) -> None:
        uids = request.entity_uids()
        subjects: list[tuple[str, list[str]]] = []
        if len(uids) >= 2:
            for uid in uids:
                e = self.knowledge.get_entity(uid)
                subjects.append((e.name if e else uid, [uid] + context.entity_groups.get(uid, [])))
        else:
            # "the two crude charge pumps" -> A/B trains or tag twins
            e = self.knowledge.get_entity(uids[0])
            from knowledge_layer.entity_identity import parse_tag

            ident = parse_tag(e.canonical_tag) if e and e.canonical_tag else None
            children = ident.children if ident else []
            twins = context.entity_groups.get(uids[0], [])
            if children:
                for child in children:
                    recs = self.knowledge.resolve_entity(child, limit=2)
                    subjects.append((child, [r.entity_uid for r in recs] or [uids[0]]))
            for t in twins:
                te = self.knowledge.get_entity(t)
                if te:
                    subjects.append((te.name, [t]))
            if not subjects:
                subjects.append((e.name if e else uids[0], [uids[0]]))
        preds = predicates_for(request.parameter)
        table: dict[str, dict[str, ClaimRecord]] = defaultdict(dict)       # attribute -> subject -> claim
        for name, group in subjects:
            for uid in group:
                for c in self.knowledge.entity_claims(uid):
                    if preds and c.predicate not in preds:
                        continue
                    if c.numeric_value is None and len(c.value) > 30:
                        continue
                    attr = c.predicate.replace("_", " ") + (f" [{context_label(c)}]" if context_label(c) else "")
                    if name not in table[attr]:
                        table[attr][name] = c
        if not table:
            result.missing.append("no comparable values")
            result.blocks.append(self.callout("No documented values were found for the subjects, so nothing can be compared.", "warning"))
            result.summary = "no values"
            result.confidence = self.confidence(0.2, "no claims")
            return
        names = [n for n, _ in subjects]
        attrs = sorted(table, key=lambda a: (-len(table[a]), a))
        cells, diffs, cits = [], [], []
        for a in attrs[:30]:
            row = []
            vals = set()
            for n in names:
                c = table[a].get(n)
                if c:
                    key = self.cite_claim(result, c)
                    cits.append(key)
                    row.append(ComparisonCell(value=c.value, unit=c.unit, citation=key, note=f"p.{c.page}" if c.page else None))
                    vals.add(c.value)
                    self.statement(result, f"{n} {a} = {c.value} {c.unit or ''}".strip(), [key])
                else:
                    row.append(ComparisonCell(value=None, note="not documented"))
            cells.append(row)
            if len(vals) > 1 and len([x for x in row if x.value]) > 1:
                diffs.append(f"{a}: " + " vs ".join(f"{n} {table[a][n].value} {table[a][n].unit or ''}".strip() for n in names if n in table[a]))
        if len(subjects) == 1:
            diffs.append("Only one subject could be resolved; the table lists its documented values by context.")
        result.blocks.append(ComparisonBlock(id="comparison", title="Comparison on matched context", subjects=names, attributes=attrs[:30], cells=cells, differences=diffs, citations=cits))
        result.content["differences"] = diffs
        result.summary = f"{len(attrs[:30])} attribute(s), {len(diffs)} difference(s) across {len(names)} subject(s)"
        result.confidence = self.confidence(0.8 if len(names) > 1 else 0.5, "values aligned by predicate and context key")

    # ------------------------------------------------------------------ scenarios (crude cases)
    def _scenarios(self, request, scenarios, result) -> None:
        if len(scenarios) == 1:
            scenarios = scenarios + ["design"]
        table: dict[str, dict[str, ClaimRecord]] = defaultdict(dict)
        for sc in scenarios:
            for c in self.knowledge.search_claims(scenario=sc, limit=400):
                subj = c.subject
                if request.entity_uids():
                    if c.subject_uid not in request.entity_uids():
                        continue
                attr = f"{subj} — {c.predicate.replace('_', ' ')}" + (f" [{c.qualifier[:30]}]" if c.qualifier and sc.lower() not in c.qualifier.lower() else "")
                if sc not in table[attr]:
                    table[attr][sc] = c
        attrs = [a for a in table if len(table[a]) >= 2] or list(table)[:25]
        if not attrs:
            result.missing.append("no scenario-specific values")
            result.blocks.append(self.callout(f"No documented values are tagged with the cases {', '.join(scenarios)}.", "warning"))
            result.summary = "no scenario values"
            result.confidence = self.confidence(0.2, "no claims")
            return
        cells, diffs, cits = [], [], []
        for a in attrs[:40]:
            row = []
            vals = []
            for sc in scenarios:
                c = table[a].get(sc)
                if c:
                    key = self.cite_claim(result, c)
                    cits.append(key)
                    row.append(ComparisonCell(value=c.value, unit=c.unit, citation=key, note=f"p.{c.page}" if c.page else None))
                    vals.append(c.value)
                    self.statement(result, f"{a} ({sc}) = {c.value} {c.unit or ''}".strip(), [key])
                else:
                    row.append(ComparisonCell(value=None, note="not documented"))
            cells.append(row)
            if len(set(vals)) > 1:
                diffs.append(f"{a}: " + " vs ".join(f"{sc} {table[a][sc].value}" for sc in scenarios if sc in table[a]))
        result.blocks.append(ComparisonBlock(id="scenario-comparison", title=f"Documented operating cases: {' vs '.join(scenarios)}", subjects=scenarios, attributes=attrs[:40], cells=cells, differences=diffs[:25], citations=cits))
        result.content["differences"] = diffs
        result.summary = f"{len(attrs[:40])} attribute(s) across {', '.join(scenarios)}; {len(diffs)} differ"
        result.confidence = self.confidence(0.8, "table values tagged with the crude case / operating mode")

    # ------------------------------------------------------------------ procedures
    def _procedures(self, request, context, result) -> None:
        uid = request.entity_uids()[0] if request.entity_uids() else None
        a = self.knowledge.procedures(entity_uid=uid, query=request.original.text + " start up", procedure_type="startup", limit=3)
        b = self.knowledge.procedures(entity_uid=uid, query=request.original.text + " shut down", procedure_type="shutdown", limit=3)
        if not a or not b:
            result.missing.append("one of the procedures is not documented")
            result.blocks.append(self.callout("Both a startup and a shutdown procedure are needed for the comparison; at least one is not documented for this equipment.", "warning"))
            result.summary = "procedure pair incomplete"
            result.confidence = self.confidence(0.25, "missing procedure")
            return
        pa, pb = a[0], b[0]
        n = max(len(pa.steps), len(pb.steps))
        rows, cits = [], []
        for i in range(n):
            sa = pa.steps[i] if i < len(pa.steps) else None
            sb = pb.steps[i] if i < len(pb.steps) else None
            ka = self.cite(result, evidence_from_step(pa, sa)) if sa else None
            kb = self.cite(result, evidence_from_step(pb, sb)) if sb else None
            rows.append([i + 1, sa.text[:200] if sa else "—", sb.text[:200] if sb else "—"])
            cits.append([k for k in (ka, kb) if k])
        result.blocks.append(TableBlock(id="proc-compare", title=f"Startup vs shutdown — {pa.title[:50]} / {pb.title[:50]}", columns=["#", f"Startup (p.{pa.page_start})", f"Shutdown (p.{pb.page_start})"], rows=rows, row_citations=cits))
        result.content["procedure_ids"] = [pa.procedure_id, pb.procedure_id]
        result.summary = f"{len(pa.steps)} startup step(s) vs {len(pb.steps)} shutdown step(s)"
        result.confidence = self.confidence(0.7, "verbatim steps of both procedures")

    # ------------------------------------------------------------------ revisions / modes
    def _revisions(self, request, context, result) -> None:
        docs = self.knowledge.documents()
        revs = sorted({(d.document_id, d.revision or "?") for d in docs})
        rows = [[d, r] for d, r in revs]
        result.blocks.append(TableBlock(id="revisions", title="Revisions available in the knowledge base", columns=["Document", "Revision"], rows=rows))
        claims = []
        for uid in request.entity_uids():
            claims += [c for c in self.knowledge.entity_claims(uid) if (c.temporal_status or "current") != "current" or c.revision]
        if len(revs) <= 1:
            result.blocks.append(self.callout("Only one revision of each document is loaded, so a revision-to-revision diff is not possible. Values marked design/historical inside the document are listed below where present.", "info"))
        if claims:
            rows2, cits = [], []
            for c in claims[:20]:
                key = self.cite_claim(result, c)
                rows2.append([c.subject, c.predicate.replace("_", " "), c.value, c.unit or "", c.temporal_status or "current", c.revision or "", f"p.{c.page}"])
                cits.append([key])
            result.blocks.append(TableBlock(id="rev-claims", title="Values with a temporal status", columns=["Subject", "Parameter", "Value", "Unit", "Status", "Revision", "Page"], rows=rows2, row_citations=cits))
        result.summary = f"{len(revs)} revision(s) loaded"
        result.confidence = self.confidence(0.6, "document metadata")

    def _modes(self, request, context, result) -> None:
        uids = request.entity_uids()
        normal = self.knowledge.search_chunks("normal operation " + request.original.text, k=4, chapters=[15], entity_uids=uids or None)
        temp = self.knowledge.search_chunks("temporary operation " + request.original.text, k=4, chapters=[22], entity_uids=uids or None)
        rows, cits = [], []
        for label, chs in (("Normal operation (Ch.15)", normal), ("Temporary operations (Ch.22)", temp)):
            for ch in chs[:3]:
                ex = self.excerpt(ch.text, [e.name.split(" (")[0] for e in request.entities if e.name] or ["operation"])
                key = self.cite_chunk(result, ch, ex)
                rows.append([label, ch.section_path.split(" > ")[-1] if ch.section_path else "", f"p.{ch.page_start}", ex])
                cits.append([key])
                self.statement(result, ex, [key])
        if not rows:
            result.missing.append("no normal/temporary operation text")
            result.summary = "no text"
            result.confidence = self.confidence(0.2, "nothing retrieved")
            return
        result.blocks.append(TableBlock(id="modes", title="Normal vs temporary operating configuration — documented passages", columns=["Mode", "Section", "Page", "Passage"], rows=rows, row_citations=cits))
        result.summary = f"{len(normal[:3])} normal / {len(temp[:3])} temporary passage(s)"
        result.confidence = self.confidence(0.6, "passages from the two chapters; not a structured diff")

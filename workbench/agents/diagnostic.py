"""Diagnostic Agent (Phase 4) — symptom -> documented upset section -> causes -> checks -> corrective actions.

Deterministic sentence extraction from the upset / stabilization chunks (Chapters 17-18 and
the equipment sections) is the backbone. The LLM, when available, is asked once to structure
the same passages into causes / checks / actions with verbatim quotes; every quote is checked
against the source text (fuzzy match >= 0.8) and dropped if it is not there. Topology-based
hypotheses (upstream equipment, suction source, controllers) are added and marked *inferred*.
"""
from __future__ import annotations

import difflib
import re

from pydantic import BaseModel, Field

from workbench.agents.base import BaseAgent
from workbench.core.blocks import StepItem, StepsBlock, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.knowledge import ChunkRecord
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest
from workbench.core.result import AgentResult
from workbench.services.evidence_store import evidence_from_relation

CAUSE_RE = re.compile(r"[^.\n]*\b(due to|because of|caused by|cause[sd]? (of|by)|reasons? (for|of|may|could|can|is|are)|may (be|result|occur|happen)|can (be|result|occur|happen)|could be|"
                      r"results? (in|from)|leads? to|resulting in|attributed to|if (the|there|any)|in case of|failure of|loss of|malfunction|choking|choked|plugging|plugged|fouling|leak\w*|"
                      r"cavitat\w*|vapou?r lock|air lock|low level|high level|blocked|blockage|starv\w*|passing|stuck|not opening|not closing)\b[^.\n]*\.?", re.IGNORECASE)
CHECK_RE = re.compile(r"[^.\n]*\b(check|verify|confirm|observe|ensure|look for|inspect|monitor|compare|measure|gauge (reading|indicat)|indicat(es|ion|or)|watch|note|record|examine|see whether|find out)\b[^.\n]*\.?", re.IGNORECASE)
ACTION_RE = re.compile(r"[^.\n]*\b((has|have|need|needs) to be|should be|must be|shall be|is to be|are to be|to be (reduced|increased|opened|closed|started|stopped|taken|lined|cut|adjusted|maintained|isolated|drained|vented|changed|switched|placed|kept)|"
                       r"can be (pinched|throttled|reduced|increased|opened|closed|bypassed|taken)|immediately)\b[^.\n]*\.?", re.IGNORECASE)
IMPERATIVE_START = re.compile(r"^\s*(?:\d+[.)]\s*)?(check|verify|confirm|ensure|open|close|reduce|increase|start|stop|switch|line|take|isolate|maintain|adjust|inform|stabili[sz]e|bring|raise|lower|throttle|put|change|restore|reset|divert|route|drain|vent|purge|observe|inspect|monitor|compare|keep|cut|commission|decommission|pinch|watch|so reduce|immediately)\b", re.IGNORECASE)
DESCRIPTIVE_START = re.compile(r"^\s*(this|so|thus|hence|the (column|level|pump|heater|vacuum)|it|sometimes|any|both|column level will|vacuum heater)\b", re.IGNORECASE)
SYMPTOM_SYNONYMS = {
    "pressure low": ["losing suction", "loss of suction", "suction pressure low", "cavitation", "discharge pressure drop", "flow fluctuation", "pump trip"],
    "pressure high": ["pressure rise", "pressure build up", "high pressure", "relief valve", "blocked outlet", "vapour load"],
    "flow low": ["losing suction", "low flow", "no flow", "flow reduced", "choking", "strainer", "trip"],
    "level high": ["level high", "carry over", "flooding"], "level low": ["level low", "loss of level", "dry"],
    "performance poor": ["poor desalting", "emulsion", "water carry over", "salt content high", "efficiency"],
    "trip trip": ["trip", "tripping", "flame failure", "low fuel gas pressure", "restart after trip"],
    "vacuum high": ["vacuum drop", "sudden vacuum drop", "ejector", "air leak", "leaks in the vacuum system"],
    "temperature high": ["temperature shoot up", "coking", "tube rupture", "overheating"],
}
UPSTREAM_RELS = {"FEEDS", "DISCHARGES_TO", "ROUTES_TO", "SUPPLIES", "HAS_FEED", "SUCTION_FROM", "RECEIVES_FROM", "CONTROLLED_BY", "CONTROLS", "HEATED_BY", "DRIVEN_BY"}


class DiagItem(BaseModel):
    text: str = Field(description="the cause / check / action in at most 25 words")
    quote: str = Field(description="a verbatim quote of at most 30 words copied from the passages that supports it")


class LLMDiagnosis(BaseModel):
    causes: list[DiagItem] = Field(default_factory=list, description="at most 5 possible causes of the symptom")
    checks: list[DiagItem] = Field(default_factory=list, description="at most 5 checks, in the order the manual gives them")
    actions: list[DiagItem] = Field(default_factory=list, description="at most 5 corrective actions")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if len(p.strip()) > 20]


def _grounded(quote: str, text: str) -> bool:
    q = re.sub(r"\s+", " ", quote).strip().lower()
    if not q or len(q) < 15:
        return False
    t = re.sub(r"\s+", " ", text).lower()
    if q in t:
        return True
    best = 0.0
    for s in _sentences(t):
        r = difflib.SequenceMatcher(None, q, s.lower()).ratio()
        best = max(best, r)
        if best >= 0.8:
            return True
    return False


class DiagnosticAgent(BaseAgent):
    name = "diagnostic"
    phase = "4 Execution (Diagnostic)"
    description = "Finds the documented upset section and extracts causes, checks and corrective actions."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "find_upset")
        chunks = self._upset_chunks(request, context, result)
        if mode == "find_upset":
            return self._find_upset(request, chunks, result)
        if not chunks:
            result.missing.append("no documented upset / deviation text for this symptom")
            result.blocks.append(self.callout("No upset or troubleshooting text in the documents matches this symptom and equipment. Only topology-based hints (marked inferred) can be offered.", "warning"))
            if mode == "causes":
                self._topology_hypotheses(request, result, [])
            result.summary = "no upset text"
            result.confidence = self.confidence(0.25, "nothing documented")
            return
        llm_out = self._llm_structure(request, chunks, result) if mode in ("causes", "checks", "actions") else None
        if mode == "causes":
            self._causes(request, chunks, result, llm_out)
        elif mode == "checks":
            self._checks(request, chunks, result, llm_out)
        else:
            self._actions(request, chunks, result, llm_out)

    # ------------------------------------------------------------------ retrieval
    def _upset_chunks(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> list[ChunkRecord]:
        prior = self.s.result_of("diagnostic", "upset") or self.s.result_of("diagnostic")
        if prior and prior.content.get("chunk_ids"):
            chs = [c for cid in prior.content["chunk_ids"] if (c := self.knowledge.get_chunk(cid))]
            if chs:
                return chs
        uids = request.entity_uids()
        kws = [e.name.split(" (")[0].lower() for e in request.entities if e.name] + [e.canonical_tag.lower() for e in request.entities if e.canonical_tag]
        for e in request.entities:                     # aliases of the resolved entity ("feed pump", "11-PM-01")
            rec = self.knowledge.get_entity(e.entity_uid) if e.entity_uid else None
            if rec:
                kws += [a.lower() for a in rec.aliases if len(a) >= 5 and not a.replace("-", "").replace(" ", "").isalnum() or (a and " " in a)]
        kws = list(dict.fromkeys(k for k in kws if k))
        class_word = (request.primary_entity.entity_type or "").lower() if request.primary_entity else ""
        sym = f"{request.symptom.variable} {request.symptom.direction}" if request.symptom else ""
        syn_key = f"{(request.parameter or (request.symptom.variable.split()[-1] if request.symptom else '')).replace('_rate', '')} {request.symptom.direction if request.symptom else ''}".strip()
        synonyms = SYMPTOM_SYNONYMS.get(syn_key, [])
        query = f"{request.original.text} {sym} {' '.join(synonyms)} upset cause remedy".strip()
        cands = [c for c in context.chunks if c.chunk_type in ("upset", "safety", "control", "narrative", "procedure", "equipment")]
        # always add a targeted pass over the upset chapters with the symptom synonyms
        cands += self.knowledge.search_chunks(query, k=12, chunk_types=["upset", "safety", "control", "narrative", "procedure", "equipment"], chapters=[17, 18, 14, 16, 7, 15], entity_uids=uids or None)
        seen = set()
        out = []
        for c in cands:
            if c.chunk_id in seen:
                continue
            seen.add(c.chunk_id)
            score = c.score
            low = c.text.lower()
            head = (c.section_path or "").lower()
            if c.chunk_type == "upset":
                score += 0.5
            if c.chapter_number in (17, 18):
                score += 0.3
            if any(k in low for k in kws):
                score += 0.6
            if any(k in head for k in kws) or (class_word and class_word in head.split(" > ")[-1]):
                score += 0.8                       # the section is about this equipment / equipment class
            if any(s in low for s in synonyms):
                score += 0.4
            if request.symptom and request.symptom.variable.split()[-1].lower() in low:
                score += 0.2
            out.append((score, c))
        out.sort(key=lambda x: -x[0])
        chosen = [c for _, c in out[:5]]
        result.trace.append(f"Ranked {len(out)} candidate passage(s) by section heading, equipment class and symptom wording; kept "
                            + "; ".join(f"p.{c.page_start} ({c.chunk_type})" for c in chosen) + ".")
        return chosen

    def _find_upset(self, request: StructuredRequest, chunks: list[ChunkRecord], result: AgentResult) -> None:
        if not chunks:
            result.missing.append("no upset section found")
            result.blocks.append(self.callout("No upset / deviation section matches the reported condition.", "warning"))
            result.summary = "no upset section"
            result.confidence = self.confidence(0.2, "nothing retrieved")
            result.needs_replan = True
            result.replan_reason = "no upset text for the symptom"
            return
        rows, cits = [], []
        for c in chunks:
            sec = c.section_path.split(" > ")[-1] if c.section_path else c.document_id
            key = self.cite_chunk(result, c, self.excerpt(c.text, [request.symptom.variable if request.symptom else ""] + [e.name.split(" (")[0] for e in request.entities if e.name]))
            rows.append([sec, c.chunk_type, f"p.{c.page_start}" + (f"–{c.page_end}" if c.page_end and c.page_end != c.page_start else ""), c.match_reason])
            cits.append([key])
            self.statement(result, f"Section '{sec}' (p.{c.page_start}) documents the deviation", [key])
        result.blocks.append(TableBlock(id="upset-sections", title="Documented upset / deviation sections consulted", columns=["Section", "Type", "Pages", "Match"], rows=rows, row_citations=cits))
        result.content["chunk_ids"] = [c.chunk_id for c in chunks]
        result.summary = f"{len(chunks)} upset section(s)"
        result.confidence = self.confidence(0.7 if any(c.chunk_type == "upset" for c in chunks) else 0.5, "sections retrieved by hybrid search anchored on the equipment")

    # ------------------------------------------------------------------ LLM structuring (optional, grounded)
    def _llm_structure(self, request: StructuredRequest, chunks: list[ChunkRecord], result: AgentResult) -> LLMDiagnosis | None:
        if not self.cfg.llm.use_llm_for_extraction:
            return None
        for prev in self.s.results_of("diagnostic"):
            if prev.content.get("llm_structure") is not None:
                try:
                    result.content["llm_structure"] = prev.content["llm_structure"]
                    return LLMDiagnosis.model_validate(prev.content["llm_structure"])
                except Exception:
                    break
        budget = min(self.cfg.retrieval.max_chunk_chars_in_prompt, 1200)
        passages = "\n\n".join(f"[{i+1}] (p.{c.page_start}) {c.text[:budget]}" for i, c in enumerate(chunks[:3]))
        out = self.llm_json("diagnostic", LLMDiagnosis, result, max_tokens=min(self.cfg.llm.num_predict_long, 700), purpose="diagnose",
                            symptom=f"{request.symptom.variable} {request.symptom.direction}" if request.symptom else request.original.text,
                            equipment=", ".join(e.name for e in request.entities if e.name) or "unspecified", passages=passages)
        if out is None:
            result.content["llm_structure"] = {"causes": [], "checks": [], "actions": []}   # remember the miss; do not call again
            return None
        text_all = "\n".join(c.text for c in chunks[:3])
        for field in ("causes", "checks", "actions"):
            kept = [item for item in getattr(out, field)[:5] if item.text.strip() and item.quote.strip() and _grounded(item.quote, text_all)]
            setattr(out, field, kept)
        result.content["llm_structure"] = out.model_dump(mode="json")
        result.trace.append(f"The LLM structured the upset text into {len(out.causes)} cause(s), {len(out.checks)} check(s) and {len(out.actions)} action(s); quotes not found verbatim in the evidence were dropped.")
        return out

    # ------------------------------------------------------------------ extraction helpers
    def _extract(self, chunks: list[ChunkRecord], rx: re.Pattern, result: AgentResult, limit: int, keywords: list[str]) -> list[tuple[str, str, ChunkRecord]]:
        items: list[tuple[str, str, ChunkRecord]] = []
        seen: set[str] = set()
        for c in chunks:
            if rx is IMPERATIVE_START:
                matches = [type("M", (), {"group": (lambda self, i=0, s=s: s)})() for s in _sentences(c.text) if IMPERATIVE_START.match(s)]
            else:
                matches = list(rx.finditer(c.text))
            for m in matches:
                sent = re.sub(r"\s+", " ", m.group(0)).strip(" -•")
                if len(sent) < 25 or len(sent) > 380 or sent.lower() in seen or sent.startswith("#"):
                    continue
                seen.add(sent.lower())
                key = self.cite_chunk(result, c, sent)
                items.append((sent, key, c))
                if len(items) >= limit:
                    return items
        return items

    def _chunk_for_quote(self, quote: str, chunks: list[ChunkRecord]) -> ChunkRecord | None:
        for c in chunks:
            if _grounded(quote, c.text):
                return c
        return None

    def _causes(self, request, chunks, result, llm_out) -> None:
        rows, cits = [], []
        if llm_out and llm_out.causes:
            for item in llm_out.causes[:10]:
                ch = self._chunk_for_quote(item.quote, chunks)
                if not ch:
                    continue
                key = self.cite_chunk(result, ch, item.quote[:400])
                rows.append([item.text[:160], "documented", f"p.{ch.page_start}"])
                cits.append([key])
                self.statement(result, item.text, [key])
        if len(rows) < 3:
            for sent, key, ch in self._extract(chunks, CAUSE_RE, result, 10, []):
                if any(sent[:60].lower() in r[0].lower() for r in rows):
                    continue
                rows.append([sent, "documented", f"p.{ch.page_start}"])
                cits.append([key])
                self.statement(result, sent, [key])
        self._topology_hypotheses(request, result, rows, cits)
        if not rows:
            result.missing.append("no documented causes")
            result.summary = "no causes found"
            result.confidence = self.confidence(0.25, "nothing extracted")
            return
        result.blocks.append(TableBlock(id="causes", title="Possible causes", columns=["Cause", "Basis", "Page"], rows=rows, row_citations=cits))
        result.content["causes"] = [r[0] for r in rows]
        n_doc = sum(1 for r in rows if r[1] == "documented")
        result.summary = f"{n_doc} documented cause(s), {len(rows) - n_doc} inferred from topology"
        result.confidence = self.confidence(0.7 if n_doc else 0.35, "causes quoted from the upset section" if n_doc else "topology only", ["inferred causes are hypotheses, not documented"] if len(rows) > n_doc else [])

    def _topology_hypotheses(self, request, result, rows, cits=None) -> None:
        cits = cits if cits is not None else []
        for uid in request.entity_uids()[:1]:
            rels = [r for r in self.knowledge.entity_neighbors(uid, hops=1) if r.rel_type in UPSTREAM_RELS]
            for r in rels[:5]:
                other = r.target_name if r.source_uid == uid else r.source_name
                key = self.cite(result, evidence_from_relation(r))
                hyp = f"Upstream / connected equipment to verify: {other} ({r.rel_type.lower().replace('_', ' ')})"
                rows.append([hyp, "inferred (topology)", f"p.{r.page}" if r.page else ""])
                cits.append([key])
                self.statement(result, hyp, [key], kind="topology")

    def _checks(self, request, chunks, result, llm_out) -> None:
        items: list[StepItem] = []
        if llm_out and llm_out.checks:
            for item in llm_out.checks[:12]:
                ch = self._chunk_for_quote(item.quote, chunks)
                if not ch:
                    continue
                key = self.cite_chunk(result, ch, item.quote[:400])
                items.append(StepItem(sequence=len(items) + 1, text=item.text[:240], page=ch.page_start, citation=key))
                self.statement(result, item.text, [key])
        if len(items) < 3:
            for sent, key, ch in self._extract(chunks, CHECK_RE, result, 12, []):
                if any(sent[:50].lower() in i.text.lower() for i in items):
                    continue
                items.append(StepItem(sequence=len(items) + 1, text=sent, page=ch.page_start, citation=key))
                self.statement(result, sent, [key])
        rng = self.s.result_of("calculation", "normal")
        if rng and rng.content.get("markers"):
            marks = ", ".join(f"{m['label']} {m['value']:g} {m['unit']}" for m in rng.content["markers"][:4])
            items.insert(0, StepItem(sequence=0, text=f"Compare the current reading with the documented values: {marks}.", citation=None))
            for i, it in enumerate(items):
                it.sequence = i + 1
        if not items:
            result.missing.append("no documented checks")
            result.summary = "no checks found"
            result.confidence = self.confidence(0.25, "nothing extracted")
            return
        result.blocks.append(StepsBlock(id="checks", title="Diagnostic checks (documented order)", procedure_type="diagnostic", steps=items, citations=[i.citation for i in items if i.citation]))
        result.content["checks"] = [i.text for i in items]
        result.summary = f"{len(items)} check(s)"
        result.confidence = self.confidence(0.65, "check sentences quoted from the upset section")

    def _actions(self, request, chunks, result, llm_out) -> None:
        items: list[StepItem] = []
        if llm_out and llm_out.actions:
            for item in llm_out.actions[:12]:
                ch = self._chunk_for_quote(item.quote, chunks)
                if not ch:
                    continue
                key = self.cite_chunk(result, ch, item.quote[:400])
                items.append(StepItem(sequence=len(items) + 1, text=item.text[:240], page=ch.page_start, citation=key))
                self.statement(result, item.text, [key])
        if len(items) < 3:
            for sent, key, ch in self._extract(chunks, ACTION_RE, result, 14, []) + self._extract(chunks, IMPERATIVE_START, result, 10, []):
                if DESCRIPTIVE_START.match(sent) and not ACTION_RE.match(sent):
                    continue
                if CHECK_RE.match(sent) and not IMPERATIVE_START.match(sent):
                    continue
                if any(sent[:50].lower() in i.text.lower() for i in items):
                    continue
                items.append(StepItem(sequence=len(items) + 1, text=sent, page=ch.page_start, citation=key))
                self.statement(result, sent, [key])
                if len(items) >= 12:
                    break
        if not items:
            result.missing.append("no documented corrective actions")
            result.summary = "no actions found"
            result.confidence = self.confidence(0.25, "nothing extracted")
            return
        result.blocks.append(StepsBlock(id="actions", title="Documented corrective actions", procedure_type="corrective_actions", steps=items, citations=[i.citation for i in items if i.citation]))
        result.content["actions"] = [i.text for i in items]
        result.summary = f"{len(items)} corrective action(s)"
        result.confidence = self.confidence(0.65, "action sentences quoted from the upset / stabilization text")

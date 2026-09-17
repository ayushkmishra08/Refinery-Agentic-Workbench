"""Phase 2 specialist retrieval agents (diagram: Graph Retrieval, Document Retrieval,
Equipment/Specification, Evidence/Provenance).

LookupAgent       -> "lookup"         claims for an entity (property lookup, limits gathering, identification)
GraphAgent        -> "graph"          flow traces, instruments, neighbours, standby equipment
ExplanationAgent  -> "explanation"    grounded text for why / support / consequences questions
CrossDocumentAgent-> "cross_document" sections, cross references, referenced documents, standing instructions
"""
from __future__ import annotations

import re
from collections import defaultdict

from pydantic import BaseModel, Field

from workbench.agents.base import BaseAgent, usable_narrative
from workbench.core.blocks import GraphBlock, GraphEdge, GraphNode, KpiBlock, KpiItem, TableBlock
from workbench.core.context import ContextPackage
from workbench.core.evidence import Evidence
from workbench.core.knowledge import ClaimRecord, EntityRecord, RelationRecord
from workbench.core.plan import PlanStep
from workbench.core.request import StructuredRequest, TaskType
from workbench.core.result import AgentResult
from workbench.services.evidence_store import evidence_from_relation
from workbench.services.index.builder import INSTRUMENT_TYPES, PREFIX_TYPES, norm_alias
from workbench.services.revision_resolver import rank_claims

PARAM_PREDICATES: dict[str, set[str]] = {
    "flow_rate": {"flow_rate", "volumetric_flow_rate", "mass_flow_rate", "capacity", "throughput", "normal_flow", "design_flow", "minimum_flow"},
    "pressure": {"pressure", "operating_pressure", "design_pressure", "discharge_pressure", "suction_pressure", "differential_pressure", "set_pressure", "relief_pressure"},
    "design_pressure": {"design_pressure"}, "design_temperature": {"design_temperature"},
    "temperature": {"temperature", "operating_temperature", "design_temperature", "inlet_temperature", "outlet_temperature", "skin_temperature"},
    "level": {"level", "operating_level"}, "speed": {"speed", "rpm"}, "head": {"differential_head", "head", "generic_property"}, "npsh": {"npsh", "generic_property"},
    "duty": {"duty", "heat_duty"}, "vacuum": {"pressure", "operating_pressure", "vacuum"},
}
ROLE_WORDS = [(r"\bnormal\b", "normal"), (r"\bdesign\b", "design"), (r"\bminimum\b|\bmin\b", "minimum"), (r"\bmaximum\b|\bmax\b|\ballowable\b", "maximum"), (r"\brated\b", "rated"), (r"\btrip\b", "trip"), (r"\balarm\b", "alarm")]
FLOW_RELS = {"FEEDS", "DISCHARGES_TO", "ROUTES_TO", "SUPPLIES", "HAS_PRODUCT", "RETURN_TO", "RECYCLES_TO", "UPSTREAM_OF", "CONNECTED_TO", "SUCTION_FROM", "RECEIVES_FROM", "HAS_FEED", "DOWNSTREAM_OF"}
REVERSE_FLOW = {"SUCTION_FROM", "RECEIVES_FROM", "HAS_FEED", "DOWNSTREAM_OF"}          # source is downstream of target
INSTRUMENT_VARIABLE = {"P": "pressure", "T": "temperature", "F": "flow", "L": "level", "A": "analysis", "PD": "differential pressure", "TD": "differential temperature", "H": "hand / manual", "Z": "position", "X": "unclassified", "B": "burner / flame", "U": "multi-variable"}
INSTRUMENT_FUNCTION = {"I": "indicator", "G": "gauge", "T": "transmitter", "C": "controller", "R": "recorder", "V": "control valve", "S": "switch", "A": "alarm", "E": "element", "Y": "relay / computing", "Q": "totalizer", "SV": "safety valve"}


def predicates_for(parameter: str | None) -> set[str] | None:
    return PARAM_PREDICATES.get(parameter) if parameter else None


def describe_instrument(tag: str) -> str:
    m = re.match(r"^(?:\d{1,3}-)?([A-Z]{1,4})-", tag)
    if not m:
        return "instrument"
    pre = m.group(1)
    var = INSTRUMENT_VARIABLE.get(pre[:2]) if pre[:2] in ("PD", "TD") else INSTRUMENT_VARIABLE.get(pre[0], "process variable")
    rest = pre[2:] if pre[:2] in ("PD", "TD") else pre[1:]
    if rest == "SV":
        return "pressure safety valve"
    funcs = [INSTRUMENT_FUNCTION.get(ch, ch) for ch in rest]
    return f"{var} {' '.join(funcs)}".strip()


def class_label(entity_type: str | None, plural: bool = False) -> str:
    """Human wording for an index entity_type: ReliefValve -> "relief valve(s)"."""
    if not entity_type:
        return "item" + ("s" if plural else "")
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", entity_type).lower()
    return words + ("s" if plural else "")


def context_label(c: ClaimRecord) -> str:
    parts = [p for p in [c.parameter_role, c.location, c.operating_mode, c.scenario, c.pressure_basis] if p]
    if c.qualifier and c.qualifier.lower() not in " ".join(parts).lower():
        parts.append(c.qualifier[:40])
    return ", ".join(parts)


class LookupAgent(BaseAgent):
    name = "lookup"
    phase = "2 Specialist retrieval (Equipment / Specification)"
    description = "Retrieves documented values for an entity from the claim layer."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "default")
        if mode == "identify":
            return self._identify(request, context, result)
        if mode == "inventory":
            return self._inventory(request, context, result)
        entity = request.primary_entity
        claims = context.claims_for(entity.entity_uid) if entity and entity.entity_uid else list(context.claims)
        if not claims and entity and entity.entity_uid:
            claims = self.knowledge.entity_claims(entity.entity_uid)
        docs = {d.document_id: d for d in self.knowledge.documents()}
        claims = rank_claims(claims, docs)
        selected, note = self._select(claims, request, mode)
        label = entity.name if entity and entity.name else (request.unresolved_mentions[0] if request.unresolved_mentions else "the requested item")
        if not selected:
            return self._fallback(request, context, result, label)
        twins = context.entity_groups.get(entity.entity_uid, []) if entity and entity.entity_uid else []
        twin_names = [self.knowledge.get_entity(u).name for u in twins if self.knowledge.get_entity(u)]
        items: list[KpiItem] = []
        rows: list[list] = []
        row_cits: list[list[str]] = []
        for c in selected[:24]:
            key = self.cite_claim(result, c)
            qual = context_label(c)
            subj = c.subject if not entity or (c.subject_uid != entity.entity_uid) else ""
            label_txt = c.predicate.replace("_", " ")
            if subj and twins:
                label_txt = f"{label_txt} ({subj})"
            items.append(KpiItem(label=label_txt, value=c.value, unit=c.unit, qualifier=qual or None, citation=key))
            rows.append([c.predicate.replace("_", " "), qual or "—", c.value, c.unit or "", c.subject, f"p.{c.page}" if c.page else ""])
            row_cits.append([key])
            self.statement(result, f"{c.subject} {c.predicate.replace('_', ' ')}{(' (' + qual + ')') if qual else ''} = {c.value} {c.unit or ''}".strip(), [key])
        title = f"{label}" + (f" — also tagged {', '.join(twin_names)}" if twin_names else "")
        if len(items) <= 6:
            result.blocks.append(KpiBlock(id=f"kpi-{step.step_id}", title=title, items=items, citations=[k for ks in row_cits for k in ks]))
        else:
            result.blocks.append(TableBlock(id=f"tbl-{step.step_id}", title=title, columns=["Parameter", "Context", "Value", "Unit", "Subject", "Page"], rows=rows, row_citations=row_cits))
        if note:
            result.blocks.append(self.callout(note, "info"))
        result.content["claims"] = [c.model_dump(mode="json") for c in selected]
        result.content["entity"] = entity.model_dump(mode="json") if entity else None
        result.summary = f"{len(selected)} documented value(s) for {label}"
        result.confidence = self.confidence(0.85 if selected else 0.3, f"{len(selected)} claim(s) from tables/specifications with page references",
                                            ["values come from different tags for the same equipment" if twin_names else ""] if twin_names else [])

    def _select(self, claims: list[ClaimRecord], request: StructuredRequest, mode: str) -> tuple[list[ClaimRecord], str | None]:
        preds = predicates_for(request.parameter)
        text = request.original.text.lower()
        role = next((r for rx, r in ROLE_WORDS if re.search(rx, text)), None)
        loc = "discharge" if "discharge" in text else "suction" if "suction" in text else None
        rows = claims
        note = None
        if preds:
            filtered = [c for c in rows if c.predicate in preds or any(p in c.predicate for p in preds if p != "generic_property")]
            if request.parameter in ("head", "npsh"):
                filtered = [c for c in filtered if c.predicate != "generic_property" or request.parameter in (c.qualifier or "").lower() or request.parameter in c.evidence.lower()]
            if filtered:
                rows = filtered
            elif rows:
                note = f"No value labelled '{request.parameter.replace('_', ' ')}' is documented for this equipment; all documented values are shown instead."
        if mode == "limits":
            lim = [c for c in rows if (c.parameter_role or "") in ("normal", "minimum", "maximum", "design", "mechanical_design", "rated", "operating", "trip", "alarm", "relief") or "design" in c.predicate]
            rows = lim or rows
        else:
            if role and mode != "limits":
                r = [c for c in rows if (c.parameter_role or "").lower() == role or (role == "design" and "design" in c.predicate)]
                if r:
                    rows = r
            if loc:
                r = [c for c in rows if (c.location or "").lower() == loc]
                if r:
                    rows = r
        if request.scenario:
            r = [c for c in rows if request.scenario.lower() in (c.scenario or "").lower() or request.scenario.lower() in (c.qualifier or "").lower()]
            if r:
                rows = r
        return rows, note

    def _fallback(self, request: StructuredRequest, context: ContextPackage, result: AgentResult, label: str) -> None:
        """No claim answers the question: use entity search + grounded chunk excerpts (keyword only)."""
        query = request.original.text
        uids = request.entity_uids()
        chunks = context.chunks or self.knowledge.search_chunks(query, k=4, entity_uids=uids or None)
        if not chunks and request.unresolved_mentions:
            ents = self.knowledge.search_entities(" ".join(request.unresolved_mentions), limit=6)
            if ents:
                rows = [[e.name, e.entity_type or "", ", ".join(e.aliases[:3]), len(e.pages)] for e in ents]
                result.blocks.append(TableBlock(id="entities", title=f"Equipment matching '{' '.join(request.unresolved_mentions)}'", columns=["Equipment", "Type", "Also called", "Pages"], rows=rows))
                result.summary = f"{len(ents)} matching equipment item(s)"
                result.confidence = self.confidence(0.5, "entity index match; no numeric claim requested")
                return
        if not chunks:
            result.ok = True
            result.missing.append(f"no documented value or text found for {label}")
            result.blocks.append(self.callout(f"The documents do not contain a value or description for **{label}** matching this question. UNKNOWN is reported rather than a guess.", "warning"))
            result.confidence = self.confidence(0.15, "nothing retrieved")
            result.summary = f"nothing documented for {label}"
            return
        keys = []
        md = []
        kws = [label.split(" (")[0]] + [e.canonical_tag for e in request.entities if e.canonical_tag] + request.unresolved_mentions
        for ch in chunks[:3]:
            ex = self.excerpt(ch.text, kws)
            k = self.cite_chunk(result, ch, ex)
            keys.append(k)
            md.append(f"> {ex}  \n> — {ch.section_path.split(' > ')[-1] if ch.section_path else ch.document_id}, p.{ch.page_start}")
            self.statement(result, ex, [k], kind="fact")
        result.blocks.append(self.text_block("\n\n".join(md), title=f"What the manual says about {label}", citations=keys))
        result.summary = f"{len(chunks[:3])} text passage(s) for {label}"
        result.confidence = self.confidence(0.55, "answer grounded in manual text, not in a structured value")

    def _identify(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> None:
        if not request.entities:
            result.missing.append("no entity identified")
            result.summary = "no entity identified"
            result.confidence = self.confidence(0.2, "no entity")
            return
        rows = []
        for e in request.entities:
            rec = self.knowledge.get_entity(e.entity_uid) if e.entity_uid else None
            if rec is None:
                continue
            twins = [self.knowledge.get_entity(u) for u in context.entity_groups.get(rec.entity_uid, [])]
            rows.append([rec.name, rec.entity_type or "?", ", ".join(t.canonical_tag or t.name for t in twins if t) or "—", rec.mention_count, f"{min(rec.pages)}–{max(rec.pages)}" if rec.pages else "—", rec.description or ""])
            self.statement(result, f"{rec.name} is a {rec.entity_type or 'documented item'} mentioned {rec.mention_count} times", [], kind="fact")
        if rows:
            result.blocks.append(TableBlock(id="identify", title="Equipment identified", columns=["Equipment", "Type", "Other tags", "Mentions", "Pages", "Description"], rows=rows))
        if request.symptom:
            s = request.symptom
            label = {"trip": "repeated trips", "failure": "repeated failures", "leak": "leakage"}.get(s.variable, f"{s.variable} {s.direction}") if s.variable == s.direction or s.direction in ("trip", "failure", "leak") else f"{s.variable} {s.direction}"
            result.blocks.append(self.text_block(f"Observed condition: **{label}**" + (f" (parameter: {request.parameter})" if request.parameter else "")))
        result.content["entities"] = [e.model_dump(mode="json") for e in request.entities]
        result.summary = ", ".join(r[0] for r in rows) or "entity identified"
        result.confidence = self.confidence(max((e.confidence for e in request.entities), default=0.5), "entity resolution")

    # Below this many plant-tagged items, a document is probably not a plant manual and the
    # inventory should be listed by whatever names it does use.
    _THIN_INVENTORY = 5

    # ------------------------------------------------------------------ inventory / survey
    def _inventory(self, request: StructuredRequest, context: ContextPackage, result: AgentResult) -> None:
        """Answer "what is in here" from the entity index rather than from a text search.

        Every row is a tagged entity the knowledge layer extracted, with the pages it appears
        on, so the list is auditable in the same way a single value is.
        """
        docs = self.knowledge.documents()
        counts = context.entity_counts or self.knowledge.entity_type_counts()
        entities = context.entities or self.knowledge.list_entities(entity_type=request.subject_type, limit=self.cfg.effort.inventory_limit)
        wanted = request.subject_type
        by_model_number = False

        # The strict listing counts plant tags — 11-P-01, 080-H-001 — which is right for a unit
        # manual and wrong for everything else. A vendor catalogue, a standard or a datasheet names
        # its equipment by model number (AVG-100, CPU-5.4, KhGN-1), and those are not plant tags. On
        # such a document the strict pass finds one or two stragglers and the answer becomes
        # "the catalogue covers one reactor" — confident, citable and wrong about the document as a
        # whole. So when the strict pass is thin and a looser one is not, list what is actually
        # there and say that these are names rather than plant tags.
        #
        # On a unit manual the strict pass returns hundreds and none of this runs.
        if len(entities) < self._THIN_INVENTORY:
            loose = self.knowledge.list_entities(entity_type=wanted, limit=self.cfg.effort.inventory_limit,
                                                 tagged_only=False, plant_only=False)
            if len(loose) > len(entities):
                entities, by_model_number = loose, True
                counts = self.knowledge.entity_type_counts(tagged_only=False, plant_only=False) or counts

        if not entities:
            result.missing.append(f"no tagged {class_label(wanted, plural=True) if wanted else 'equipment'} in the documents")
            result.blocks.append(self.callout(
                f"The documents contain no tagged {class_label(wanted, plural=True) if wanted else 'equipment'}." +
                (f" Recorded classes are: {', '.join(class_label(c) for c in counts)}." if counts else ""), "warning"))
            result.summary = "nothing to list"
            result.confidence = self.confidence(0.3, "the entity index holds no item of that class")
            return

        total = sum(counts.values())
        doc_names = ", ".join(d.title or d.document_id for d in docs) or "the loaded documents"

        if not wanted and counts:
            rows = [[class_label(cls).title(), n, ", ".join(e.canonical_tag or e.name for e in self.knowledge.list_entities(entity_type=cls, limit=3))]
                    for cls, n in counts.items()]
            result.blocks.append(TableBlock(id="inventory-classes", title="Equipment classes on record",
                                            caption=(f"Every item the knowledge layer extracted from {doc_names}, grouped by class."
                                                     if by_model_number else
                                                     f"Every tag the knowledge layer extracted from {doc_names}, grouped by class."),
                                            columns=["Class", "Tagged items", "Most referenced"], rows=rows))

        rows, cits = [], []
        for e in entities:
            pages = f"{min(e.pages)}–{max(e.pages)}" if e.pages else "—"
            rows.append([e.canonical_tag or "—", e.name, e.entity_type or "—", e.mention_count, pages])
            if e.pages:
                cits.append(self.cite(result, Evidence(document_id=e.document_ids[0] if e.document_ids else "", page=min(e.pages),
                                                       text=f"{e.canonical_tag or e.name}: {e.name}" + (f" — {e.description}" if e.description else ""),
                                                       source="graph")))
        title = f"{class_label(wanted, plural=True).title() if wanted else 'Equipment'} on record" + (f" — {len(rows)} shown" if len(rows) < counts.get(wanted, total) else "")
        result.blocks.append(TableBlock(id="inventory", title=title, columns=["Tag", "Name", "Class", "Mentions", "Pages"],
                                        rows=rows, citations=cits[:12]))
        if len(rows) < counts.get(wanted, total):
            result.blocks.append(self.callout(
                f"{counts.get(wanted, total) - len(rows)} further tagged item(s) are on record. Ask for a class "
                f"(\"list all the pumps\") or raise the effort level to see more.", "info"))
        result.content["entity_counts"] = counts
        result.content["listed"] = [e.canonical_tag or e.name for e in entities]
        where = f" in the {request.scope}" if request.scope and request.scope != "document" else ""
        result.summary = (f"{counts.get(wanted, len(entities))} tagged {class_label(wanted, plural=True)}{where or ' in ' + doc_names}" if wanted
                          else f"{total} tagged items across {len(counts)} equipment classes{where or ' in ' + doc_names}")
        result.confidence = self.confidence(0.88, "counted from the knowledge layer's entity index, each row carrying its pages")


class GraphAgent(BaseAgent):
    name = "graph"
    phase = "2 Specialist retrieval (Graph)"
    description = "Traverses the engineering graph: flow paths, instruments, standby equipment, neighbours."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        text = request.original.text.lower()
        uids = request.entity_uids()
        if not uids:
            result.missing.append("no entity to traverse from")
            result.summary = "no entity"
            result.confidence = self.confidence(0.2, "no entity")
            return
        wants_instruments = bool(re.search(r"instrument|monitor|measure|variables|control", text))
        wants_standby = bool(re.search(r"standby|stand-by|spare|which pumps? (can|are available)", text))
        rels = list(context.relations) or [r for u in uids for r in self.knowledge.entity_neighbors(u, hops=2 if request.task_type == TaskType.MULTI_HOP else 1)]
        if wants_instruments:
            self._instruments(request, uids, rels, result)
        elif wants_standby:
            self._standby(request, uids, result)
        elif len(uids) >= 2 and re.search(r"trace|from .* to|path|between", text):
            self._trace(request, uids, result)
        else:
            self._neighbourhood(request, uids, rels, result, downstream_only=bool(re.search(r"downstream|receives|after leaving", text)), upstream_only=bool(re.search(r"upstream|before entering|pass(es)? through", text)))

    # ---- helpers ------------------------------------------------------------------
    def _name(self, uid: str) -> str:
        e = self.knowledge.get_entity(uid)
        return e.name if e else uid

    def _edge(self, r: RelationRecord, result: AgentResult) -> GraphEdge:
        key = self.cite(result, evidence_from_relation(r))
        inferred = r.source != "rule"
        self.statement(result, f"{r.source_name} {r.rel_type.lower().replace('_', ' ')} {r.target_name}", [key], kind="topology" if inferred else "fact")
        return GraphEdge(source=r.source_uid, target=r.target_uid, label=r.rel_type.replace("_", " ").lower() + (" (inferred)" if inferred else ""), citation=key, inferred=inferred)

    def _graph_block(self, uids_focus: list[str], rels: list[RelationRecord], result: AgentResult, title: str, layout: str = "left-right") -> GraphBlock:
        nodes: dict[str, GraphNode] = {}
        edges = []
        for r in rels:
            for uid, nm in ((r.source_uid, r.source_name), (r.target_uid, r.target_name)):
                if uid not in nodes:
                    e = self.knowledge.get_entity(uid)
                    nodes[uid] = GraphNode(id=uid, label=(e.name if e else nm), type=(e.entity_type if e else None), is_focus=uid in uids_focus)
            edges.append(self._edge(r, result))
        for uid in uids_focus:
            if uid not in nodes:
                e = self.knowledge.get_entity(uid)
                nodes[uid] = GraphNode(id=uid, label=e.name if e else uid, type=e.entity_type if e else None, is_focus=True)
        mm = ["graph LR" if layout == "left-right" else "graph TD"]
        for n in nodes.values():
            safe = re.sub(r"[^A-Za-z0-9_]", "_", n.id)
            lbl = n.label.replace('"', "'")
            mm.append(f'  {safe}["{lbl}"]' + (":::focus" if n.is_focus else ""))
        for ed in edges:
            mm.append(f"  {re.sub(r'[^A-Za-z0-9_]', '_', ed.source)} -->|{ed.label}| {re.sub(r'[^A-Za-z0-9_]', '_', ed.target)}")
        mm.append("  classDef focus stroke-width:3px;")
        return GraphBlock(id="graph", title=title, nodes=list(nodes.values()), edges=edges, layout=layout, mermaid="\n".join(mm), citations=[e.citation for e in edges if e.citation])

    def _neighbourhood(self, request, uids, rels, result, downstream_only=False, upstream_only=False) -> None:
        focus = set(uids)

        def is_down(r: RelationRecord) -> bool:
            return (r.source_uid in focus and r.rel_type in FLOW_RELS - REVERSE_FLOW) or (r.target_uid in focus and r.rel_type in REVERSE_FLOW)

        def is_up(r: RelationRecord) -> bool:
            return (r.target_uid in focus and r.rel_type in FLOW_RELS - REVERSE_FLOW) or (r.source_uid in focus and r.rel_type in REVERSE_FLOW)

        chosen = rels
        if downstream_only:
            chosen = [r for r in rels if is_down(r)] or rels
        elif upstream_only:
            chosen = [r for r in rels if is_up(r)] or rels
        chosen = chosen[:40]
        if not chosen:
            result.missing.append("no documented relationships for the equipment")
            result.blocks.append(self.callout(f"No routing or connection sentence about **{self._name(uids[0])}** was recognised by the deterministic rules. Text passages may still describe it (see other blocks).", "warning"))
            result.confidence = self.confidence(0.2, "no relationships")
            result.summary = "no relationships"
            return
        title = ("Downstream of " if downstream_only else "Upstream of " if upstream_only else "Connections of ") + ", ".join(self._name(u) for u in uids)
        result.blocks.append(self._graph_block(uids, chosen, result, title))
        n_doc = sum(1 for r in chosen if r.source == "rule")
        result.content["relations"] = [r.model_dump(mode="json") for r in chosen]
        result.summary = f"{len(chosen)} relationship(s), {n_doc} from explicit sentences"
        result.confidence = self.confidence(0.75 if n_doc else 0.45, f"{n_doc} rule-based relationships with sentence evidence, {len(chosen) - n_doc} inferred from co-mention",
                                            ["inferred links come from instruments/equipment named in the same sentence"] if len(chosen) > n_doc else [])

    def _trace(self, request, uids, result) -> None:
        src, dst = uids[0], uids[1]
        # BFS along flow relations, both explicit directions
        adj: dict[str, list[RelationRecord]] = defaultdict(list)
        frontier = [src]
        seen = {src}
        parent: dict[str, RelationRecord] = {}
        for _ in range(6):
            nxt = []
            for u in frontier:
                for r in self.knowledge.entity_neighbors(u, hops=1):
                    if r.rel_type not in FLOW_RELS and r.rel_type not in ("PART_OF", "CONNECTED_TO", "HEATED_BY", "COOLED_BY"):
                        continue
                    a, b = (r.source_uid, r.target_uid) if r.rel_type not in REVERSE_FLOW else (r.target_uid, r.source_uid)
                    if a != u:
                        continue
                    if b not in seen:
                        seen.add(b)
                        parent[b] = r
                        nxt.append(b)
            frontier = nxt
            if dst in seen or not frontier:
                break
        if dst in parent:
            path = []
            cur = dst
            while cur != src:
                r = parent[cur]
                path.append(r)
                cur = r.source_uid if r.rel_type not in REVERSE_FLOW else r.target_uid
            path.reverse()
            result.blocks.append(self._graph_block([src, dst], path, result, f"Flow path: {self._name(src)} → {self._name(dst)}"))
            result.content["path"] = [r.model_dump(mode="json") for r in path]
            result.summary = f"path with {len(path)} hop(s)"
            result.confidence = self.confidence(0.8 if all(r.source == "rule" for r in path) else 0.55, "path assembled from documented routing sentences")
        else:
            rels = self.knowledge.entity_neighbors(src, hops=2) + self.knowledge.entity_neighbors(dst, hops=1)
            result.blocks.append(self.callout(f"No continuous documented route from **{self._name(src)}** to **{self._name(dst)}** was found in the extracted relationships. The neighbourhoods of both are shown; the narrative text may describe intermediate equipment.", "warning"))
            if rels:
                result.blocks.append(self._graph_block([src, dst], rels[:40], result, "Known connections around both ends"))
            result.missing.append("continuous flow path")
            result.summary = "no complete path; partial neighbourhoods"
            result.confidence = self.confidence(0.35, "partial topology only")

    def _instruments(self, request, uids, rels, result) -> None:
        focus = set(uids)
        inst_rels = [r for r in rels if (r.source_uid in focus or r.target_uid in focus)
                     and r.rel_type in ("MONITORS", "CONTROLS", "CONTROLLED_BY", "MEASURED_BY", "MONITORED_BY", "INDICATED_BY", "ALARMED_BY", "TRIPPED_BY", "PROTECTED_BY", "RELIEVED_BY")]
        inst_rels.sort(key=lambda r: (0 if r.source == "description" else 1 if r.source == "rule" else 2, -r.confidence))
        if not inst_rels:
            for u in uids:
                inst_rels += [r for r in self.knowledge.entity_neighbors(u, hops=1) if r.rel_type in ("MONITORS", "CONTROLS", "CONTROLLED_BY", "MEASURED_BY", "PROTECTED_BY", "RELIEVED_BY")]
        rows, cits = [], []
        seen = set()
        for r in inst_rels:
            inst_uid = r.source_uid if r.rel_type in ("MONITORS", "CONTROLS") else r.target_uid
            inst = self.knowledge.get_entity(inst_uid)
            if not inst or inst_uid in seen or (inst.entity_type not in INSTRUMENT_TYPES and inst.entity_type not in ("ReliefValve", "SafetyValve")):
                continue
            seen.add(inst_uid)
            key = self.cite(result, evidence_from_relation(r))
            desc = describe_instrument(inst.canonical_tag or inst.name)
            rows.append([inst.canonical_tag or inst.name, inst.entity_type or "", desc, r.rel_type.lower().replace("_", " ") + (" (inferred)" if r.source != "rule" else ""), f"p.{r.page}" if r.page else ""])
            cits.append([key])
            self.statement(result, f"{inst.canonical_tag or inst.name} ({desc}) is associated with {self._name(uids[0])}", [key], kind="topology" if r.source != "rule" else "fact")
        if not rows:
            # fall back: instrument tags in the same sentence as the equipment (same plant prefix when both carry one)
            ent = self.knowledge.get_entity(uids[0])
            aliases = [a.lower() for a in (ent.aliases if ent else []) if len(a) >= 4] + ([ent.name.split(" (")[0].lower()] if ent else [])
            ent_plant = (ent.canonical_tag or "").split("-")[0] if ent and ent.canonical_tag and ent.canonical_tag[0].isdigit() else None
            for ch in self.knowledge.chunks_for_entity(uids[0], limit=16):
                for sent in re.split(r"(?<=[.;:!?])\s+|\n+", ch.text):
                    low = sent.lower()
                    if len(sent) > 400 or not any(a in low for a in aliases):
                        continue
                    for tag in re.findall(r"\b(?:\d{1,3}-)?[A-Z]{1,4}-\d{2,5}[A-Z]?\b", sent):
                        parts = tag.split("-")
                        pre = parts[1] if len(parts) == 3 else parts[0]
                        plant = parts[0] if len(parts) == 3 else None
                        if PREFIX_TYPES.get(pre) not in INSTRUMENT_TYPES or tag in seen:
                            continue
                        if ent_plant and plant and plant != ent_plant:
                            continue
                        seen.add(tag)
                        key = self.cite_chunk(result, ch, re.sub(r"\s+", " ", sent).strip()[:400])
                        rows.append([tag, PREFIX_TYPES.get(pre, ""), describe_instrument(tag), "same sentence (inferred)", f"p.{ch.page_start}"])
                        cits.append([key])
                        self.statement(result, f"{tag} is mentioned with {self._name(uids[0])}", [key], kind="topology")
                    if len(rows) >= 10:
                        break
                if len(rows) >= 10:
                    break
        if not rows:
            result.missing.append("no instruments documented for the equipment")
            result.blocks.append(self.callout(f"No instrument tag is documented together with **{self._name(uids[0])}** in the extracted text.", "warning"))
            result.confidence = self.confidence(0.2, "no instruments")
            result.summary = "no instruments"
            return
        result.blocks.append(TableBlock(id="instruments", title=f"Instruments associated with {self._name(uids[0])}", columns=["Tag", "Type", "Measures / function", "Relationship", "Page"], rows=rows, row_citations=cits))
        result.content["instruments"] = [r[0] for r in rows]
        result.summary = f"{len(rows)} instrument(s)"
        result.confidence = self.confidence(0.6, "instrument tags linked by controller/indicator sentences; measured variable derived from the ISA tag letters", ["relationship type is inferred from co-mention where marked"])

    def _standby(self, request, uids, result) -> None:
        primary = self.knowledge.get_entity(uids[0])
        rows, cits = [], []
        if primary and primary.canonical_tag:
            from knowledge_layer.entity_identity import parse_tag

            ident = parse_tag(primary.canonical_tag)
            children = ident.children if ident else []
            for child in children:
                rows.append([child, "train of the same tag (A/B)", "installed spare / standby", ""])
        # entities sharing the name (twins) and changeover procedures
        for e in self.knowledge.resolve_entity(primary.name.split(" (")[0] if primary else request.original.text, limit=6):
            if e.entity_uid != uids[0] and e.canonical_tag and [e.canonical_tag, "", "", ""] not in rows:
                rows.append([e.canonical_tag, e.name, "same service (alternate tag)", ""])
        procs = self.knowledge.procedures(entity_uid=uids[0], query="change over standby", procedure_type="changeover", limit=4)
        for p in procs:
            key = self.cite(result, __import__("workbench.services.evidence_store", fromlist=["evidence_from_step"]).evidence_from_step(p, p.steps[0])) if p.steps else None
            rows.append([p.title[:60], "changeover procedure", f"{p.procedure_type}, p.{p.page_start}", key or ""])
            cits.append([key] if key else [])
        if not rows:
            result.missing.append("no standby / spare equipment documented")
            result.summary = "no standby information"
            result.confidence = self.confidence(0.2, "nothing found")
            return
        result.blocks.append(TableBlock(id="standby", title=f"Standby options for {primary.name if primary else uids[0]}", columns=["Item", "Description", "Basis", "Evidence"], rows=rows))
        result.content["changeover_procedures"] = [p.procedure_id for p in procs]
        result.summary = f"{len(rows)} standby/changeover item(s)"
        result.confidence = self.confidence(0.6, "A/B trains from the tag plus changeover procedures", ["conditions for use come from the changeover procedure text"])


class ExplanationAgent(BaseAgent):
    name = "explanation"
    phase = "2 Specialist retrieval (Document)"
    description = "Grounded explanatory text: why questions, supporting passages, consequences of deviation."

    class Summary(BaseModel):
        summary: str = Field(description="2-4 sentences answering the question using only the passages; cite passages as [P1], [P2]")
        used_passages: list[int] = Field(default_factory=list)

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "why")
        uids = request.entity_uids()
        kws = [e.name.split(" (")[0] for e in request.entities if e.name] + [e.canonical_tag for e in request.entities if e.canonical_tag] + request.unresolved_mentions
        if mode == "consequences":
            query = f"consequences of deviation {request.parameter or ''} {' '.join(kws)}"
            chunks = self.knowledge.search_chunks(query, k=4, chunk_types=["safety", "narrative", "table", "control", "upset"], chapters=[14, 17, 18], entity_uids=uids or None)
            title = "Documented consequences of deviation"
        elif mode == "support":
            chunks = context.chunks[:4] or self.knowledge.search_chunks(request.original.text, k=4, entity_uids=uids or None)
            title = "Supporting passages from the manual"
        else:
            chunks = context.chunks[:5] or self.knowledge.search_chunks(request.original.text, k=5, chunk_types=["narrative", "equipment", "control", "safety"], entity_uids=uids or None)
            title = "What the manual says"
        if not chunks:
            result.missing.append("no explanatory text found")
            result.blocks.append(self.callout("No passage in the documents addresses this question; UNKNOWN rather than a guess.", "warning"))
            result.confidence = self.confidence(0.15, "nothing retrieved")
            result.summary = "no passages"
            return
        why_words = ["because", "in order to", "so that", "to prevent", "to avoid", "purpose", "to ensure", "this is done", "is used to", "is provided to", "helps", "improves", "otherwise"]
        passages = []
        keys = []
        for i, ch in enumerate(chunks, 1):
            ex = self.excerpt(ch.text, (why_words if mode == "why" else []) + kws, width=520)
            if mode == "why" and not any(w in ex.lower() for w in why_words):
                ex2 = self.excerpt(ch.text, kws, width=520)
                ex = ex2 if kws else ex
            key = self.cite_chunk(result, ch, ex)
            keys.append(key)
            passages.append((i, ex, ch, key))
            self.statement(result, ex, [key])
        md = "\n\n".join(f"**[P{i}]** {ex}  \n<sub>{ch.section_path.split(' > ')[-1] if ch.section_path else ch.document_id} — p.{ch.page_start}</sub>" for i, ex, ch, _ in passages)
        summary_text = None
        if mode == "why" and self.cfg.llm.use_llm_for_narrative:
            out = self.llm_json("explanation", self.Summary, result, max_tokens=260, purpose="why_summary",
                                question=request.original.text, passages="\n".join(f"[P{i}] {ex}" for i, ex, _, _ in passages))
            if out and out.summary and usable_narrative(out.summary, request.original.text):
                summary_text = out.summary.strip()
                used = [passages[i - 1][3] for i in out.used_passages if 0 < i <= len(passages)] or keys
                self.statement(result, summary_text, used, kind="inference")
            elif out and out.summary:
                result.trace.append("The model's summary was not usable (question restated or task narrated), so it was dropped; the quoted passages stand on their own.")
        if summary_text:
            result.blocks.append(self.text_block(summary_text, title="Answer", citations=keys))
        result.blocks.append(self.text_block(md, title=title, citations=keys))
        result.content["passages"] = [{"chunk_id": ch.chunk_id, "page": ch.page_start, "excerpt": ex} for _, ex, ch, _ in passages]
        result.summary = f"{len(passages)} grounded passage(s)" + (" + summary" if summary_text else "")
        result.confidence = self.confidence(0.7 if summary_text else 0.6, "explanation grounded in quoted passages", ["summary sentence is a paraphrase of the cited passages"] if summary_text else [])


class CrossDocumentAgent(BaseAgent):
    name = "cross_document"
    phase = "2 Specialist retrieval (Evidence / Provenance)"
    description = "Finds sections, cross references, referenced documents and standing instructions."

    def execute(self, request: StructuredRequest, context: ContextPackage, step: PlanStep, result: AgentResult) -> None:
        mode = step.inputs.get("mode", "find")
        if mode in ("for_procedure", "for_safety"):
            return self._for_prior(request, context, step, result)
        if mode == "scope":
            return self._scope(result)
        query = request.original.text
        uids = request.entity_uids()
        chunks = context.chunks or self.knowledge.search_chunks(query, k=10, entity_uids=uids or None)
        sections = context.sections or self.knowledge.sections(query, limit=8)
        if mode == "find":
            rows, cits = [], []
            seen = set()
            for ch in chunks[:10]:
                sec = ch.section_path.split(" > ")[-1] if ch.section_path else "—"
                if (ch.document_id, sec) in seen:
                    continue
                seen.add((ch.document_id, sec))
                ex = self.excerpt(ch.text, [e.name.split(" (")[0] for e in request.entities if e.name] + [e.canonical_tag for e in request.entities if e.canonical_tag] or query.split()[:3])
                key = self.cite_chunk(result, ch, ex)
                rows.append([ch.document_id, sec, f"{ch.page_start}–{ch.page_end}" if ch.page_end and ch.page_end != ch.page_start else str(ch.page_start), ch.chunk_type, ch.match_reason])
                cits.append([key])
                self.statement(result, f"{ch.document_id} section '{sec}' (p.{ch.page_start}) covers the topic", [key])
            for s in sections[:6]:
                if (s.document_id, s.title) not in seen:
                    seen.add((s.document_id, s.title))
                    rows.append([s.document_id, s.title, f"{s.page_start}" + (f"–{s.page_end}" if s.page_end and s.page_end != s.page_start else ""), "section title", "title match"])
                    cits.append([])
            docs = self.knowledge.documents()
            if rows:
                result.blocks.append(TableBlock(id="sections", title=f"Where this is covered ({len(docs)} document(s) loaded)", columns=["Document", "Section", "Pages", "Type", "Match"], rows=rows, row_citations=cits))
            else:
                result.missing.append("no matching sections")
                result.blocks.append(self.callout("No section in the loaded documents matches the request.", "warning"))
            result.content["chunk_ids"] = [c.chunk_id for c in chunks[:10]]
            result.content["sections"] = [s.model_dump(mode="json") for s in sections[:8]]
            result.summary = f"{len(rows)} section(s) across {len(docs)} document(s)"
            result.confidence = self.confidence(0.7 if rows else 0.2, "section and passage matches with page numbers")
            return
        # follow: cross references, referenced documents, standing instructions
        self._references(request, context, chunks, result, query)

    def _references(self, request, context, chunks, result, query) -> None:
        section_paths = {c.section_path for c in chunks[:10] if c.section_path}
        xrefs = [x for x in (context.cross_references or self.knowledge.cross_references(None)) if any(x.source_section == sp or sp.startswith(x.source_section) for sp in section_paths)]
        step_refs = []
        for ch in chunks[:10]:
            for m in re.finditer(r"\b(?:refer|see|as per|described in|given in)\s+(chapter|section|annexure|appendix|sop|procedure)\s*(?:no\.?\s*)?([\dA-Z][\w.\-/]*)", ch.text, re.IGNORECASE):
                key = self.cite_chunk(result, ch, self.excerpt(ch.text, [m.group(0)]))
                step_refs.append([f"{m.group(1).title()} {m.group(2)}", ch.section_path.split(" > ")[-1] if ch.section_path else ch.document_id, f"p.{ch.page_start}", key])
        rows, cits = [], []
        for x in xrefs[:12]:
            rows.append([f"{x.target_kind} {x.target_number}", x.source_section.split(" > ")[-1], f"p.{x.source_page}" if x.source_page else "", x.evidence[:90]])
            cits.append([])
        for r in step_refs[:12]:
            rows.append([r[0], r[1], r[2], "in-text reference"])
            cits.append([r[3]])
        if rows:
            result.blocks.append(TableBlock(id="xrefs", title="Cross references from the matching sections", columns=["Refers to", "From section", "Page", "Evidence"], rows=rows, row_citations=cits))
        qtoks = set(re.findall(r"[a-z0-9]{3,}", query.lower())) - {"what", "which", "the", "are", "for", "before", "required", "this", "that", "with", "from", "and"}

        def real_reference(d) -> bool:
            # "IS 482" harvested from "...is 482 m3/h" is not a standard; a real citation keeps the prefix upper-case in the evidence
            m = re.match(r"^(IS|API|BS|EN|DIN|ASTM|ASME|ANSI|NFPA|OISD)\s+(\S+)$", d.reference_text)
            if m and not re.search(rf"\b{m.group(1)}[\s\-]{re.escape(m.group(2))}\b", d.evidence):
                return False
            return True

        drefs = [d for d in (context.document_references or self.knowledge.document_references(None))
                 if real_reference(d) and qtoks & set(re.findall(r"[a-z0-9]{3,}", (d.reference_text + " " + d.title + " " + d.document_type).lower()))]
        if drefs:
            result.blocks.append(TableBlock(id="docrefs", title="Referenced external documents", columns=["Reference", "Type", "Page", "In corpus?", "Evidence"],
                                            rows=[[d.reference_text, d.document_type, d.page or "", "yes" if d.present_in_corpus else "no", d.evidence[:100]] for d in drefs[:12]]))
        sis = context.standing_instructions or self.knowledge.standing_instructions(query)
        if sis:
            si_cits = []
            for s in sis[:12]:
                key = self.cite(result, Evidence(document_id=s.document_id, text=f"Standing instruction {s.number}: {s.title} ({s.status or 'listed'}{', chapter ' + s.incorporated_in_chapter if s.incorporated_in_chapter else ''})",
                                                 page=s.page, claim_id=f"si:{s.number}", source="rule"))
                si_cits.append([key])
                self.statement(result, f"Standing instruction {s.number} '{s.title}' is {s.status or 'listed'}", [key], kind="fact")
            result.blocks.append(TableBlock(id="sis", title="Standing instructions", columns=["Number", "Title", "Status", "Incorporated in chapter", "Page"],
                                            rows=[[s.number, s.title, s.status or "", s.incorporated_in_chapter or "", s.page or ""] for s in sis[:12]], row_citations=si_cits))
        if not rows and not drefs and not sis:
            result.missing.append("no cross references / standing instructions found")
            result.blocks.append(self.callout("No cross reference, referenced document or standing instruction is linked to the matching sections.", "info"))
        result.content["cross_references"] = rows
        result.content["standing_instructions"] = [s.number for s in sis[:12]]
        result.summary = f"{len(rows)} cross reference(s), {len(drefs)} referenced document(s), {len(sis)} standing instruction(s)"
        result.confidence = self.confidence(0.65 if (rows or drefs or sis) else 0.2, "document-structure data from the knowledge layer profile")

    @staticmethod
    def _tidy(value: str | None, limit: int = 40) -> str:
        """A document field fit to print.

        The profiler infers ``title`` and ``unit`` from page headers, and on a PDF without clean
        ones it lands a sentence fragment there. Printing that verbatim produced lines like
        "Steam Jet Ejectors is a unknown for unit now covers a range that previously required two
        ejectors...". A field that reads like prose rather than a label is dropped.
        """
        text = re.sub(r"\s+", " ", (value or "")).strip(" .,;:")
        if not text or len(text) > limit or len(text.split()) > 6:
            return ""
        return text

    def _scope(self, result: AgentResult) -> None:
        """What is loaded: the documents, their chapters and the standing instructions on top of them."""
        docs = self.knowledge.documents()
        rows = [[d.document_id, self._tidy(d.document_type, 24) or "—", self._tidy(d.unit) or "—",
                 d.revision or "—", d.effective_date or "—", d.total_pages or "—"] for d in docs]
        result.blocks.append(TableBlock(id="scope-documents", title="Documents in scope",
                                        columns=["Document", "Type", "Unit", "Revision", "Effective", "Pages"], rows=rows))
        for d in docs:
            kind = self._tidy(d.document_type, 24).replace("_", " ") or "document"
            unit = self._tidy(d.unit)
            pages = f"{d.total_pages} pages" if d.total_pages else "an unrecorded number of pages"
            key = self.cite(result, Evidence(document_id=d.document_id, page=1, source="rule",
                                             text=f"{d.document_id} — {kind}"
                                                  + (f", unit {unit}" if unit else "")
                                                  + f", revision {d.revision or 'n/a'}, {pages}", revision=d.revision))
            self.statement(result, f"{d.document_id} is a {kind}" + (f" for unit {unit}" if unit else "")
                           + f", {pages} long", [key])
        chapters = [c for c in (self.knowledge.chapters() or []) if not c.get("is_administrative")]
        if chapters:
            rows = [[c.get("number"), c.get("title", ""), f"{c.get('page_start')}–{c.get('page_end')}"] for c in chapters[:24]]
            result.blocks.append(TableBlock(id="scope-chapters", title=f"Engineering chapters ({len(chapters)} of {len(self.knowledge.chapters() or [])})",
                                            columns=["#", "Chapter", "Pages"], rows=rows))
        si = self.knowledge.standing_instructions(None)[:6]
        if si:
            result.blocks.append(TableBlock(id="scope-si", title="Standing instructions on record",
                                            columns=["Number", "Title", "Status"], rows=[[s.number, s.title[:70], s.status or "—"] for s in si]))
        result.content["documents"] = [d.model_dump(mode="json") for d in docs]
        result.summary = f"{len(docs)} document(s), {len(chapters)} engineering chapter(s), {len(si)} standing instruction(s)"
        result.confidence = self.confidence(0.9, "document structure from the knowledge layer profile")

    def _for_prior(self, request, context, step, result) -> None:
        proc_res = self.s.result_of("procedure") or self.s.result_of("safety")
        chunk_ids: list[str] = []
        if proc_res:
            for pid in proc_res.content.get("procedure_ids", []):
                p = self.knowledge.get_procedure(pid)
                if p:
                    chunk_ids += p.chunk_ids
        chunks = [c for cid in chunk_ids if (c := self.knowledge.get_chunk(cid))] or context.chunks
        self._references(request, context, chunks, result, request.original.text)

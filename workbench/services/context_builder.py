"""Phase 2 — Specialist retrieval along the cheapest route for the task type.

claims  : entity claims (+ tag twins) and conflicts. No text search, no embedder, no reranker.
graph   : relations up to 2 hops + the chunks that mention the entity (keyword only).
proc    : procedure index (title / applies_to / step tags) + standing instructions. No vectors.
hybrid  : BM25 + vectors + reranker over the chunk types and chapters that matter for the task,
          anchored on the resolved entities.
The chapter preferences come from the manual's structure (Operating Limits = ch.14, Upsets =
ch.17-18, Emergency/Restart = ch.19-20, Shutdown = ch.21, SOPs = ch.32, Safety = ch.27/30/31,
Instrument tags = ch.36); they are soft preferences, never hard filters.
"""
from __future__ import annotations

import logging
import re
import time

from workbench.config import WorkbenchConfig
from workbench.core.context import ContextPackage
from workbench.core.request import StructuredRequest, TaskType
from workbench.orchestration.router import RETRIEVAL_ROUTE
from workbench.services.index.builder import norm_alias

logger = logging.getLogger(__name__)

CHAPTER_PREFS: dict[TaskType, list[int]] = {
    TaskType.TROUBLESHOOTING: [17, 18, 14, 16, 7, 15],
    TaskType.EXPLANATION: [5, 6, 7, 2, 3],
    TaskType.SAFETY: [27, 30, 31, 23, 32, 19, 16],
    TaskType.LIMITS: [14, 16, 25, 3],
    TaskType.PROCEDURE: [16, 32, 12, 13, 20, 21, 15],
    TaskType.CROSS_DOCUMENT: [1, 16, 32, 34],
    TaskType.REPORT: [3, 5, 6, 14, 16, 25],
    TaskType.PLANNING: [16, 32, 14, 17, 27, 31],
}
CHUNK_TYPE_PREFS: dict[TaskType, list[str]] = {
    TaskType.TROUBLESHOOTING: ["upset", "safety", "control", "narrative", "procedure", "equipment"],
    TaskType.EXPLANATION: ["narrative", "equipment", "control", "safety"],
    TaskType.SAFETY: ["safety", "procedure", "narrative", "equipment", "upset"],
    TaskType.LIMITS: ["table", "equipment", "safety", "narrative", "control"],
    TaskType.CROSS_DOCUMENT: ["procedure", "narrative", "safety", "document_control", "equipment"],
    TaskType.REPORT: ["narrative", "equipment", "table", "control", "procedure", "safety"],
    TaskType.PLANNING: ["procedure", "safety", "upset", "equipment", "narrative"],
    TaskType.MULTI_HOP: ["narrative", "equipment", "control", "procedure"],
}
ABBREV_RE = re.compile(r"\b[A-Z]{2,6}\b")
# The manual numbers its plant sections in the tag itself (11-P-01 is atmospheric, 12-P-01 vacuum),
# so a scoped survey ("what equipment is in the vacuum section") filters on the tag prefix.
SCOPE_TAG_PREFIXES: dict[str, tuple[str, ...]] = {
    "vacuum section": ("12-",),
    "atmospheric section": ("11-",),
    "stabilizer section": ("11-C-05", "11-V-03", "11-E-19", "11-E-20", "11-E-25"),
}


#: Below this many plant-tagged items a document is probably not a plant manual, and its
#: inventory should be counted by whatever names it does use. Mirrors the retrieval agent.
THIN_INVENTORY = 5


class ContextBuilder:
    def __init__(self, knowledge, cfg: WorkbenchConfig) -> None:
        self.knowledge = knowledge
        self.cfg = cfg

    # ------------------------------------------------------------------ helpers
    def _entity_group(self, primary_uid: str, mention: str) -> list[str]:
        """Tagged twins that share the mention's name (11-P-01 and 11-PM-01 are both 'crude charge pump')."""
        twins: list[str] = []
        for e in self.knowledge.resolve_entity(mention, limit=6):
            if e.entity_uid == primary_uid:
                continue
            same_name = norm_alias(mention) in {norm_alias(a) for a in e.aliases} | {norm_alias(e.name.split(" (")[0])}
            if same_name:
                twins.append(e.entity_uid)
        return twins

    def _glossary_terms(self, text: str) -> list:
        out = []
        for abbr in dict.fromkeys(ABBREV_RE.findall(text)):
            out.extend(self.knowledge.glossary(abbr)[:1])
        return out[:6]

    # ------------------------------------------------------------------ routes
    def build(self, req: StructuredRequest) -> ContextPackage:
        t0 = time.time()
        route = RETRIEVAL_ROUTE.get(req.task_type, "hybrid")
        pkg = ContextPackage(route=route)
        pkg.glossary = self._glossary_terms(req.original.text)
        uids = req.entity_uids()
        for e in req.entities:
            if e.entity_uid:
                rec = self.knowledge.get_entity(e.entity_uid)
                if rec:
                    pkg.entities.append(rec)
                    pkg.entity_groups[e.entity_uid] = self._entity_group(e.entity_uid, e.mention)
        group_uids = list(dict.fromkeys(uids + [u for g in pkg.entity_groups.values() for u in g]))

        if route == "none":
            pkg.timing_ms = int((time.time() - t0) * 1000)
            return pkg

        if route == "inventory":
            # A survey question has no single subject: list the corpus instead of searching it.
            pkg.entity_counts = self.knowledge.entity_type_counts()
            # The default count is plant tags only (11-P-01, 080-H-001), which is right for a unit
            # manual and empty-to-misleading for a vendor catalogue or a standard, where equipment
            # is named by model number. The composer writes its prose from these counts, so a thin
            # count here is what produced "the catalogue covers one reactor" over a table listing
            # seventy-four items. Widen on the same rule the retrieval agent uses, so the sentence
            # and the table it sits above cannot disagree.
            if sum(pkg.entity_counts.values()) < THIN_INVENTORY:
                loose = self.knowledge.entity_type_counts(tagged_only=False, plant_only=False)
                if sum(loose.values()) > sum(pkg.entity_counts.values()):
                    pkg.entity_counts = loose
            limit = self.cfg.effort.inventory_limit
            prefixes = SCOPE_TAG_PREFIXES.get(req.scope or "")
            if prefixes:
                # plant-number prefixes are the manual's own section numbering (11- atmospheric,
                # 12- vacuum), so the whole list is pulled and filtered rather than truncated first
                rows = self.knowledge.list_entities(entity_type=req.subject_type, limit=2000)
                pkg.entities = [e for e in rows if (e.canonical_tag or "").startswith(prefixes)][:limit]
                pkg.entity_counts = {t: sum(1 for e in self.knowledge.list_entities(entity_type=t, limit=2000)
                                            if (e.canonical_tag or "").startswith(prefixes))
                                     for t in pkg.entity_counts}
                pkg.entity_counts = {t: n for t, n in pkg.entity_counts.items() if n}
                pkg.notes.append(f"scope filter: tags starting {', '.join(prefixes)} ({req.scope})")
            else:
                pkg.entities = self.knowledge.list_entities(entity_type=req.subject_type, limit=limit)
            pkg.sections = self.knowledge.sections(req.original.text, limit=6)
            pkg.standing_instructions = self.knowledge.standing_instructions(None)[:5]
            if req.subject_type and not pkg.entities:
                pkg.gaps.append(f"no tagged {req.subject_type.lower()} is recorded in the documents")
            pkg.notes.append(f"inventory: type={req.subject_type or 'all'}, limit={self.cfg.effort.inventory_limit}")
            pkg.timing_ms = int((time.time() - t0) * 1000)
            return pkg

        if route in ("claims", "hybrid") or req.task_type in (TaskType.MULTI_HOP, TaskType.PLANNING, TaskType.REPORT):
            for uid in group_uids:
                pkg.claims.extend(self.knowledge.entity_claims(uid))
            if uids and route == "claims":
                for uid in uids:
                    pkg.conflicts.extend(self.knowledge.conflicts_for(uid))
            if not group_uids and req.task_type in (TaskType.LOOKUP, TaskType.LIMITS, TaskType.COMPARISON, TaskType.CONFLICT, TaskType.PROVENANCE):
                # no entity resolved: try claims by subject text (e.g. "Basrah", "Light Naphtha")
                for m in req.unresolved_mentions[:3]:
                    pkg.claims.extend(self.knowledge.search_claims(subject=m, limit=40))
                if not pkg.claims:
                    pkg.gaps.append("no entity or claim subject could be resolved from the request")
            if req.parameter and pkg.claims:
                pkg.notes.append(f"parameter filter available: {req.parameter}")

        if route in ("graph",) or req.task_type in (TaskType.TROUBLESHOOTING, TaskType.EXPLANATION, TaskType.PLANNING, TaskType.REPORT):
            hops = self.cfg.effort.graph_hops + (1 if req.task_type == TaskType.MULTI_HOP else 0)
            for uid in group_uids:
                pkg.relations.extend(self.knowledge.entity_neighbors(uid, hops=hops))
            if route == "graph":
                for uid in group_uids[:2]:
                    pkg.chunks.extend(self.knowledge.chunks_for_entity(uid, limit=6))
                if not pkg.relations and not pkg.chunks:
                    pkg.gaps.append("no graph relationships found for the resolved entities")

        if route in ("proc",) or req.task_type in (TaskType.TROUBLESHOOTING, TaskType.SAFETY, TaskType.PLANNING, TaskType.REPORT):
            query = " ".join(x for x in [req.action or "", req.original.text] if x)
            seen: set[str] = set()
            for uid in (group_uids or [None]):
                for p in self.knowledge.procedures(entity_uid=uid, query=query, procedure_type=None, limit=self.cfg.effort.procedure_candidates):
                    if p.procedure_id not in seen:
                        seen.add(p.procedure_id)
                        pkg.procedures.append(p)
            pkg.procedures.sort(key=lambda p: -p.score)
            pkg.procedures = pkg.procedures[:8]
            pkg.standing_instructions = self.knowledge.standing_instructions(req.original.text)[:5]
            if route == "proc" and not pkg.procedures:
                pkg.gaps.append("no documented procedure matched the equipment and action")

        if route == "hybrid":
            k = self.cfg.retrieval.final_k
            chunk_types = CHUNK_TYPE_PREFS.get(req.task_type)
            chapters = CHAPTER_PREFS.get(req.task_type)
            query = self._query_text(req)
            pkg.chunks = self.knowledge.search_chunks(query, k=k, chunk_types=chunk_types, chapters=chapters, entity_uids=group_uids or None, rerank=self.cfg.retrieval.use_reranker)
            if req.task_type == TaskType.CROSS_DOCUMENT:
                pkg.sections = self.knowledge.sections(query, limit=8)
                pkg.document_references = self.knowledge.document_references(None)[:40]
                pkg.cross_references = self.knowledge.cross_references(None)[:40]
                pkg.standing_instructions = self.knowledge.standing_instructions(query)[:10] or self.knowledge.standing_instructions(None)[:10]
            if not pkg.chunks:
                pkg.gaps.append("no manual text matched the request")
            pkg.notes.append(f"hybrid retrieval: k={k}, types={chunk_types}, chapters={chapters}")

        pkg.timing_ms = int((time.time() - t0) * 1000)
        return pkg

    @staticmethod
    def _query_text(req: StructuredRequest) -> str:
        parts = [req.original.text]
        for e in req.entities:
            if e.name:
                parts.append(e.name.split(" (")[0])
            if e.canonical_tag:
                parts.append(e.canonical_tag)
        if req.symptom:
            parts.append(f"{req.symptom.variable} {req.symptom.direction}")
        if req.action:
            parts.append(req.action)
        return " ".join(parts)

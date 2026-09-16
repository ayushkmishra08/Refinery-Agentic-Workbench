"""CompositeKnowledgeService: several backends behind one KnowledgeService (main corpus + session uploads)."""
from __future__ import annotations

from typing import Any

from workbench.core.knowledge import ChunkRecord, EntityRecord, ProcedureRecord


class CompositeKnowledgeService:
    name = "composite"

    def __init__(self, primary, extras: list | None = None) -> None:
        self.primary = primary
        self.extras: list = list(extras or [])

    @property
    def services(self) -> list:
        return [self.primary, *self.extras]

    def add(self, service) -> None:
        self.extras.append(service)

    def remove(self, service) -> None:
        self.extras = [s for s in self.extras if s is not service]

    # ---- generic fan-out helpers -------------------------------------------------
    def _concat(self, method: str, *args: Any, **kw: Any) -> list:
        out: list = []
        for s in self.services:
            fn = getattr(s, method, None)
            if fn is None:
                continue
            out.extend(fn(*args, **kw))
        return out

    def _first(self, method: str, *args: Any, **kw: Any):
        for s in self.services:
            fn = getattr(s, method, None)
            if fn is None:
                continue
            r = fn(*args, **kw)
            if r:
                return r
        return None

    # ---- KnowledgeService ---------------------------------------------------------
    def documents(self):
        return self._concat("documents")

    def document_profile(self, document_id: str) -> dict:
        return self._first("document_profile", document_id) or {}

    def chapters(self, document_id: str | None = None) -> list[dict]:
        return self._concat("chapters", document_id)

    def resolve_entity(self, mention: str, limit: int = 5) -> list[EntityRecord]:
        rows = self._concat("resolve_entity", mention, limit)
        seen: set[str] = set()
        out = []
        for e in rows:
            if e.entity_uid in seen:
                continue
            seen.add(e.entity_uid)
            out.append(e)
        return out[:limit]

    def get_entity(self, entity_uid: str):
        return self._first("get_entity", entity_uid)

    def search_entities(self, query: str, entity_type: str | None = None, limit: int = 10):
        return self._concat("search_entities", query, entity_type, limit)[:limit]

    def list_entities(self, entity_type: str | None = None, tagged_only: bool = True, min_mentions: int = 1,
                      limit: int = 500, plant_only: bool = True):
        rows = self._concat("list_entities", entity_type, tagged_only, min_mentions, limit, plant_only)
        seen: set[str] = set()
        out = []
        for e in rows:
            if e.entity_uid in seen:
                continue
            seen.add(e.entity_uid)
            out.append(e)
        out.sort(key=lambda e: (-e.mention_count, e.canonical_tag or e.name))
        return out[:limit]

    def entity_type_counts(self, tagged_only: bool = True, min_mentions: int = 1, plant_only: bool = True) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.services:
            fn = getattr(s, "entity_type_counts", None)
            if fn is None:
                continue
            for k, v in fn(tagged_only, min_mentions, plant_only).items():
                counts[k] = counts.get(k, 0) + v
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def entity_claims(self, entity_uid: str, predicate: str | None = None, context: dict | None = None):
        return self._concat("entity_claims", entity_uid, predicate, context)

    def search_claims(self, subject=None, predicate=None, scenario=None, text=None, limit: int = 50):
        return self._concat("search_claims", subject, predicate, scenario, text, limit)[:limit]

    def conflicts_for(self, entity_uid: str, predicate: str | None = None):
        # cross-backend conflicts: merge claim lists and regroup
        claims = self.entity_claims(entity_uid, predicate)
        from collections import defaultdict

        from workbench.core.knowledge import ConflictRecord

        groups: dict[str, list] = defaultdict(list)
        for c in claims:
            if c.numeric_value is not None:
                groups[c.context_key].append(c)
        e = self.get_entity(entity_uid)
        out = []
        for key, rows in groups.items():
            if len(rows) < 2 or len({(c.document_id, c.page, c.chunk_id) for c in rows}) < 2:
                continue
            values = {round(c.numeric_value, 4) for c in rows}
            out.append(ConflictRecord(subject=e.name if e else entity_uid, predicate=rows[0].predicate, context_key=key, claims=rows,
                                      status="potential_conflict" if len(values) > 1 else "corroborated"))
        return out

    def entity_neighbors(self, entity_uid: str, rel_types=None, direction: str = "both", hops: int = 1):
        return self._concat("entity_neighbors", entity_uid, rel_types, direction, hops)

    def procedures(self, entity_uid=None, query=None, procedure_type=None, limit: int = 10) -> list[ProcedureRecord]:
        rows = self._concat("procedures", entity_uid, query, procedure_type, limit)
        rows.sort(key=lambda p: -p.score)
        return rows[:limit]

    def get_procedure(self, procedure_id: str):
        return self._first("get_procedure", procedure_id)

    def search_chunks(self, query: str, k: int = 8, chunk_types=None, chapters=None, document_ids=None, entity_uids=None, rerank: bool = False) -> list[ChunkRecord]:
        rows = self._concat("search_chunks", query, k, chunk_types, chapters, document_ids, entity_uids, rerank)
        # uploads first when they matched, then by score
        rows.sort(key=lambda c: (-c.score))
        return rows[:k]

    def get_chunk(self, chunk_id: str):
        return self._first("get_chunk", chunk_id)

    def chunks_for_entity(self, entity_uid: str, limit: int = 20):
        return self._concat("chunks_for_entity", entity_uid, limit)[:limit]

    def sections(self, query: str, limit: int = 10):
        return self._concat("sections", query, limit)[:limit]

    def glossary(self, term: str):
        return self._concat("glossary", term)

    def document_references(self, query: str | None = None):
        return self._concat("document_references", query)

    def standing_instructions(self, query: str | None = None):
        return self._concat("standing_instructions", query)

    def cross_references(self, section_query: str | None = None):
        return self._concat("cross_references", section_query)

    def stats(self) -> dict:
        return {"backends": [getattr(s, "name", "?") for s in self.services],
                "details": [s.stats() for s in self.services if hasattr(s, "stats")]}

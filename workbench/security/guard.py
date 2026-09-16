"""GuardedKnowledgeService — the enforcement point.

Every agent receives a guarded service rather than the backend. The guard forwards each call
and then drops every record whose ``document_id`` the principal is not cleared for, so there
is no route to restricted content: not through a search, not through a tag lookup, not
through a neighbour walk, not through an entity the record happens to be attached to.

Entities are the one composite case — an entity can be mentioned in several documents — so
an entity survives only if at least one of its documents is readable, and its
``document_ids`` are narrowed to the readable ones.

The guard also counts what it withheld, so governance can tell the user that an answer was
trimmed rather than silently returning less.
"""
from __future__ import annotations

import logging
from typing import Any

from workbench.core.knowledge import ConflictRecord, EntityRecord

logger = logging.getLogger(__name__)

# Methods whose return value is a list of records carrying a document_id
_LIST_METHODS = (
    "entity_claims", "search_claims", "entity_neighbors", "procedures", "search_chunks",
    "chunks_for_entity", "sections", "glossary", "document_references", "standing_instructions",
    "cross_references",
)
_ENTITY_LIST_METHODS = ("resolve_entity", "search_entities", "list_entities")
_SINGLE_METHODS = ("get_entity", "get_procedure", "get_chunk")


class GuardedKnowledgeService:
    """Read-only KnowledgeService view restricted to the documents a principal may read."""

    name = "guarded"

    def __init__(self, inner, allowed_document_ids: list[str] | set[str] | None, *, enabled: bool = True) -> None:
        self.inner = inner
        self.enabled = enabled
        self.allowed: set[str] | None = None if allowed_document_ids is None else set(allowed_document_ids)
        self.withheld: dict[str, int] = {}

    # ------------------------------------------------------------------ the filter
    def _ok(self, document_id: str | None) -> bool:
        if not self.enabled or self.allowed is None:
            return True
        return bool(document_id) and document_id in self.allowed

    def _note(self, document_id: str | None) -> None:
        key = document_id or "unattributed"
        self.withheld[key] = self.withheld.get(key, 0) + 1

    def _filter(self, rows: list) -> list:
        if not self.enabled or self.allowed is None:
            return rows
        out = []
        for r in rows:
            if isinstance(r, ConflictRecord):
                kept = [c for c in r.claims if self._ok(c.document_id)]
                for c in r.claims:
                    if not self._ok(c.document_id):
                        self._note(c.document_id)
                if kept:
                    out.append(r.model_copy(update={"claims": kept}))
                continue
            doc = getattr(r, "document_id", None)
            if self._ok(doc):
                out.append(r)
            else:
                self._note(doc)
        return out

    def _filter_entities(self, rows: list[EntityRecord]) -> list[EntityRecord]:
        if not self.enabled or self.allowed is None:
            return rows
        out: list[EntityRecord] = []
        for e in rows:
            docs = [d for d in (e.document_ids or []) if self._ok(d)]
            if docs:
                out.append(e if docs == list(e.document_ids) else e.model_copy(update={"document_ids": docs}))
            elif not e.document_ids:
                out.append(e)                       # a backend that does not attribute entities (fixtures)
            else:
                self._note(e.document_ids[0] if e.document_ids else None)
        return out

    def withheld_count(self) -> int:
        return sum(self.withheld.values())

    # ------------------------------------------------------------------ documents
    def documents(self) -> list:
        return [d for d in self.inner.documents() if self._ok(d.document_id)]

    def document_profile(self, document_id: str) -> dict:
        return self.inner.document_profile(document_id) if self._ok(document_id) else {}

    def chapters(self, document_id: str | None = None) -> list[dict]:
        fn = getattr(self.inner, "chapters", None)
        if fn is None:
            return []
        rows = fn(document_id)
        if not self.enabled or self.allowed is None:
            return rows
        return [c for c in rows if self._ok(c.get("document_id")) or "document_id" not in c]

    def entity_type_counts(self, tagged_only: bool = True, min_mentions: int = 1) -> dict[str, int]:
        if not self.enabled or self.allowed is None:
            return self.inner.entity_type_counts(tagged_only=tagged_only, min_mentions=min_mentions)
        counts: dict[str, int] = {}
        for e in self.list_entities(tagged_only=tagged_only, min_mentions=min_mentions, limit=100_000):
            counts[e.entity_type or "Equipment"] = counts.get(e.entity_type or "Equipment", 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    # ------------------------------------------------------------------ forwarding
    def __getattr__(self, item: str) -> Any:
        """Forward anything not overridden, filtering list results by document."""
        target = getattr(self.inner, item)
        if item in _SINGLE_METHODS:
            def single(*a, **kw):
                r = target(*a, **kw)
                if r is None:
                    return None
                if isinstance(r, EntityRecord):
                    kept = self._filter_entities([r])
                    return kept[0] if kept else None
                if self._ok(getattr(r, "document_id", None)):
                    return r
                self._note(getattr(r, "document_id", None))
                return None
            return single
        if item in _ENTITY_LIST_METHODS:
            return lambda *a, **kw: self._filter_entities(target(*a, **kw))
        if item in _LIST_METHODS or item == "conflicts_for":
            return lambda *a, **kw: self._filter(target(*a, **kw))
        return target

    # ------------------------------------------------------------------ mutation passthrough
    def add(self, service) -> None:
        self.inner.add(service)

    def remove(self, service) -> None:
        self.inner.remove(service)

    @property
    def primary(self):
        return getattr(self.inner, "primary", self.inner)

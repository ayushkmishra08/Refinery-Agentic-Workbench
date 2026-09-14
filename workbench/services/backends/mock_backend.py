"""Fixture-backed KnowledgeService. Reads workbench/fixtures/*.json.
Lets every agent be developed and unit-tested with no Neo4j / Ollama / GPU."""
from __future__ import annotations

import json
from pathlib import Path


class MockKnowledgeBackend:
    def __init__(self, fixtures_dir: Path):
        self.dir = fixtures_dir
        self._cache: dict[str, list[dict] | dict] = {}

    def _load(self, name: str):
        if name not in self._cache:
            p = self.dir / f"{name}.json"
            self._cache[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        return self._cache[name]

    def resolve_entity(self, mention: str) -> list[dict]:
        m = mention.upper().replace(" ", "-")
        return [e for e in self._load("entities") if m in (e.get("canonical_tag", ""), *e.get("aliases", []))]

    def entity_claims(self, entity_uid: str, context: dict | None = None) -> list[dict]:
        rows = [c for c in self._load("claims") if c.get("entity_uid") == entity_uid]
        for k, v in (context or {}).items():
            rows = [c for c in rows if c.get(k) in (None, v)]
        return rows

    def entity_neighbors(self, entity_uid, rel_types=None, hops=1):
        return [r for r in self._load("relationships")
                if entity_uid in (r.get("source_uid"), r.get("target_uid"))
                and (not rel_types or r.get("type") in rel_types)]

    def procedures_for(self, entity_uid=None, query=None):
        return [p for p in self._load("procedures")
                if (entity_uid is None or entity_uid in p.get("applies_to", []))]

    def procedure_steps(self, procedure_id):
        return next((p["steps"] for p in self._load("procedures") if p["procedure_id"] == procedure_id), [])

    def search_chunks(self, query, k=8, chunk_types=None):
        q = query.lower()
        hits = [c for c in self._load("chunks") if q.split()[0] in c.get("text", "").lower()]
        return hits[:k]

    def conflicts_for(self, entity_uid):
        return [c for c in self._load("conflicts") if c.get("entity_uid") == entity_uid]

    def glossary(self, term):
        return [g for g in self._load("glossary") if term.lower() in g.get("term", "").lower()]

    def document_profile(self, document_id):
        return next((d for d in self._load("documents") if d["document_id"] == document_id), {})

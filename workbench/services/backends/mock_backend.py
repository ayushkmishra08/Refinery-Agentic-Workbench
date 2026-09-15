"""MockKnowledgeBackend: a tiny hand-written index (workbench/fixtures/*.json) for machines without artefacts.

It reuses IndexStore so retrieval behaves exactly like the real backend, only the data is small.
"""
from __future__ import annotations

import json
from pathlib import Path

from workbench.core.knowledge import (
    ChunkRecord,
    ClaimRecord,
    DocumentInfo,
    EntityRecord,
    ProcedureRecord,
    RelationRecord,
    StepRecord,
)
from workbench.services.index.builder import DocumentIndex, norm_alias
from workbench.services.index.store import IndexStore


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def build_mock_index(fixtures_dir: Path) -> DocumentIndex:
    docs = _load(fixtures_dir / "documents.json")
    doc_id = docs[0]["document_id"] if docs else "fixture"
    info = DocumentInfo(document_id=doc_id, title=doc_id, document_type="operating_manual", revision=(docs[0].get("revision") if docs else None),
                        total_pages=(docs[0].get("pages") if docs else None), unit=(docs[0].get("unit") if docs else None))
    entities: dict[str, EntityRecord] = {}
    alias_index: dict[str, list[str]] = {}
    for e in _load(fixtures_dir / "entities.json"):
        rec = EntityRecord(entity_uid=e["entity_uid"], name=e["name"], canonical_tag=e.get("canonical_tag"), entity_type=e.get("entity_type"),
                           aliases=e.get("aliases", []), document_ids=[doc_id], mention_count=e.get("mention_count", 1))
        entities[rec.entity_uid] = rec
        for a in [rec.name, *(rec.aliases or []), *( [rec.canonical_tag] if rec.canonical_tag else [])]:
            alias_index.setdefault(norm_alias(a), []).append(rec.entity_uid)
    claims = []
    for c in _load(fixtures_dir / "claims.json"):
        ctx = "|".join([c.get("predicate", ""), (c.get("parameter_role") or ""), (c.get("location") or ""), (c.get("operating_mode") or ""),
                        (c.get("scenario") or ""), (c.get("pressure_basis") or ""), (c.get("qualifier") or "")]).lower()
        claims.append(ClaimRecord(claim_id=c["claim_id"], subject_uid=c.get("entity_uid"), subject=entities.get(c.get("entity_uid"), EntityRecord(entity_uid="", name=c.get("subject", "?"))).name,
                                  predicate=c["predicate"], value=str(c["value"]), numeric_value=float(c["value"]) if isinstance(c["value"], (int, float)) else None,
                                  unit=c.get("unit"), parameter_role=c.get("parameter_role"), location=c.get("location"), scenario=c.get("scenario"),
                                  pressure_basis=c.get("pressure_basis"), context_key=ctx, document_id=c.get("document_id", doc_id), page=c.get("page"),
                                  chunk_id=c.get("chunk_id"), evidence=c.get("evidence", ""), source=c.get("source", "table")))
    relations = [RelationRecord(source_uid=r["source_uid"], source_name=entities[r["source_uid"]].name if r["source_uid"] in entities else r["source_uid"],
                                target_uid=r["target_uid"], target_name=entities[r["target_uid"]].name if r["target_uid"] in entities else r["target_uid"],
                                rel_type=r["type"], document_id=doc_id, page=r.get("page"), evidence=r.get("evidence", ""), source=r.get("source", "rule"))
                 for r in _load(fixtures_dir / "relationships.json")]
    procedures = {}
    for p in _load(fixtures_dir / "procedures.json"):
        procedures[p["procedure_id"]] = ProcedureRecord(procedure_id=p["procedure_id"], title=p["title"], procedure_type=p.get("procedure_type", "procedure"),
                                                        document_id=doc_id, page_start=p.get("page_start"), page_end=p.get("page_end"), applies_to=p.get("applies_to", []),
                                                        section_path=p.get("section_path", ""),
                                                        steps=[StepRecord(sequence=s.get("sequence", i + 1), text=s["text"], page=s.get("page"), tags=s.get("tags", []))
                                                               for i, s in enumerate(p.get("steps", []))])
    chunks = {}
    chunk_entities = {}
    for c in _load(fixtures_dir / "chunks.json"):
        chunks[c["chunk_id"]] = ChunkRecord(chunk_id=c["chunk_id"], document_id=doc_id, chunk_type=c.get("chunk_type", "narrative"), text=c["text"],
                                            page_start=c.get("page"), page_end=c.get("page"), section_path=c.get("section_path", ""), chapter_number=c.get("chapter_number"))
        chunk_entities[c["chunk_id"]] = c.get("entity_uids", [])
    return DocumentIndex(info=info, entities=entities, alias_index=alias_index, claims=claims, relations=relations, procedures=procedures,
                         chunks=chunks, chunk_order=list(chunks), chunk_entities=chunk_entities)


class MockKnowledgeBackend(IndexStore):
    name = "mock"

    def __init__(self, fixtures_dir: Path) -> None:
        super().__init__([build_mock_index(fixtures_dir)], embeddings=None, use_vectors=False, use_reranker=False)

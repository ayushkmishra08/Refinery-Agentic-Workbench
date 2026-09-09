"""Identity probe: two synthetic documents mention the same vessel as "11-V-02" and
"11 V 02" with different design pressures.  Expected end state in Neo4j:

    ONE Entity node for the vessel, two HAS_ENTITY links (one per document),
    two MENTIONS (one per chunk), two Claims linked by CONFLICTS_WITH.

Usage: python scripts/probe_identity.py [--keep]
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chunker import Chunk  # noqa: E402
from src.config import load_config  # noqa: E402
from src.entity_resolver import EntityResolver  # noqa: E402
from src.extractor import OllamaExtractor  # noqa: E402
from src.graph import GraphInserter  # noqa: E402
from src.memory import Neo4jMemory  # noqa: E402
from src.validator import ExtractionValidator  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
DOCS = ["_probe_docA", "_probe_docB"]

SCENARIOS = {
    "_probe_docA": {
        "chunk_id": "_probe_docA_chunk_0001", "page": 12,
        "text": "The crude desalter 11-V-02 receives preheated crude from 11-P-01A/B. "
                "Design pressure of 11-V-02 is 14.0 kg/cm2g. The desalter is protected by PSV-1102.",
        "json": {
            "entities": [
                {"name": "11-V-02", "canonical_name": "Crude Desalter 11-V-02", "entity_type": "desalter",
                 "domain": "equipment", "evidence": "The crude desalter 11-V-02 receives preheated crude", "page": 12, "confidence": 0.9},
                {"name": "11-P-01A/B", "canonical_name": "Crude charge pumps", "entity_type": "pump", "domain": "equipment",
                 "evidence": "receives preheated crude from 11-P-01A/B", "page": 12, "confidence": 0.85},
                {"name": "Crude Desalter", "canonical_name": "Crude Desalter", "entity_type": "desalter", "domain": "equipment",
                 "evidence": "The crude desalter 11-V-02", "page": 12, "confidence": 0.7},
            ],
            "relationships": [
                {"subject": "11-V-02", "predicate": "RECEIVES_FROM", "object": "11-P-01A/B",
                 "evidence": "11-V-02 receives preheated crude from 11-P-01A/B", "page": 12, "confidence": 0.85},
                {"subject": "11-V-02", "predicate": "PROTECTED_BY", "object": "PSV-1102",
                 "evidence": "The desalter is protected by PSV-1102", "page": 12, "confidence": 0.8},
            ],
            "claims": [
                {"subject": "11-V-02", "predicate": "design_pressure", "value": "14.0", "unit": "kg/cm2g",
                 "evidence": "Design pressure of 11-V-02 is 14.0 kg/cm2g", "page": 12, "confidence": 0.9},
            ],
        },
    },
    "_probe_docB": {
        "chunk_id": "_probe_docB_chunk_0007", "page": 88,
        "text": "Desalter 11 V 02 (crude desalter vessel): design pressure 15.5 kg/cm2g, operating temperature 130 degC. "
                "The desalter feeds the preheat exchanger 11-E-05.",
        "json": {
            "entities": [
                {"name": "11 V 02", "canonical_name": "Desalter vessel", "entity_type": "vessel", "domain": "equipment",
                 "evidence": "Desalter 11 V 02 (crude desalter vessel)", "page": 88, "confidence": 0.88},
                {"name": "Crude Desalter", "canonical_name": "Crude Desalter", "entity_type": "desalter", "domain": "equipment",
                 "evidence": "crude desalter vessel", "page": 88, "confidence": 0.7},
                {"name": "Desalter Mixing Valve", "canonical_name": "Desalter Mixing Valve", "entity_type": "mixing_valve",
                 "domain": "valve", "evidence": "Desalter 11 V 02", "page": 88, "confidence": 0.6},
            ],
            "relationships": [
                {"subject": "11 V 02", "predicate": "FEEDS", "object": "11-E-05",
                 "evidence": "The desalter feeds the preheat exchanger 11-E-05", "page": 88, "confidence": 0.8},
            ],
            "claims": [
                {"subject": "11 V 02", "predicate": "design_pressure", "value": "15.5", "unit": "kg/cm2g",
                 "evidence": "design pressure 15.5 kg/cm2g", "page": 88, "confidence": 0.85},
                {"subject": "11 V 02", "predicate": "operating_temperature", "value": "130", "unit": "degC",
                 "evidence": "operating temperature 130 degC", "page": 88, "confidence": 0.85},
            ],
        },
    },
}

config = load_config()
memory = Neo4jMemory(config)
memory.connect()
memory.setup_schema()
extractor = OllamaExtractor(config)
validator = ExtractionValidator(config, memory)

for doc_id, sc in SCENARIOS.items():
    memory.upsert_document({"document_id": doc_id, "title": doc_id, "doc_type": "operating_manual", "revision": "",
                            "status": "", "plant": "11", "unit": "CDU-II", "source_filename": f"{doc_id}.pdf", "total_pages": 100})
    chunk = Chunk(chunk_id=sc["chunk_id"], document_id=doc_id, sequence=1, page_start=sc["page"], page_end=sc["page"],
                  section_path="Desalter", parent_heading="Desalter", text=sc["text"])
    chunk.embedding = [0.02] * config.embedding.dimensions
    memory.upsert_chunk({"chunk_id": chunk.chunk_id, "document_id": doc_id, "page_start": sc["page"], "page_end": sc["page"],
                         "section": "Desalter", "sequence": 1, "embedding": chunk.embedding, "text": chunk.text,
                         "contains_table": False, "table_ids": []})
    extraction = extractor._parse_extraction(json.dumps(sc["json"]), chunk, doc_id)
    validation = validator.validate(extraction, chunk)
    print(f"\n{doc_id}: parsed e/r/c={len(extraction.entities)}/{len(extraction.relationships)}/{len(extraction.claims)}; "
          f"validation errors={validation.failed_checks}")
    for issue in validation.issues:
        print(f"   [{issue.severity.value}] {issue.category.value}: {issue.message[:100]}")
    resolver = EntityResolver(config, memory, None, plant="11", unit="CDU-II")
    counts = GraphInserter(memory, resolver).insert_extraction(extraction, validation, chunk_embedding=chunk.embedding)
    print("  inserted:", counts)
    for e in extraction.entities:
        print(f"  resolved {e.name!r} -> uid={e.uid} tag={e.canonical_tag} method={e.resolution_method} type_raw={e.entity_type_raw}")

print("\n================ GRAPH STATE ================")
with memory.session() as s:
    q = "MATCH (e:Entity) WHERE any(d IN e.document_ids WHERE d STARTS WITH '_probe_') RETURN e ORDER BY e.canonical_tag, e.name"
    for r in s.run(q):
        p = dict(r["e"])
        print("ENTITY", json.dumps({k: p.get(k) for k in ("name", "canonical_tag", "uid", "entity_type", "entity_type_raw",
                                                           "document_ids", "mention_count", "aliases", "is_stub", "plant", "unit")}, default=str))
    print("\nvessel 11-V-02 node count:",
          s.run("MATCH (e:Entity {canonical_tag: '11-V-02'}) RETURN count(e) AS n").single()["n"])
    print("HAS_ENTITY -> 11-V-02:",
          s.run("MATCH (d:Document)-[h:HAS_ENTITY]->(e:Entity {canonical_tag:'11-V-02'}) RETURN d.document_id AS d, h.page AS page, h.confidence AS c ORDER BY d").data())
    print("MENTIONS -> 11-V-02:",
          s.run("MATCH (ch:Chunk)-[m:MENTIONS]->(e:Entity {canonical_tag:'11-V-02'}) RETURN ch.chunk_id AS ch, m.confidence AS c, left(m.evidence,40) AS ev ORDER BY ch").data())
    print("CLAIMS on 11-V-02:",
          s.run("MATCH (e:Entity {canonical_tag:'11-V-02'})-[:HAS_CLAIM]->(c:Claim) RETURN c.predicate AS p, c.value AS v, c.unit AS u, c.document_id AS d, c.page AS pg ORDER BY p, d").data())
    print("CONFLICTS_WITH:",
          s.run("MATCH (a:Claim)-[k:CONFLICTS_WITH]-(b:Claim) WHERE a.document_id STARTS WITH '_probe_' RETURN DISTINCT a.value AS a, b.value AS b, k.cross_document AS cross").data())
    print("HAS_TRAIN:",
          s.run("MATCH (p:Entity)-[:HAS_TRAIN]->(c:Entity) WHERE p.canonical_tag STARTS WITH '11-P-01' RETURN p.canonical_tag AS parent, collect(c.canonical_tag) AS children").data())
    print("typed rels:",
          s.run("MATCH (a:Entity)-[r]->(b:Entity) WHERE r.predicate IS NOT NULL AND r.document_id STARTS WITH '_probe_' "
                "RETURN a.name AS s, type(r) AS t, b.name AS o, r.document_id AS d, r.page AS pg").data())
    print("SAME_AS:",
          s.run("MATCH (a:Entity)-[r:SAME_AS]->(b:Entity) WHERE a.document_id STARTS WITH '_probe_' "
                "RETURN a.name AS a, a.document_id AS da, b.name AS b, b.document_id AS db, r.confidence AS c, r.method AS m").data())
    if "--keep" not in sys.argv:
        s.run("MATCH (n) WHERE n.document_id STARTS WITH '_probe_' OR any(d IN coalesce(n.document_ids, []) WHERE d STARTS WITH '_probe_') DETACH DELETE n")
        print("\ncleaned probe nodes; remaining probe nodes:",
              s.run("MATCH (n) WHERE n.document_id STARTS WITH '_probe_' RETURN count(n) AS c").single()["c"])
memory.close()
extractor.close()

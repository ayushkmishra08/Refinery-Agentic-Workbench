"""Probe: run a synthetic extraction through validator + GraphInserter and dump the
resulting Neo4j nodes/relationships with all their properties, then clean up.

Usage: python scripts/probe_graph_insert.py [--keep]
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_layer.chunker import Chunk  # noqa: E402
from knowledge_layer.config import load_config  # noqa: E402
from knowledge_layer.extractor import OllamaExtractor  # noqa: E402
from knowledge_layer.graph import GraphInserter  # noqa: E402
from knowledge_layer.memory import Neo4jMemory  # noqa: E402
from knowledge_layer.validator import ExtractionValidator  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
DOC = "_probe_doc"

chunk = Chunk(
    chunk_id=f"{DOC}_chunk_0001", document_id=DOC, sequence=1, page_start=15, page_end=15,
    section_path="3 DESIGN BASIS > 3.2 Equipment", parent_heading="3.2 Equipment",
    text=("P-101 Crude Charge Pump handles crude oil from T-101. P-101 takes suction from crude "
          "storage tank T-101. Design pressure of P-101 is 15 barg. The pump is driven by motor M-101. "
          "Design temperature of E-201 is 350 degC."),
)
raw = json.dumps({
    "entities": [
        {"name": "P-101", "canonical_name": "Crude Charge Pump P-101", "entity_type": "pump", "domain": "equipment",
         "aliases": ["P-101A"], "description": "Crude oil charge pump",
         "evidence": "P-101 Crude Charge Pump handles crude oil from T-101", "page": 15, "confidence": 0.9},
        {"name": "T-101", "canonical_name": "Crude storage tank T-101", "entity_type": "tank", "domain": "equipment",
         "evidence": "crude storage tank T-101", "page": 15, "confidence": None},
    ],
    "relationships": [
        {"subject": "P-101", "predicate": "SUCTION_FROM", "object": "T-101",
         "evidence": "P-101 takes suction from crude storage tank T-101", "page": 15, "confidence": 0.85},
        {"subject": "P-101", "predicate": "DRIVEN_BY", "object": "M-101",
         "evidence": "The pump is driven by motor M-101", "page": 15, "confidence": 0.8},
    ],
    "claims": [
        {"subject": "P-101", "predicate": "design_pressure", "value": "15", "unit": "barg",
         "evidence": "Design pressure of P-101 is 15 barg", "page": 15, "confidence": 0.9, "is_from_table": False},
        {"subject": "E-201", "predicate": "design_temperature", "value": "350", "unit": "degC",
         "evidence": "Design temperature of E-201 is 350 degC", "page": None, "confidence": "0.7"},
        {"subject": "P-101", "predicate": "design_pressure", "value": "99", "unit": "degC",
         "evidence": "this evidence is not in the chunk at all", "page": 15, "confidence": 0.9},
    ],
    "references": ["P&ID-CDU-001"],
})

config = load_config()
extractor = OllamaExtractor(config)
extraction = extractor._parse_extraction(raw, chunk, DOC)
print(f"parsed: {len(extraction.entities)} entities, {len(extraction.relationships)} rels, {len(extraction.claims)} claims")

memory = Neo4jMemory(config)
memory.connect()
memory.setup_schema()
memory.upsert_document({"document_id": DOC, "title": "Probe", "doc_type": "operating_manual", "revision": "",
                        "status": "", "plant": "", "unit": "", "source_filename": "probe.pdf", "total_pages": 1})
chunk.embedding = [0.01] * config.embedding.dimensions
memory.upsert_chunk({"chunk_id": chunk.chunk_id, "document_id": DOC, "page_start": 15, "page_end": 15,
                     "section": chunk.section_path, "sequence": 1, "embedding": chunk.embedding,
                     "text": chunk.text, "contains_table": False, "table_ids": []})

validator = ExtractionValidator(config, memory)
validation = validator.validate(extraction, chunk)
print(f"validation passed={validation.passed} errors={validation.failed_checks} warnings={validation.warning_checks}")
for issue in validation.issues:
    print(f"   [{issue.severity.value}] {issue.category.value}: {issue.message[:110]}")

counts = GraphInserter(memory).insert_extraction(extraction, validation)
print("inserted:", counts)

with memory.session() as s:
    print("\n--- nodes ---")
    for r in s.run("MATCH (n) WHERE n.document_id = $d RETURN labels(n) AS l, properties(n) AS p ORDER BY l[0]", d=DOC):
        p = dict(r["p"])
        p.pop("embedding", None)
        print(r["l"], json.dumps(p, default=str)[:400])
    print("\n--- relationships ---")
    for r in s.run("MATCH (a)-[r]->(b) WHERE a.document_id = $d OR b.document_id = $d "
                   "RETURN labels(a)[0] AS a, coalesce(a.name, a.chunk_id, a.document_id, a.uid) AS an, type(r) AS t, "
                   "properties(r) AS p, labels(b)[0] AS b, coalesce(b.name, b.chunk_id, b.uid) AS bn", d=DOC):
        print(f"({r['a']} {r['an']}) -[{r['t']} {json.dumps(dict(r['p']), default=str)[:150]}]-> ({r['b']} {r['bn']})")
    if "--keep" not in sys.argv:
        s.run("MATCH (n) WHERE n.document_id = $d DETACH DELETE n", d=DOC)
        print("\ncleaned; remaining nodes:", s.run("MATCH (n) RETURN count(n) AS c").single()["c"])
memory.close()

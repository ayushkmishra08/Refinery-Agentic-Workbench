# Mock knowledge fixtures

Hand-written JSON that mirrors the shapes the knowledge layer writes to Neo4j,
so agents can be developed before extraction completes. Keep them tiny and real
(values from the CDU manual, with page numbers). Files: entities.json, claims.json,
relationships.json, procedures.json, chunks.json, conflicts.json, glossary.json, documents.json.
Regenerate from the real graph later with `scripts/export_fixtures.py` (to be written).

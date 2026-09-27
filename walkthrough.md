# Refinery Knowledge Layer — Walkthrough

*Rewritten 2026-09-26 against [`knowledge_layer/pipeline.py`](knowledge_layer/pipeline.py). This file covers the
knowledge-layer build only; the agentic workbench on top of it is described in [`README.md`](README.md) and
[`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md). Installation (Python, Neo4j in `neo4j-data/`, Ollama) is in
[`SETUP.md`](SETUP.md).*

## What it does

`python -m knowledge_layer` (banner `Refinery Knowledge Layer v0.3.0`) finds every PDF in `data/raw/` and runs each
one through the phases below. Every phase is checkpointed per document in `data/checkpoints/<doc>_pipeline.json`,
so an interrupted run resumes where it stopped; Phase 8 is also checkpointed per chunk. Six documents are in
`data/raw/` today: CDU operating manual, API610 pump operation manual, API560 comparison, Bulletin-5EH steam-jet
ejectors, Crude desalter and ESBWR.

| Phase | What happens | Module(s) | Output |
|---|---|---|---|
| 1 Parse | Docling parse, then parse validation; a failed validation stops the document here | [`parser.py`](knowledge_layer/parser.py), [`parse_validation.py`](knowledge_layer/parse_validation.py) | `data/parsed/<doc>/` (`parsed_document.json`, `tables.json`, `parse_report.md`, images) |
| 2 Table classification | 18 table classes; tables that enter extraction are reconstructed (merged cells). Runs *before* normalization so furniture tables are filtered by class | [`table_classifier.py`](knowledge_layer/table_classifier.py), [`table_reconstructor.py`](knowledge_layer/table_reconstructor.py) | in memory |
| 3 Normalize | furniture removal (headers, running titles, approval blocks, figure placeholders), TOC-based chapter structure, sections, procedure detection | [`normalizer.py`](knowledge_layer/normalizer.py), [`structure.py`](knowledge_layer/structure.py) | `data/normalized/<doc>_normalized.json` |
| 4 Document profile | document type, revision, chapters, abbreviations, referenced documents, cross references, standing instructions | [`document_profile.py`](knowledge_layer/document_profile.py) | `data/knowledge/<doc>/document_profile.json` |
| 5 Glossary | terms and abbreviations (tables + inline) | [`glossary_builder.py`](knowledge_layer/glossary_builder.py) | `data/knowledge/<doc>/glossary.json` |
| 6 Chunk | canonical text once, typed chunks (`procedure`, `table`, `specification`, `safety`, `upset`, `equipment`, `control`, `narrative`, `document_control`, `toc`) with an `is_engineering` flag | [`chunker.py`](knowledge_layer/chunker.py) | `data/knowledge/<doc>/chunks.json` |
| 7 Neo4j setup | schema/indexes, `Document` node, glossary `Term` nodes | [`memory.py`](knowledge_layer/memory.py) | Neo4j |
| 7a Document graph | chapters, sections, procedures and steps (`HAS_STEP`, `NEXT`), cross references, standing instructions, referenced documents | [`document_graph.py`](knowledge_layer/document_graph.py) | Neo4j |
| 7b Embeddings | `BAAI/bge-small-en-v1.5` (384-dim, CPU), cached on disk, then written to `Chunk` nodes (vector index `chunk_embeddings`) | [`embedder.py`](knowledge_layer/embedder.py) | `data/knowledge/<doc>/chunk_embeddings.json`, Neo4j |
| 8 Extraction | per chunk: LLM pass 1 (and a relationship-only pass 2) when Ollama is available and the chunk is an engineering chunk; then **always** the deterministic passes: rule relationships, specification and prose claims, table claims. Then the validator, the `GraphInserter` and the ontology update | [`extractor.py`](knowledge_layer/extractor.py), [`rule_relations.py`](knowledge_layer/rule_relations.py), [`spec_claims.py`](knowledge_layer/spec_claims.py), [`table_claims.py`](knowledge_layer/table_claims.py), [`table_context.py`](knowledge_layer/table_context.py), [`validator.py`](knowledge_layer/validator.py), [`graph.py`](knowledge_layer/graph.py), [`entity_resolver.py`](knowledge_layer/entity_resolver.py), [`ontology_manager.py`](knowledge_layer/ontology_manager.py) | Neo4j, `data/knowledge/ontology.json`, `data/reports/<doc>/extraction_by_source.json` |
| 9 Report | per-document markdown report | [`reporter.py`](knowledge_layer/reporter.py), [`reference_tracker.py`](knowledge_layer/reference_tracker.py) | `data/reports/<doc>/report.md` |

Notes that matter when reading the results:

- **Validation is per item, not all-or-nothing.** Items with an error-severity issue are rejected; the rest of the
  chunk is inserted. Accepted/rejected/weak-grounded counts are recorded per source (`llm_pass1`, `llm_pass2`,
  `rule`, `table`).
- **The graph is deterministic today.** No `extraction_by_source.json` under `data/reports/` contains an LLM
  source (only `rule` / `table`, or empty) and every `report.md` shows "LLM ok 0"; the LLM passes have never produced graph output (deepseek-r1:7b runs at ~3 tok/s on the
  4 GB GPU). For the CDU manual: 116 rule relationships and 40 rule + 364 table claims accepted.
- **Without Neo4j** Phases 7/7a and graph insertion are skipped (a warning is printed), but embeddings are still
  cached and Phase 8's deterministic passes still run. **Without Ollama** Phase 8 runs the deterministic passes only.
- The schema has 101 entity types and 38 relationship types ([`schemas/knowledge.py`](knowledge_layer/schemas/knowledge.py)).
  Claims carry a context key (predicate, role, location, mode, scenario, pressure basis, qualifier); two claims
  are compared only when those match (`CONFLICTS_WITH` / `CORROBORATES`).
- LLM settings ([`config.py`](knowledge_layer/config.py)): `deepseek-r1:7b`, `num_ctx` 8192, `num_predict` 3072.
  Env overrides: `RKL_OLLAMA_MODEL`, `RKL_OLLAMA_URL`, `RKL_NEO4J_URI`, `RKL_NEO4J_USER`, `RKL_NEO4J_PASSWORD`.

## Flags

```
python -m knowledge_layer                  # all PDFs in data/raw/, resume from checkpoints
python -m knowledge_layer --no-llm         # deterministic passes only (rules, spec/prose/table claims, document graph)
python -m knowledge_layer --max-pages 40   # extract only chunks starting on pages 1..40 (phase left open)
python -m knowledge_layer --retry-failed   # re-run only chunks recorded as failed
python -m knowledge_layer --clean          # reset the extraction checkpoint and clear Neo4j
python -m knowledge_layer --rebuild        # re-run everything after parsing and clear Neo4j
```

Caveats: `--clean` and `--rebuild` clear the whole Neo4j database at the start of *each* PDF, so with several
PDFs in `data/raw/` only the last one remains in the graph. `--help` is not implemented: it is ignored and the
full pipeline runs.

Useful scripts in [`knowledge_layer/scripts/`](knowledge_layer/scripts/): `run_parse.py` (Phase 1 only),
`audit_pipeline.py "<doc>"` (offline quality gate over the deterministic layers), `dump_graph.py`,
`graph_integrity.py`, `cleanup_graph.py`, `smoke_test.py`, `start_neo4j.ps1 -Detached`.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/knowledge_layer -q    # 132 tests
.venv\Scripts\python.exe -m pytest tests -q                    # 665 tests (533 workbench + 132 knowledge layer)
```

The knowledge-layer tests need no services: parser structures, normalizer, table classifier and context,
chunker types, glossary, entity identity, claim context, spec/table claims, rule relations, validator and
regressions (for example: a pressure claim with a °C unit is rejected; co-occurrence does not create `FEEDS`).

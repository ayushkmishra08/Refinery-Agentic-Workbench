# Handoff — finishing the workbench once knowledge-layer extraction is complete

*Written 2026-09-15 for the next session. Read this before touching code. It lists what exists, what is
pending, and exactly how to plug the finished knowledge layer (Neo4j) into the workbench.*

## 0. Where things stand

| Area | State | Where |
|---|---|---|
| Knowledge layer (parse → normalize → chunks → claims → Neo4j) | Deterministic layers complete for `CDU operating manual`; LLM entity/relationship extraction still running / pending | `knowledge_layer/`, artefacts under `data/` |
| Workbench contracts (blocks, request, plan, result, events) | Done, frozen; JSON schema export via `python -m workbench schema` | `workbench/core/` |
| Knowledge access | **Files backend** (reads the KL artefacts, rebuilds claims/relations deterministically, cached index) — production path today. **Neo4j backend** — stub that raises `NotImplementedError` | `workbench/services/backends/` |
| Agents (12 named + 4 retrieval specialists) | Implemented, LLM-optional, tested end-to-end in LLM-free mode on the real manual | `workbench/agents/` |
| Orchestration (phases 0–5, replan, HITL, runs, "btw" status agent) | Done | `workbench/orchestration/` |
| API (FastAPI + SSE + upload + reviews) and CLI (ask/repl/serve/bench/schema) | Done; not yet exercised by the real frontend | `workbench/app/` |
| Resource management (idle unload, vision on demand, lazy embedder/reranker, profiles) | Done | `workbench/services/resources.py`, `workbench/config.py` |
| LLM integration (Ollama, schema-constrained JSON) | Client done and probed with `qwen3:4b`; agents' LLM paths are wired but only lightly exercised | `workbench/llm/` |
| Uploads (PDF → KL pipeline → session backend; images → vision description) | Implemented; **not tested with a real PDF or image** (would run Docling on the GPU) | `workbench/services/ingest.py` |
| Benchmarks | Prompt set (62 prompts, 14 categories) + runner + report; 2026-09-15 LLM-free run: task/agents/entities/blocks/safety all 1.00, mean 2.4 s | `workbench/benchmarks/`, `data/workbench/reports/benchmark-20260915-045202.md` |
| Tests | `tests/workbench/` — 166 tests (160 pass, 1 xfail: spaced tags without dashes, 5 xpass); knowledge layer 132 pass | `tests/` |
| Docs | `docs/HOW_IT_WORKS.md` (presentation), `docs/API.md` (frontend contract), this file, `docs/PLAN.md` | |

## 1. Pending work, in the order to do it

### 1.1 Neo4jKnowledgeBackend (the main integration task)
Implement `workbench/services/backends/neo4j_backend.py` as a class with the same methods as
`workbench/services/protocols.py::KnowledgeService`, returning the typed records in `workbench/core/knowledge.py`.
Reuse `knowledge_layer.memory.Neo4jMemory` for the driver/session (`memory.session()` context manager) and
`knowledge_layer.embedder.ChunkEmbedder.embed_query` (or the store's `_Embedder`) for query vectors.

Graph model written by the knowledge layer (from `knowledge_layer/memory.py`):

| Record to return | Cypher source |
|---|---|
| `DocumentInfo` | `(:Document {document_id,title,doc_type,revision,status,plant,unit,total_pages})` |
| `EntityRecord` | `(:Entity {uid,name,canonical_name,canonical_tag,entity_type,aliases,document_ids,mention_count,page})`; trains via `(:Entity)-[:HAS_TRAIN]->(:Entity)`; `SAME_AS` for untagged twins |
| `ClaimRecord` | `(:Entity)-[:HAS_CLAIM]->(:Claim {uid,subject_uid,predicate,value,value_numeric,unit_normalized,parameter_role,location,operating_mode,scenario,pressure_basis,temporal_status,qualifier,context_key,document_id,page,chunk_id,evidence,source,confidence})` — map `uid`→`claim_id`, `unit_normalized`→`unit`; `revision` from the Document |
| `ConflictRecord` | `(:Claim)-[:CONFLICTS_WITH|CORROBORATES]-(:Claim)`; group by `context_key` exactly as `IndexStore.conflicts_for` does |
| `RelationRecord` | `(:Entity)-[r]->(:Entity)` where `type(r)` is a `RelationshipType` value; properties `document_id,predicate,evidence,page,confidence,chunk_id`; `source` = `'rule'` when `predicate_raw STARTS WITH 'rule:'` else `'llm'` |
| `ProcedureRecord` / `StepRecord` | `(:Document)-[:HAS_PROCEDURE]->(:Procedure {procedure_id,title,procedure_type,section_path,chapter_number,page_start,page_end,applies_to})-[:HAS_STEP {sequence}]->(:ProcedureStep {step_id,sequence,instruction,page})`; `(:Procedure)-[:APPLIES_TO]->(:Entity)`; `(:ProcedureStep)-[:MENTIONS]->(:Entity)` gives `StepRecord.tags`; `(:Procedure)-[:DESCRIBED_IN]->(:Chunk)` gives `chunk_ids` |
| `ChunkRecord` | `(:Chunk {chunk_id,document_id,page_start,page_end,section,section_id,chapter_number,text,chunk_type,procedure_ids,table_ids,embedding})`; `section`→`section_path` |
| `SectionRecord` | `(:Section {section_id,title,level,number,page_start,page_end,path,chapter_number})` |
| `GlossaryRecord` | `(:Term {term,canonical_meaning,abbreviation,full_form,page,document_id})` |
| `DocumentReferenceRecord` | `(:Document)-[:REFERENCES_DOCUMENT {page,evidence}]->(:DocumentReference {number,document_type,reference_text,present_in_corpus})` |
| `StandingInstructionRecord` | `(:StandingInstruction {number,title,issue_date,status,remark,page})-[:INCORPORATED_IN]->(:Chapter)` |
| `CrossReferenceRecord` | `(:Section)-[:REFERENCES {page,target_number,target_kind,evidence}]->(:Chapter|:Section)` |

Retrieval in Neo4j:
- vector: `CALL db.index.vector.queryNodes('chunk_embeddings', $k, $vec)` (index exists; 384-dim bge-small — the
  query encoder must stay `BAAI/bge-small-en-v1.5`, see `RetrievalSettings.embedding_model`).
- keyword: create once `CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text, c.section]`
  and `CREATE FULLTEXT INDEX procedure_title IF NOT EXISTS FOR (p:Procedure) ON EACH [p.title, p.section_path]`,
  then `CALL db.index.fulltext.queryNodes('chunk_text', $q)`. Fuse with RRF exactly like `IndexStore.search_chunks`
  (`rrf_k=60`), apply the same `chunk_types` / `chapters` soft filters, then the optional reranker (reuse `_Reranker`).
- entity resolution: match `canonical_tag` first (note the KL scopes untagged tags with the unit slug, e.g.
  `CDU-II/P-101`; the workbench's file index uses the unscoped canonical — match on both `canonical_tag` and
  `split(canonical_tag,'/')[-1]`), then `aliases`/`name` case-insensitively, then a full-text index on `Entity.name`.
  Keep the same ranking rule as `IndexStore.resolve_entity` (tagged beats untagged at equal score; mentions break ties).
- `entity_claims` must also include claims of `HAS_TRAIN` children and the parent (11-PM-01 ↔ 11-PM-01A/B) — the
  file store does this via `parse_tag`.

Wire-up: `RWB_KNOWLEDGE_BACKEND=neo4j` → `workbench/services/knowledge.py` already routes there. Keep the composite
wrapper so session uploads still work. Then run `python -m workbench bench` and compare with the files-backend report
in `data/workbench/reports/benchmark-*.md`; the same prompts must give the same task types and entities.

Cheap alternative if Neo4j quality is not better yet: keep the files backend and add a **hybrid**: files backend for
structure/claims (identical to the KL's deterministic layers) + Neo4j only for the LLM-extracted entities/relations
(`source IN ['llm_pass1','llm_pass2']`) merged into `entity_neighbors`. That is a ~150-line class.

### 1.2 LLM-extracted relationships
Once the KL LLM passes finish, `FEEDS/DISCHARGES_TO/...` relations will be far denser. The `GraphAgent._trace`
BFS then finds real paths ("crude charge pump → pre-heat train → desalter → … → column"). Nothing to change in the
agent; check `docs/HOW_IT_WORKS.md` examples still read correctly.

### 1.3 LLM-on quality pass (qwen3:4b, then a larger profile)
Measured on 2026-09-15 with `qwen3:4b` on the GTX 1650: `why_summary` (explanation) 18 s for a 61-token grounded
summary — good; `plan_refine` 20 s; `diagnose` (structuring causes/checks/actions from 3 passages) 90–120 s per
call because the model wrote 800–900 output tokens at ~9 tok/s. Consequences already applied: the diagnose schema is
typed (`DiagItem{text, quote}`), output capped at 700 tokens and 5 items per list, the structure is cached across the
causes/checks/actions steps (one call per request), and `HardwareProfile.llm_extraction` is **False on `cpu` and
`gpu_4gb`** (deterministic extraction only; force with `RWB_LLM_EXTRACTION=on`) and True from `gpu_8gb` up.
Run `python -m workbench bench --llm -v` and look at:
- `TaskClassifierAgent`: the LLM is consulted only when rule confidence < `classifier_confidence_threshold` (0.72).
  Compare rule vs rules+llm accuracy in the report; lower the threshold only if the LLM wins.
- `ExplanationAgent` (`why_summary`) and `DiagnosticAgent` (`diagnose`): quotes are verified against the passages;
  if many are dropped ("ungrounded quotes dropped" in the trace), shorten the passages (`max_chunk_chars_in_prompt`).
- `VerificationAgent` removes narrative blocks whose numbers are not in evidence — count `removed_blocks` in the audit.
- Timing: each short structured call is ~5–6 s on the GTX 1650 after load; the first call adds ~8 s model load.
  On the college GPU (`gpu_12gb`+ profile) switch `llm.think` to `True` only for `planner` refinement if quality needs it.

### 1.4 Uploads and images (implemented, untested)
- PDF: `POST /upload` → `services/ingest.py::build_index_from_pdf` runs the KL parser (Docling) → normalizer → chunker →
  claims → `SessionDocumentsBackend`. Test with a 5–20 page PDF first; a 500-page manual takes hours on the 4 GB card.
  To make an upload permanent in the knowledge layer: copy the PDF to `data/raw/` and run `python -m knowledge_layer`
  (the parsed artefacts written by the upload are reused, so only Neo4j writes remain).
- Images: `ResourceManager.describe_image` uses the profile's vision model (`qwen3.5:2b` on 4 GB) and unloads it
  right after. The description becomes a session note (`SessionState.notes`). Next step: let `ContextResolverAgent`
  pull tags/values from those notes (they are not used yet) and add an `ImageBlock` to the answer.

### 1.5 Frontend integration
`docs/API.md` is the contract. Run `python -m workbench serve`, then `python -m workbench schema` to refresh
`docs/schema/*.json`. The frontend should: open `POST /runs` → `GET /runs/{id}/events` (SSE) → render `final`;
call `POST /runs/{id}/btw` for the status agent; render blocks by `type`. Session id per browser tab.

### 1.6 Known limitations to fix or document
- Instrument ↔ equipment links are heuristic (same sentence, ≤160 chars, same plant prefix) plus the instrument-tag
  description tables; the graph shows them as *inferred*. Denser LLM relations will supersede them.
- Flow-path traces depend on rule relationships (86 FEEDS today) → partial neighbourhoods are shown when no path exists.
- LLM-free narratives are templated; with the LLM on, summaries are added only when grounded.
- One document is loaded; multi-document authority ranking (`DocumentInfo.authority_rank`) is implemented but exercised
  only in tests.
- `IndexStore` holds everything in memory (fine for tens of documents; Neo4j is the scale path).

## 2. How to run things

```powershell
python -m workbench status                     # profile, models, backend, documents
python -m workbench ask "What is the normal flow rate of the crude charge pump?" -v
python -m workbench repl                       # type "btw what's going on?" while a request runs
python -m workbench serve --port 8000          # API + SSE
python -m workbench bench                      # LLM-free benchmark (fast);  --llm to include the model
python -m pytest tests/workbench -q
RWB_LLM=off / RWB_RERANKER=off / RWB_PROFILE=gpu_12gb / RWB_LLM_MODEL=qwen3.5:9b / RWB_KNOWLEDGE_BACKEND=files|mock|neo4j
```
Cache: `data/workbench/cache/<doc>/index.json` — delete it (or bump `BUILDER_VERSION` in `files_backend.py`) after
changing `services/index/builder.py`.

## 3. Design invariants (do not break)
1. Every statement carries evidence keys; numbers in prose must appear in cited evidence (Verification agent).
2. Two values are comparable only when their claim context keys match (role, location, mode, scenario, basis, qualifier).
3. The LLM never does arithmetic and never writes procedure steps; it classifies, structures grounded quotes, and
   summarises. All LLM output is schema-constrained (`format` = JSON schema) and validated.
4. Restricted requests (bypass/defeat protection) never receive instructions; only the documented authorization path.
5. Nothing stays loaded when idle: LLM unload after 45 s (4 GB profile), vision model released immediately,
   embedder/reranker lazy.
6. UNKNOWN beats GUESS: missing evidence is reported in `warnings` / callouts, never filled in.

## 4. File map (workbench)
```
workbench/config.py                 profiles (cpu, gpu_4gb, gpu_8gb, gpu_12gb, gpu_16gb, gpu_24gb), settings, env overrides
workbench/core/                     blocks.py (16 block types), request.py, plan.py, result.py, evidence.py, knowledge.py, context.py, events.py
workbench/llm/                      client.py (OllamaClient, NullLLM, build_llm), fake.py (tests), prompts.py
workbench/services/index/           builder.py (DocumentIndex from KL objects), store.py (IndexStore: BM25+vector+rerank, entity/claim/procedure search)
workbench/services/backends/        files_backend.py, mock_backend.py, composite.py, neo4j_backend.py (stub)
workbench/services/                 context_builder.py (Phase 2 routes), evidence_store.py, revision_resolver.py, audit_store.py, resources.py, ingest.py, calculators/
workbench/agents/                   task_classifier, context_resolver, retrieval (lookup/graph/explanation/cross_document), procedure, diagnostic,
                                    calculation, comparison, revision_conflict, report, planner, safety, verification, governance, registry, base
workbench/orchestration/            router.py (matrix + templates), executor.py, orchestrator.py (phases, replan), runs.py, status_agent.py (btw), hitl.py
workbench/app/                      api.py (FastAPI), cli.py
workbench/prompts/                  task_classifier, context_resolver, explanation, diagnostic, planner, report_summary
workbench/benchmarks/               prompts.yaml, runner.py
workbench/fixtures/                 mock backend data
```

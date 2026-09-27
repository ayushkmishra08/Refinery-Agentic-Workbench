# Handoff — finishing the workbench once knowledge-layer extraction is complete

*Written 2026-09-15 for the next session; status section and file map refreshed 2026-09-26 against the code.
Read this before touching code. It lists what exists, what is pending, and exactly how to plug the finished
knowledge layer (Neo4j) into the workbench.*

**The integration described here is still pending.** As of 2026-09-26 the Neo4j backend is a stub, the full-text
indexes proposed in §1.1 have not been created, and the knowledge layer's LLM extraction has never produced graph
output, so there are no LLM-extracted relations to integrate yet.

## 0. Where things stand (2026-09-26)

| Area | State | Where |
|---|---|---|
| Knowledge layer (parse → table classification → normalize → profile → glossary → chunks → document graph → embeddings → claims → Neo4j) | Deterministic layers complete for **six documents** (CDU operating manual, API610, API560, Bulletin-5EH, Crude desalter, ESBWR). **LLM extraction is not running and has never produced output**: no `data/reports/*/extraction_by_source.json` contains an LLM source (only `rule` / `table`, or empty), every `report.md` shows "LLM ok 0", and the latest reports for API560 and Bulletin-5EH show 0 chunks processed | `knowledge_layer/`, artefacts under `data/` |
| Workbench contracts (blocks, request, plan, result, events) | Done, frozen; 16 block types, 15 task types; JSON schema export via `python -m workbench schema` | `workbench/core/` |
| Knowledge access | Default backend `auto`: **files backend** when KL artefacts exist (reads them, rebuilds claims/relations deterministically, cached index), else **mock** fixtures; both wrapped in the composite service for session uploads. **Neo4j backend** — 20-line stub that raises `NotImplementedError` | `workbench/services/backends/` |
| Agents (17 keys: 12 named + 4 retrieval specialists + Answer Composer) | Implemented, LLM-optional, tested end-to-end in LLM-free mode on the real documents | `workbench/agents/` |
| Orchestration (phases 0–5, replan, HITL, runs, "btw" status agent, narration) | Done | `workbench/orchestration/` |
| API (FastAPI `api.py` + `api_ext.py`, SSE, upload, reviews) and CLI (`cli.py` + `cli_ext.py`) | Done; exercised by the `WebPage/` frontend (React + Vite, proxies to `serve --port 8077`; `serve` defaults to port 8000) | `workbench/app/`, `WebPage/` |
| Resource management (idle unload, vision on demand, lazy embedder/reranker, profiles) | Done | `workbench/services/resources.py`, `workbench/config.py` |
| LLM integration (Ollama, schema-constrained JSON) | Client done and probed with `qwen3:4b`; model router on by default, choosing per call from the 7 models in `workbench/models/registry.yaml`. At the default `medium` effort the LLM *is* called (Answer Composer, unsure classification, follow-up rewrite); only `low` is model-free | `workbench/llm/`, `workbench/models/` |
| Uploads (PDF → KL pipeline → per-session backend; images → OCR + vision intake → review draft) | Implemented; PDF path run on sample PDFs (`data/sample docs/`), image intake covered by tests but not yet by a broad set of real scans; `promote_upload` (manager+) moves a session upload into the shared corpus | `workbench/services/ingest.py`, `workbench/intake/` |
| September subsystems (models/router, tools + sandbox, intake, deliverables + review, vault, TLS, sovereignty, escalation) | Implemented and tested; open security issues are listed in `docs/DOC_AUDIT.md` §1 | see §4 |
| Benchmarks | Prompt set (**70 prompts, 16 categories**) + runner + report; latest run 2026-09-16 (files backend, no LLM): task/agents/entities/blocks/safety/answered all 1.0, mean 434 ms, 30.6 s total | `workbench/benchmarks/`, `data/workbench/reports/benchmark-20260916-202058.md` |
| Tests | **665** collected: `tests/workbench/` 533, `tests/knowledge_layer/` 132 | `tests/` |
| Docs | `docs/HOW_IT_WORKS.md`, `docs/API.md`, `docs/SECURITY.md`, `docs/ACCESS_AND_ANSWERS.md`, `docs/manual/index.html`, this file, `docs/PLAN.md`, `docs/DOC_AUDIT.md` | |

## 1. Pending work, in the order to do it

### 1.1 Neo4jKnowledgeBackend (the main integration task) — not started
Implement `workbench/services/backends/neo4j_backend.py` as a class with the same methods as
`workbench/services/protocols.py::KnowledgeService`, returning the typed records in `workbench/core/knowledge.py`.
Reuse `knowledge_layer.memory.Neo4jMemory` for the driver/session (`memory.session()` context manager) and
`knowledge_layer.embedder.ChunkEmbedder.embed_query` (or the store's `_Embedder`) for query vectors.

Graph model written by the knowledge layer (from `knowledge_layer/memory.py` and `knowledge_layer/document_graph.py`):

| Record to return | Cypher source |
|---|---|
| `DocumentInfo` | `(:Document {document_id,title,doc_type,revision,status,plant,unit,source_filename,total_pages})`; `(:Document)-[:HAS_ENTITY {page,evidence,confidence}]->(:Entity)`; `(:Document)-[:HAS_CHUNK]->(:Chunk)` |
| `EntityRecord` | `(:Entity {uid,name,canonical_name,canonical_tag,entity_type,domain,aliases,document_ids,mention_count,page,plant,unit,is_stub,resolution_method,source,grounding})`; trains via `(:Entity)-[:HAS_TRAIN]->(:Entity)`; `SAME_AS` for untagged twins |
| `ClaimRecord` | `(:Entity)-[:HAS_CLAIM]->(:Claim {uid,subject_uid,predicate,predicate_raw,value,value_numeric,unit,unit_raw,unit_normalized,claim_category,parameter_role,location,operating_mode,scenario,pressure_basis,temporal_status,qualifier,context_key,document_id,page,chunk_id,evidence,source_text,source,grounding,confidence,is_from_table,table_id,sentence_index,resolution_status})` — map `uid`→`claim_id`, `unit_normalized`→`unit`; `revision` from the Document. `(:Chunk)-[:SUPPORTS]->(:Claim)` links a claim to its chunk |
| `ConflictRecord` | `(:Claim)-[:CONFLICTS_WITH|CORROBORATES {cross_document}]-(:Claim)`; `resolution_status` is set to `potential_conflict` / `supported` (default `unreviewed`); group by `context_key` exactly as `IndexStore.conflicts_for` does |
| `RelationRecord` | `(:Entity)-[r]->(:Entity)` where `type(r)` is a `RelationshipType` value; properties `document_id,predicate,predicate_raw,evidence,page,confidence,chunk_id,mention_count,sentence_index` and explicit **`r.source`** (`rule`, `llm_pass1`, `llm_pass2`) and **`r.grounding`** (`strong` / `weak`), written by `knowledge_layer/memory.py::upsert_relationship` — read `r.source` directly instead of inferring it from `predicate_raw` |
| `ProcedureRecord` / `StepRecord` | `(:Document)-[:HAS_PROCEDURE]->(:Procedure {procedure_id,title,procedure_type,section_path,chapter_number,page_start,page_end,step_count,applies_to})-[:HAS_STEP {sequence}]->(:ProcedureStep {step_id,sequence,instruction,page,procedure_id})`; `(:ProcedureStep)-[:NEXT]->(:ProcedureStep)`; `(:Procedure)-[:IN_SECTION]->(:Section)`; `(:Procedure)-[:APPLIES_TO]->(:Entity)`; `(:ProcedureStep)-[:MENTIONS]->(:Entity)` gives `StepRecord.tags`; `(:Procedure)-[:DESCRIBED_IN]->(:Chunk)` gives `chunk_ids` |
| `ChunkRecord` | `(:Chunk {chunk_id,document_id,page_start,page_end,section,section_id,chapter_number,sequence,text,chunk_type,is_engineering,contains_table,contains_procedure,procedure_ids,table_ids,embedding})`; `section`→`section_path`; `(:Chunk)-[:IN_SECTION]->(:Section)`; `(:Chunk)-[:MENTIONS {evidence,confidence}]->(:Entity)` |
| `SectionRecord` | `(:Document)-[:HAS_CHAPTER]->(:Chapter {chapter_id,number,title,page_start,page_end,revision,revision_date,is_administrative})-[:HAS_SECTION]->(:Section {section_id,title,level,number,page_start,page_end,path,chapter_number,element_count})-[:HAS_SUBSECTION]->(:Section)` |
| `GlossaryRecord` | `(:Term {term,canonical_meaning,abbreviation,full_form,page,document_id})` |
| `DocumentReferenceRecord` | `(:Document)-[:REFERENCES_DOCUMENT {page,evidence}]->(:DocumentReference {number,document_type,reference_text,present_in_corpus})` |
| `StandingInstructionRecord` | `(:Document)-[:HAS_STANDING_INSTRUCTION]->(:StandingInstruction {number,title,issue_date,status,remark,page})-[:INCORPORATED_IN]->(:Chapter)` |
| `CrossReferenceRecord` | `(:Section)-[:REFERENCES {page,target_number,target_kind,evidence}]->(:Chapter|:Section)` |

Retrieval in Neo4j:
- vector: `CALL db.index.vector.queryNodes('chunk_embeddings', $k, $vec)` (index exists; 384-dim bge-small — the
  query encoder must stay `BAAI/bge-small-en-v1.5`, see `RetrievalSettings.embedding_model`).
- keyword: **not created yet** — `Neo4jMemory.setup_schema()` creates constraints, property indexes and the vector
  index only. Create once
  `CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text, c.section]`
  and `CREATE FULLTEXT INDEX procedure_title IF NOT EXISTS FOR (p:Procedure) ON EACH [p.title, p.section_path]`,
  then `CALL db.index.fulltext.queryNodes('chunk_text', $q)`. Fuse with RRF exactly like `IndexStore.search_chunks`
  (`rrf_k=60`), then apply the same filters: `chunk_types` (and `document_ids`) is a **hard** filter; `chapters` is
  soft (when too few candidates remain it falls back to the unfiltered ranking, still restricted to `chunk_types`).
  Then the optional reranker (reuse `_Reranker`).
- entity resolution: match `canonical_tag` first (note the KL scopes untagged tags with the unit slug, e.g.
  `CDU-II/P-101`; the workbench's file index uses the unscoped canonical — match on both `canonical_tag` and
  `split(canonical_tag,'/')[-1]`), then `aliases`/`name` case-insensitively, then a full-text index on `Entity.name`
  (also not created yet). Keep the same ranking rule as `IndexStore.resolve_entity` (tagged beats untagged at equal
  score; mentions break ties).
- `entity_claims` must also include claims of `HAS_TRAIN` children and the parent (11-PM-01 ↔ 11-PM-01A/B) — the
  file store does this via `parse_tag`.

Wire-up: `RWB_KNOWLEDGE_BACKEND=neo4j` → `workbench/services/knowledge.py` already routes there. Keep the composite
wrapper so session uploads still work. Then run `python -m workbench bench` and compare with the files-backend report
in `data/workbench/reports/benchmark-*.md`; the same prompts must give the same task types and entities.

Before loading Neo4j for this, note that `python -m knowledge_layer --clean` / `--rebuild` clear the whole database
at the start of *each* PDF, so with six PDFs in `data/raw/` only the last one survives a clean run.

Cheap alternative if Neo4j quality is not better yet: keep the files backend and add a **hybrid**: files backend for
structure/claims (identical to the KL's deterministic layers) + Neo4j only for the LLM-extracted entities/relations
(`r.source IN ['llm_pass1','llm_pass2']`) merged into `entity_neighbors`. That is a ~150-line class — worth building
only once the LLM passes have produced something (so far they have produced nothing).

### 1.2 LLM-extracted relationships — blocked on KL LLM extraction
Once the KL LLM passes run, `FEEDS/DISCHARGES_TO/...` relations will be far denser. The `GraphAgent._trace`
BFS then finds real paths ("crude charge pump → pre-heat train → desalter → … → column"). Nothing to change in the
agent; check `docs/HOW_IT_WORKS.md` examples still read correctly. As of 2026-09-26 no report contains an LLM
relation; `deepseek-r1:7b` (the KL default) runs at ~3 tok/s on the 4 GB GPU, and `RKL_OLLAMA_MODEL=qwen3:4b` is the
faster option.

### 1.3 LLM-on quality pass (qwen3:4b, then a larger profile)
Measured on 2026-09-15 with `qwen3:4b` on the GTX 1650: `why_summary` (explanation) 18 s for a 61-token grounded
summary — good; `plan_refine` 20 s; `diagnose` (structuring causes/checks/actions from 3 passages) 90–120 s per
call because the model wrote 800–900 output tokens at ~9 tok/s. Consequences already applied: the diagnose schema is
typed (`DiagItem{text, quote}`), output capped at 700 tokens and 5 items per list, the structure is cached across the
causes/checks/actions steps (one call per request), and `HardwareProfile.llm_extraction` is False on `cpu` and
`gpu_4gb` and True from `gpu_8gb` up. **The effort level overrides that profile flag**: `load_config` calls
`apply_effort` after `apply_profile`, and `apply_effort` sets `use_llm_for_extraction` from the effort level, which
is True only at `ultra`. So diagnosis structuring is on only at `--effort ultra` (on any GPU) or when forced with
`RWB_LLM_EXTRACTION=on` (applied last).
Run `python -m workbench -v bench --llm` and look at:
- `TaskClassifierAgent`: the LLM is consulted only when rule confidence < `classifier_confidence_threshold` (0.72).
  Compare rule vs rules+llm accuracy in the report; lower the threshold only if the LLM wins.
- `ExplanationAgent` (`why_summary`) and `DiagnosticAgent` (`diagnose`): quotes are verified against the passages;
  if many are dropped ("ungrounded quotes dropped" in the trace), shorten the passages (`max_chunk_chars_in_prompt`).
- `VerificationAgent` removes narrative blocks whose numbers are not in evidence — count `removed_blocks` in the audit.
- `AnswerComposerAgent`: writes the released prose at `medium` effort and above; its answer is grounding-checked
  and re-asked up to `answer_retries` times.
- Timing: each short structured call is ~5–6 s on the GTX 1650 after load; the first call adds ~8 s model load.
  On the college GPU (`gpu_12gb`+ profile) switch `llm.think` to `True` only for `planner` refinement if quality needs it.

### 1.4 Uploads and images (implemented; PDF path run on sample PDFs)
- Uploads are **per conversation**: `POST /upload` stores the file under
  `data/workbench/uploads/<username>/<session_id>/` and the result is visible only in that session.
- PDF: `services/ingest.py::build_index_from_pdf` runs the KL parser (Docling) → normalizer → chunker → claims in a
  background run (poll `/runs/{run_id}`), then adds a `SessionDocumentsBackend` for that session only. A 500-page
  manual takes hours on the 4 GB card; test with 5–20 pages.
- Making an upload permanent: `POST /knowledge/documents/{document_id}/promote` (`Orchestrator.promote_upload`,
  manager or above) re-indexes the uploaded file with `persist=True`, adds it to the shared knowledge service,
  classifies it (the upload default tag unless one is given), drops the private session copy and writes a security
  audit entry. The older route — copy the PDF to `data/raw/` and run `python -m knowledge_layer` — also works.
- Images go through the **intake pipeline** (`workbench/intake`): on-device OCR (RapidOCR) with a confidence per
  line, then the vision model; lines under `review.ocr_confidence_threshold` (0.6, `RWB_OCR_THRESHOLD`) are flagged
  and a review draft is created (`workbench/review`). If intake fails, `ResourceManager.describe_image` gives a plain
  description. The OCR and vision text become session notes (`SessionState.notes`). Next step is unchanged: let
  `ContextResolverAgent` pull tags/values from those notes (no agent reads them yet) and add an `ImageBlock` to the
  answer. `POST /intake` runs the same pipeline standalone.

### 1.5 Frontend integration — done
`docs/API.md` is the contract. The `WebPage/` frontend (React + Vite) talks to `python -m workbench serve --port 8077`
through its dev proxy (`/api` → 8077; `serve` on its own defaults to 8000). Run `python -m workbench schema` to
refresh `docs/schema/*.json` after contract changes. Streaming flow: `POST /runs` → `GET /runs/{id}/events` (SSE) →
render `final`; `POST /runs/{id}/btw` for the status agent; blocks rendered by `type`. Caveat: the first `final`
event on the stream is the orchestrator's `{response_id, status}`, not the FinalResponse (see `docs/DOC_AUDIT.md`
§1). Not built yet: a frontend screen for the HITL `/reviews` queue.

### 1.6 Known limitations to fix or document
- Instrument ↔ equipment links are heuristic (same sentence, ≤160 chars, same plant prefix) plus the instrument-tag
  description tables; the graph shows them as *inferred*. Denser LLM relations will supersede them.
- Flow-path traces depend on rule relationships → partial neighbourhoods are shown when no path exists.
- LLM-free narratives are templated; with the LLM on, summaries are added only when grounded.
- Six documents are loaded, but every knowledge-layer document gets the same `DocumentInfo.authority_rank` (60;
  uploads 40), so authority ranking only separates uploads from the corpus.
- `IndexStore` holds everything in memory (fine for tens of documents; Neo4j is the scale path).

## 2. How to run things

```powershell
python -m workbench status                     # profile, models, backend, documents
python -m workbench -v ask "What is the normal flow rate of the crude charge pump?"   # -v goes before the subcommand
python -m workbench repl                       # type "btw what's going on?" while a request runs
python -m workbench serve --port 8077          # API + SSE on the port the WebPage proxy expects (default is 8000)
python -m workbench bench                      # LLM-free benchmark (fast);  --llm to include the model
python -m pytest tests -q                      # 665 tests (tests/workbench 533, tests/knowledge_layer 132)
RWB_LLM=off / RWB_RERANKER=off / RWB_PROFILE=gpu_12gb / RWB_LLM_MODEL=qwen3.5:9b / RWB_ROUTING=off / RWB_KNOWLEDGE_BACKEND=auto|files|mock|neo4j
```
Cache: `data/workbench/cache/<doc>/index.json` — delete it (or bump `BUILDER_VERSION` in `files_backend.py`) after
changing `services/index/builder.py`.

## 3. Design invariants (do not break)
1. Every statement carries evidence keys; numbers in prose must appear in cited evidence (Verification agent).
2. Two values are comparable only when their claim context keys match (role, location, mode, scenario, basis, qualifier).
3. The LLM never does arithmetic and never writes procedure steps; it classifies, structures grounded quotes, and
   summarises. LLM output is schema-constrained (`format` = JSON schema) and validated, **with two exceptions**:
   the Report agent's `report_summary` narrative (`BaseAgent.llm_text` → free-text `complete()`, kept only if
   `usable_narrative` accepts it) and vision descriptions (`describe_image`, free text — which is why
   low-confidence intake output is flagged for review rather than used as fact).
4. Restricted requests (bypass/defeat protection) never receive instructions; only the documented authorization path.
5. Nothing stays loaded when idle: LLM unload after 45 s (`cpu` and `gpu_4gb` profiles; 180 s otherwise), vision
   model released immediately, embedder/reranker lazy. `RWB_KEEP_WARM=1` disables the idle unload for demos.
6. UNKNOWN beats GUESS: missing evidence is reported in `warnings` / callouts, never filled in.

## 4. File map (workbench)
```
workbench/__init__.py, __main__.py  module map; `python -m workbench`
workbench/config.py                 profiles (cpu, gpu_4gb, gpu_8gb, gpu_12gb, gpu_16gb, gpu_24gb), effort levels, settings blocks, env overrides
workbench/core/                     blocks.py (16 block types), request.py (15 task types), plan.py, result.py, evidence.py, knowledge.py,
                                    context.py, events.py, errors.py
workbench/llm/                      client.py (OllamaClient, NullLLM, build_llm), fake.py (tests), prompts.py
workbench/models/                   registry.py + registry.yaml (7 models), router.py (capability routing, routing log), routed.py (RoutedLLM)
workbench/services/index/           builder.py (DocumentIndex from KL objects), store.py (IndexStore: BM25+vector+rerank, entity/claim/procedure search)
workbench/services/backends/        files_backend.py, mock_backend.py, composite.py, neo4j_backend.py (stub)
workbench/services/                 knowledge.py (backend selection), protocols.py (KnowledgeService), context_builder.py (Phase 2 routes),
                                    evidence_store.py, revision_resolver.py, audit_store.py, response_store.py, thinking_store.py,
                                    tag_matcher.py, kl_io.py, resources.py, ingest.py (uploads), calculators/ (limits, units)
workbench/agents/                   task_classifier, context_resolver, retrieval (lookup/graph/explanation/cross_document), procedure, diagnostic,
                                    calculation, comparison, revision_conflict, report, planner, safety, verification, governance,
                                    composer (Answer Composer), registry, base
workbench/orchestration/            router.py (matrix + templates), executor.py, orchestrator.py (Orchestrator.ask: phases, replan), runs.py,
                                    status_agent.py (btw), hitl.py, narration.py (thinking text)
workbench/memory/                   session.py (per-session state), followup.py (elliptical follow-up rewriting)
workbench/app/                      api.py (FastAPI core), api_ext.py (models, sovereignty, vault, tools, sandbox, intake, deliverables, drafts),
                                    cli.py, cli_ext.py, credentials.py (CLI sign-in), deps.py, thinking_display.py
workbench/tools/                    base.py, registry.py, agent_loop.py, file_tools.py, code_tool.py, spreadsheet_tool.py,
                                    document_search_tool.py, calculator_tool.py, extra.py (intake + deliverable tools)
workbench/sandbox/                  runner.py (docker / isolated subprocess), verify.py, manifest.py, runlog.py, vendor/
workbench/intake/                   pipeline.py, ocr.py, vision.py
workbench/deliverables/             export.py, common.py, docx_builder.py, pptx_builder.py, xlsx_builder.py
workbench/review/                   drafts.py (drafts, flagged figures, sign-off)
workbench/security/                 roles.py, classification.py, auth.py, totp.py, policy.py, guard.py, escalation.py, records.py,
                                    leakcheck.py, audit.py, redteam.py, setup.py, vault.py, vault_backend.py, tls.py
workbench/sovereignty/              service.py, egress.py, netmonitor.py, hashchain.py, model_updates.py
workbench/prompts/                  task_classifier, context_resolver, explanation, diagnostic, planner, report_summary, answer_composer
workbench/benchmarks/               prompts.yaml, runner.py
workbench/fixtures/                 mock backend data
```

# Refinery Engineering AI Workbench: Complete Solution Context

> A durable technical context document for understanding, operating, demonstrating, extending, and handing off the Refinery project.
>
> This document consolidates the repository documentation, `walkthrough.md`, parser reports, setup notes, API contract, security model, implementation plan, and knowledge-layer handoff. It describes the current solution as of 2026-09-26, re-checked against the source code (see `docs/DOC_AUDIT.md` for the audit it was corrected from). Where a feature is implemented but still pending validation or integration, or is wired but has never produced output, that status is stated explicitly.

## 1. Executive Summary

The project is a fully local refinery engineering knowledge and question-answering system. It converts refinery and engineering PDFs into structured, source-grounded knowledge and exposes that knowledge through a local agentic workbench and a browser API.

The system has two layers:

1. **Knowledge layer** (`knowledge_layer/`): parses documents, removes noise, reconstructs structure and tables, creates typed chunks, extracts deterministic engineering claims and relationships, validates everything, and writes a document/domain/evidence graph to Neo4j plus JSON artefacts. An Ollama extraction pass (`llm_pass1` / `llm_pass2`) is wired into the pipeline, but it has **never produced output**: every knowledge-layer report shows 0 LLM entities, relations and claims, so the graph and the workbench indexes are deterministic only (rule and table sources).
2. **Agentic workbench** (`workbench/`): accepts an engineer's request, classifies the task, resolves equipment and context, enforces access control, retrieves only permitted evidence, builds and executes a bounded plan, verifies the result, composes a readable answer, applies safety and governance rules, and returns typed answer blocks with citations and an audit trail.

The core principle is:

> The LLM is not the system's memory and is not trusted to invent engineering truth. Structured external knowledge, deterministic rules, evidence, validation, and governance control what can be answered.

The workbench's backend setting defaults to `auto`, which selects the **files backend** when knowledge-layer artefacts exist (and `mock` otherwise). The files backend reads the on-disk artefacts of the six loaded documents, rebuilds deterministic indexes, and serves answers without requiring Neo4j. The Neo4j-backed `KnowledgeService` adapter is a 20-line stub that raises `NotImplementedError`; implementing it is the main pending integration task.

The solution is designed to:

- answer refinery lookups, flow traces, procedures, troubleshooting questions, limit checks, explanations, safety questions, comparisons, conflicts, provenance, plans, reports, cross-document questions, and inventory surveys;
- prefer `UNKNOWN` or a clarification over an unsupported guess;
- cite the exact document, page, chunk, claim, sentence, or table row behind a statement;
- compare engineering values only when their full context matches;
- prevent a user from bypassing document access restrictions through prompts, search, aliases, graph traversal, or record identifiers;
- run locally on modest hardware, including a GTX 1650 with 4 GB VRAM;
- degrade gracefully when Ollama or Neo4j is unavailable.

## 2. The Problem Being Solved

Refinery manuals contain valuable operating knowledge, but that knowledge is difficult to use directly:

- information is distributed across prose, tables, procedures, headings, figures, references, and appendices;
- repeated page headers, footers, approval blocks, figure placeholders, and table fragments contaminate extraction;
- the same equipment can appear under parent tags, A/B train tags, aliases, abbreviations, and OCR variations;
- a number is meaningless without its role, location, scenario, operating mode, pressure basis, and qualifier;
- two values that look different may be different facts rather than contradictions;
- procedures must preserve order, prerequisites, warnings, and page evidence;
- a language model can produce convincing but unsupported engineering statements;
- not every user is authorized to see every document;
- a frontend needs structured blocks, progress events, citations, safety indicators, and review states rather than a single unstructured paragraph.

The project addresses these problems by separating deterministic document intelligence from optional model assistance and by enforcing evidence and access boundaries beneath the model.

## 3. High-Level Architecture

```text
PDF
  |
  v
Docling parser + checkpointing + parse validation
  |
  v
Parsed document and tables
  |
  v
Table classification and reconstruction
  (before normalization: furniture tables are filtered by class)
  |
  v
Deterministic normalizer
  - remove page furniture
  - recover chapters and sections
  - identify procedures
  |
  v
Document profile + glossary
  |
  v
Structure-aware typed chunks
  |
  v
Neo4j schema/document/terms (when Neo4j is reachable)
  |
  +------------------------------+
  |                              |
  v                              v
7a Document graph                7b Embeddings (bge-small, 384-d)
  |                              |
  +---------------+--------------+
                  |
                  v
      8 Per-chunk extraction
        - Ollama llm_pass1/2: wired, but has produced
          NO output in any run to date
        - rule / specification / prose / table claims
          and rule relations (the only real sources)
                  |
                  v
        Validation and entity resolution
                  |
                  v
   Neo4j graph + JSON knowledge artefacts + report
                  |
                  v
       Workbench KnowledgeService (backend: auto)
                  |
  +---------------+----------------+
  |                                |
  v                                v
Files backend (auto default     Neo4j backend (stub: raises
when artefacts exist)           NotImplementedError)
  |
  v
Phase 0: access decision
  |
  v
Phase 1: classify, resolve, safety gate
  |
  v
Phase 2: targeted retrieval
  |
  v
Phase 3: plan DAG
  |
  v
Phase 4: execute agents and bounded replans
  |
  v
Phase 5: verify, compose, govern
  |
  v
FinalResponse: answer + blocks + evidence + confidence + safety + audit
  |
  +--------------------+--------------------+
  |                                         |
  v                                         v
CLI / REPL                               FastAPI + SSE + React/Vite UI
```

The deterministic document graph and deterministic claims are written independently of the LLM. If Ollama is unavailable (or `--no-llm` is passed), phase 8 runs the deterministic passes only, and the document still produces structure, procedures, claims, references, and rule-based relations. In practice this is the only path that has produced graph content so far.

## 4. Repository Map

```text
Refinery/
├── knowledge_layer/          PDF-to-knowledge extraction pipeline (banner v0.3.0)
│   ├── __main__.py           `python -m knowledge_layer` entry (note: `--help` runs the pipeline)
│   ├── config.py             central configuration; RKL_* overrides
│   ├── pipeline.py           main pipeline orchestrator (phases 1-9)
│   ├── parser.py             Docling parsing and page-window checkpoints
│   ├── parse_validation.py   parse validation checks and parse_report.md
│   ├── provenance.py         source provenance tracking
│   ├── table_classifier.py   deterministic table type classification
│   ├── table_reconstructor.py merged-cell propagation and deduplication
│   ├── table_context.py      table labels and page-split continuation links
│   ├── normalizer.py         deterministic cleanup and structure
│   ├── structure.py          structure reconstruction and block detection
│   ├── document_profile.py   document orientation and metadata
│   ├── glossary_builder.py   abbreviations and terminology
│   ├── chunker.py            typed, structure-aware chunks
│   ├── document_graph.py     deterministic document graph written to Neo4j (phase 7a)
│   ├── embedder.py           chunk/query embeddings (phase 7b)
│   ├── spec_claims.py        deterministic specification/prose claims
│   ├── table_claims.py       table cells to claims
│   ├── rule_relations.py     deterministic domain relations
│   ├── entity_identity.py    tag normalization and stable identity
│   ├── entity_resolver.py    cross-document identity handling
│   ├── ontology_manager.py   corpus-driven open, governed vocabulary
│   ├── reference_tracker.py  cross-document references
│   ├── retriever.py          Neo4j memory retrieval used as LLM context
│   ├── extractor.py          constrained Ollama extraction (wired; no output to date)
│   ├── validator.py          evidence, units, domain, contradiction checks
│   ├── graph.py              idempotent Neo4j insertion (GraphInserter)
│   ├── memory.py             Neo4j operations and vector search
│   ├── reporter.py           Markdown and JSON reports
│   ├── checkpoint.py         phase/chunk resume state
│   ├── schemas/              Pydantic schemas (claims, knowledge, ontology, tables, ...)
│   ├── prompts/              9 extraction prompt files (only 2 are used by extractor.py)
│   └── scripts/              22 utilities: run_parse, audit_pipeline, dump_graph,
│                             cleanup_graph, graph_integrity, migrate_entities, benchmark,
│                             validate_environment, start_neo4j.ps1, setup_neo4j,
│                             smoke_test, reset_phases, check_tables, inspect_*, probe_*,
│                             test_fixes / test_parse_window / test_parser_fix
├── workbench/                local agentic question-answering layer
│   ├── config.py             hardware profiles, effort levels, all Settings blocks, RWB_* env
│   ├── core/                 request, knowledge, plans, blocks, results, events, evidence, errors
│   ├── agents/               17 agent keys: classifier, resolver, retrieval, planner, procedure,
│   │                         diagnostic, calculation, comparison, revision_conflict, report,
│   │                         verification, composer, safety, governance (+ base, registry)
│   ├── services/             backends/ (files, composite, mock, neo4j stub), index/, calculators/,
│   │                         context_builder, evidence_store, audit_store (hash-chained),
│   │                         response_store, thinking_store, tag_matcher, revision_resolver,
│   │                         ingest, kl_io, knowledge, protocols, resources
│   ├── orchestration/        orchestrator, routing matrix/templates, DAG executor, runs,
│   │                         HITL queue, narration, btw status agent
│   ├── llm/                  Ollama client, prompts, fake/null clients
│   ├── models/               model registry (registry.yaml), per-call router, RoutedLLM
│   ├── memory/               session store and follow-up rewriting
│   ├── app/                  cli.py + cli_ext.py, api.py + api_ext.py, credentials, deps,
│   │                         thinking_display
│   ├── security/             auth, roles, classification, policy, guard, leakcheck (release gate),
│   │                         escalation (grants), audit, records, redteam, setup, totp, tls,
│   │                         vault, vault_backend
│   ├── sovereignty/          hashchain, egress guard, netmonitor, signed model packages, service
│   ├── sandbox/              subprocess/Docker runner, manifest + vendor/, verify, runlog
│   ├── tools/                named local tools and the agent loop
│   ├── intake/               on-device OCR, vision, intake pipeline
│   ├── deliverables/         docx / pptx / xlsx builders and export
│   ├── review/               drafts with flagged figures and sign-off
│   ├── prompts/              7 agent prompt files
│   ├── benchmarks/           prompts.yaml (70 prompts, 16 categories) and runner
│   └── fixtures/             small mock knowledge artefacts
├── scripts/setup_security.py same as `python -m workbench setup-security`
├── data/
│   ├── raw/                  input PDFs
│   ├── parsed/               parser output and parse reports
│   ├── normalized/           <document>_normalized.json files
│   ├── knowledge/            profiles, glossaries, chunks, embeddings (+ ontology.json)
│   ├── checkpoints/          resumable state
│   ├── reports/              knowledge-layer reports and graph dumps
│   └── workbench/            cache, sessions, audit, thinking, uploads, reports, security,
│                             models, routing, sovereignty, sandbox, workspace, vault, drafts,
│                             deliverables
├── WebPage/                  React/Vite frontend
├── tests/                    knowledge_layer/ (132 tests) and workbench/ (533 tests)
├── docs/                     API, SECURITY, HOW_IT_WORKS, ACCESS_AND_ANSWERS, DEMO, PLAN,
│                             HANDOFF_KNOWLEDGE_LAYER_INTEGRATION, DOC_AUDIT, this file,
│                             architecture/, manual/index.html, schema/*.json
├── neo4j-data/               local Neo4j Community installation, gitignored
├── pyproject.toml            Python package and dependency definition (version 0.1.0)
├── SETUP.md                  setup and runtime prerequisites
├── walkthrough.md            original implementation walkthrough (obsolete snapshot)
└── README.md                 project overview and commands
```

The knowledge layer was moved from the older `src/` and `schemas/` layout into `knowledge_layer/`; imports were rewritten without changing the intended logic. The workbench depends on the knowledge layer through the `KnowledgeService` protocol instead of importing extraction internals directly.

## 5. Knowledge Layer: Document-to-Knowledge Pipeline

### 5.1 Pipeline phases

`knowledge_layer/pipeline.py` runs nine phases (with 7a/7b sub-phases). The implementation is resumable and phases can be skipped when checkpoints exist. The order below is the order in the code; note that table classification runs **before** normalization:

1. **Parse with Docling and validate the parse**
   - Scans `data/raw/` for PDFs.
   - Uses layout and table models, OCR, page windows, and provenance metadata.
   - Writes parsed elements, pages, tables, figures, metadata, and a parse report (`parse_validation.py` checks) under `data/parsed/<document>/`.
   - Checkpoints page windows so an interrupted parse can resume.

2. **Classify and reconstruct tables**
   - Uses deterministic rules and regular expressions, not an LLM.
   - Recognizes table families such as abbreviations, equipment, operating limits, safety, procedures, material balance, and comparison tables.
   - Runs before normalization so that page-furniture tables can be filtered by class.
   - Propagates merged-cell headers and removes duplicate/repeated content.

3. **Normalize**
   - Removes page furniture such as repeated headers, footers, running titles, approval blocks, table-of-contents noise, and figure placeholders.
   - Strips articles in headings where appropriate, for example `The Vacuum Distillation Unit` to `Vacuum Distillation Unit`.
   - Builds chapters from table-of-contents information and page headers.
   - Builds section hierarchy from heading numbers and chapter context.
   - Retains provenance and section paths for kept elements.
   - Detects ordered procedure blocks.

4. **Build document profile**
   - Records chapter and section orientation, cross references, referenced documents, standing instructions, and document metadata.
   - Detects document type, revision, plant/unit context, and pages.

5. **Build glossary**
   - Extracts abbreviations from abbreviation tables and inline definitions.
   - Produces normalized lookup maps for terms, abbreviations, and full forms.

6. **Chunk the document**
   - Creates canonical source text once and assigns a typed chunk category.
   - Chunk types include `procedure`, `table`, `specification`, `equipment`, `control`, `safety`, `upset`, `narrative`, and `document_control`.
   - Chunks retain document id, page range, section path, chapter, source element references, and procedure/table links.
   - Chunk boundaries are structure-aware and avoid overlap contamination and figure-only fragments.

7. **Neo4j setup, document graph (7a) and embeddings (7b)**
   - Neo4j schema, the document node and glossary terms are written when Neo4j is reachable.
   - 7a writes the deterministic document graph: chapters, sections, chunks, procedures with ordered `ProcedureStep` records and `NEXT` links, standing instructions, references, and cross references.
   - 7b embeds chunks with `BAAI/bge-small-en-v1.5` (384 dimensions), caches the vectors on disk, and stores them on Neo4j chunk nodes.
   - `--clean` and `--rebuild` clear **all** Neo4j data at the start of this phase for *each* PDF, so with several PDFs in `data/raw` only the last document survives in the graph (see Known limitations).

8. **Per-chunk extraction, validation and graph insertion**
   - When Ollama and the configured model are available (and `--no-llm` is not passed), the LLM passes `llm_pass1` / `llm_pass2` are attempted first. Prompts are constrained by chunk type and schema, and every model-produced item must include evidence. **Status: these passes have never produced output. Every `data/reports/*/extraction_by_source.json` contains only `rule` and `table` sources, i.e. 0 LLM entities, relations and claims.** The graph is therefore deterministic-only.
   - The deterministic passes always run: rule relations (routing, composition, suction/discharge, procedure ordering), specification-block, prose-value and table-cell claims.
   - The validator checks schema, evidence grounding, units, domain compatibility, and contradictions per item before insertion; malformed or unsupported items are rejected, not repaired into facts.
   - `GraphInserter` writes entities, claims, relations and evidence links idempotently, including `CONFLICTS_WITH` / `CORROBORATES` edges.

9. **Reporting**
   - A Markdown report, source breakdown (`extraction_by_source.json`), graph dump, and checkpoint state are generated.

### 5.2 Checkpointing and restart behavior

`PipelineCheckpoint` records completed phases and chunk-level extraction state. A rerun of `python -m knowledge_layer`:

- skips completed parse/normalize/profile/glossary/chunk phases;
- skips completed extraction chunks;
- resumes after a model timeout or process interruption;
- preserves successful output while recording failures separately;
- allows failed chunks to be retried with `--retry-failed`.

This is especially important for long documents and local inference on a 4 GB GPU.

### 5.3 Evidence-first extraction

Every entity, relation, and claim carries provenance fields such as:

- `document_id`;
- page or page range;
- `chunk_id`;
- evidence sentence or table row;
- source type: `rule` or `table` in all current output; `llm_pass1` / `llm_pass2` are defined source types but no record carries them yet;
- grounding and confidence.

The system does not treat co-occurrence as a process relation. For example, two items appearing in the same paragraph do not automatically create a `FEEDS` edge. Similarly, a motor does not receive a `SUCTION_FROM` relation merely because it is near a pump.

### 5.4 Claim context keys

A claim is not just `subject / predicate / value`. Its context determines whether it can be compared to another claim.

The context key (`Claim.context_key()` in `knowledge_layer/schemas/claims.py`) is built from the predicate plus:

- parameter role: `design`, `rated`, `normal`, `operating`, `minimum`, `maximum`, `mechanical_design`, `test`, `relief`, `alarm`, `trip`;
- location: `suction`, `discharge`, `inlet`, `outlet`, `top`, `bottom`, `flash_zone`, `shell`, `tube`;
- operating mode: `normal_operation`, `startup`, `shutdown`, `emergency`, `temporary`, `upset`;
- scenario: Basrah, Bombay High, BH mode, PG mode, SKO operation, Kuwait, Kirkuk, and similar cases;
- pressure basis: absolute versus gauge;
- qualifier: table label, case, reference condition, distillation point, or similar qualifier.

Claims also carry a temporal status (current, historical, design, defunct), but it is **not** part of the context key.

Two values are potential conflicts only when their context keys match and they come from different sentences or rows. Equal values with the same context corroborate. Different contexts remain separate facts.

Example for the crude charge pump:

- normal flow: 482 m3/h;
- minimum flow: 219 m3/h;
- design flow: 520 m3/h;
- suction pressure: 2.0 kg/cm2A;
- discharge pressure: 24.45 kg/cm2A;
- design pressure: 31.7 kg/cm2A.

These values are not one contradictory set because their roles and locations differ.

### 5.5 Entity identity

`entity_identity.py` normalizes equipment tags such as:

- `11-V-02` and `11 V 02`;
- `10-P-01A/B`;
- `11-PM-01A/B`;
- `TIC-1001`.

A stable UID is derived from the canonical tag. Parent/train relationships are represented with `HAS_TRAIN`. Untagged names remain document-scoped unless `entity_resolver.py` creates a cautious `SAME_AS` link above a confidence threshold. Unknown type labels are preserved and can later be promoted by the corpus-driven ontology manager.

The workbench's tag matcher separately handles probable user typos and OCR variants. Exact backend resolution is intentionally conservative so a pump cannot silently become a heater because of fuzzy matching.

## 6. Parsed Corpus and Observed Data

The repository contains parsed artefacts for six documents, and the workbench loads all six (CDU operating manual, API610 pump operation manual, API560 comparison, Bulletin-5EH Steam Jet Ejectors, Crude desalter, ESBWR). The CDU operating manual is the primary end-to-end corpus used by the benchmark and demonstrations.

### 6.1 CDU operating manual parse

The parse report records:

- 562 PDF pages, all processed and all with text;
- 7,732 elements;
- 875,207 non-table text characters;
- 1,177 headings and 3,436 list items;
- 814 detected tables, including 527 page-header boxes and 287 content tables;
- 15,506 table cells, all non-empty;
- 1,148 merged cells;
- 600 figures and 600 exported images;
- 0 parser errors and 0 parser warnings, but one validation check at **WARN**: `reading_order_monotonic_across_pages` reports 19 reading-order inversions across page boundaries (`data/parsed/CDU operating manual/parse_report.md`);
- consistent provenance and bounding boxes;
- CUDA Docling processing on an NVIDIA GTX 1650 4 GB;
- approximately 43 minutes 55 seconds for parsing.

The deterministic normalization/chunking results for the manual, as they stand in the current artefacts (`data/knowledge/CDU operating manual/document_profile.json` and the workbench index cache `data/workbench/cache/CDU operating manual/index.json`):

- 36 chapters;
- 1,119 sections in the workbench index (the knowledge-layer document profile lists 1,084);
- 182 procedures;
- 1,503 ordered procedure steps with `NEXT` order;
- 467 typed chunks;
- 64 referenced documents;
- 20 in-document cross references;
- 19 standing instructions;
- 96 abbreviations in the document profile and 172 glossary terms in the workbench index.

Current workbench index graph (files backend `build_stats`): **1,056 entities, 276 relations, 1,531 claims** (703 tagged entities).

Current knowledge-layer extraction report for the manual (`data/reports/CDU operating manual/extraction_by_source.json` and `report.md`): 467 chunks, 397 processed deterministically and 70 skipped, **LLM ok 0**; accepted items are 116 rule relations, 40 rule claims (plus 21 weak) and 364 table claims. There are no LLM-sourced items.

*Historical snapshot, not reproducible from the current artefacts:* earlier documentation quoted a Neo4j graph of 1,528 table/specification/prose claims, 531 entities, 169 typed relations and 649 chunk-to-section links. Those figures come from an older Neo4j run and should not be cited as current.

The reports use different aggregate counts at different stages because the knowledge-layer graph, the files index, and the workbench index apply different filters and aggregation rules. Those figures should be interpreted as stage-specific metrics rather than expected equality.

### 6.2 Other parsed documents

The parse reports show successful deterministic parsing for:

| Document | Pages | Tables | Text characters | Notes |
|---|---:|---:|---:|---|
| API 610 pump operation manual | 82 | 17 | 84,401 | All pages processed; no parser errors; no matching abbreviation/TOC/operating-limit/safety tables in the representative checks. |
| API 560 comparison document | 105 | 124 | 68,852 | Edition comparison tables with many merged cells; all pages processed and content tables present. |
| Crude desalter | 93 | 24 | 71,555 | Contains troubleshooting and corrective-action tables; all pages processed. |
| Bulletin 5EH Steam Jet Ejectors | 20 | 8 | 39,443 | All pages processed; equipment/index table detected. |
| ESBWR | 504 | 50 | 955,026 | 499 pages contain text; five pages are empty, with a warning for a consecutive empty run near the end. |

The parser reports consistently show successful windows, provenance consistency, available table cells, and no placeholder table text. Some representative semantic checks are warnings simply because a given document does not contain that table category.

Extraction status per document (`data/reports/<document>/report.md`): every report records **LLM ok 0**. The API560 and Bulletin-5EH reports record 0 chunks processed in phase 8; API610 (72), Crude desalter (58) and ESBWR (405) were processed deterministically only.

### 6.3 Knowledge-layer output layout

```text
data/parsed/<document>/
  parsed_document.json
  tables.json
  metadata.json
  parse_report.md
  images/

data/normalized/
  <document>_normalized.json

data/knowledge/<document>/
  document_profile.json
  glossary.json
  chunks.json
  chunk_embeddings.json

data/checkpoints/
  <document>_parse.json
  <document>_pipeline.json

data/reports/<document>/
  report.md
  extraction_by_source.json
  graph_dump.json
```

## 7. Knowledge Graph Model

The graph has three conceptual layers:

1. **Document graph**: chapters, sections, procedures, steps, standing instructions, references, and cross references.
2. **Domain graph**: equipment/entities and typed engineering relationships.
3. **Evidence layer**: claims, chunks, supporting evidence, conflicts, and corroboration.

Representative structure:

```text
(:Document)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION]->(:Section)
(:Document)-[:HAS_CHUNK]->(:Chunk)-[:IN_SECTION]->(:Section)
(:Document)-[:HAS_PROCEDURE]->(:Procedure)-[:HAS_STEP {sequence}]->(:ProcedureStep)-[:NEXT]->(:ProcedureStep)
(:Procedure)-[:APPLIES_TO]->(:Entity)
(:ProcedureStep)-[:MENTIONS]->(:Entity)
(:Section)-[:REFERENCES]->(:Chapter|:Section)
(:Document)-[:HAS_STANDING_INSTRUCTION]->(:StandingInstruction)-[:INCORPORATED_IN]->(:Chapter)
(:Document)-[:REFERENCES_DOCUMENT]->(:DocumentReference)
(:Document)-[:HAS_ENTITY]->(:Entity)
(:Chunk)-[:MENTIONS]->(:Entity)
(:Entity)-[:HAS_TRAIN]->(:Entity)
(:Entity)-[:FEEDS|SUCTION_FROM|DISCHARGES_TO|PART_OF|DRIVEN_BY|CONTROLLED_BY]->(:Entity)
(:Entity)-[:HAS_CLAIM]->(:Claim)
(:Chunk)-[:SUPPORTS]->(:Claim)
(:Claim)-[:CONFLICTS_WITH|CORROBORATES]->(:Claim)
```

Every relation retains evidence, page, document, chunk, source, and confidence properties where applicable. The `source` property would distinguish rule-derived from LLM-derived relations, but no LLM-derived relations exist in the current graph.

## 8. Workbench Contracts and Data Flow

The workbench communicates through typed contracts rather than agent-to-agent imports:

```text
UserRequest
  -> StructuredRequest
  -> ContextPackage
  -> Plan
  -> AgentResult collection
  -> VerificationResult
  -> Governance decision
  -> FinalResponse
```

The central abstraction is `KnowledgeService` in `workbench/services/protocols.py`. It exposes typed records such as:

- `DocumentInfo`;
- `EntityRecord`;
- `ClaimRecord`;
- `RelationRecord`;
- `ProcedureRecord` and `StepRecord`;
- `ChunkRecord`;
- `SectionRecord`;
- `GlossaryRecord`;
- `ConflictRecord`;
- `DocumentReferenceRecord`;
- `StandingInstructionRecord`;
- `CrossReferenceRecord`.

The files backend loads the knowledge artefacts of every discovered document (six today) and builds in-memory `IndexStore` indexes, cached per document. For the CDU manual the current cache holds 1,056 entities, 1,531 claims, 276 relations, 182 procedures, and 467 chunks. The cache lives at `data/workbench/cache/<document>/index.json`. The backend setting is `auto` by default (`files` when artefacts exist, otherwise `mock`); `composite` layers session uploads over it; `neo4j` is a stub.

A cold index build is about 15 seconds and a cached load is about 3 seconds in the documented GTX 1650 environment.

## 9. The Agent System

Seventeen agent keys are registered in `workbench/agents/registry.py`: twelve primary named agents, four retrieval specialists (`lookup`, `graph`, `explanation`, `cross_document`), and `answer_composer`. Agents return `AgentResult` objects containing render blocks, evidence, checkable statements, confidence, safety flags, and missing requirements.

### 9.1 Understanding and cross-cutting agents

| Agent | Responsibility |
|---|---|
| Task Classifier | Rules-first intent classification; calls the model only when rule confidence is below the threshold. |
| Context Resolver | Resolves equipment, values, units, symptoms, actions, scenarios, modes, pronouns, ambiguity, and evidence requirements. |
| Safety Agent | Handles safety-sensitive review and restricted/bypass requests across the workflow. |
| Planner | Creates and validates task-specific DAGs, extends compound requests, and optionally refines planning requests with the LLM. |
| Verification Agent | Checks evidence keys, numbers, tags, grounding, units, and context consistency. |
| Governance Agent | Applies policy, confidence, human-review rules, citations, and final response decisions. |
| Answer Composer | Converts retrieved material into readable prose and checks the prose against a compact evidence brief. |

### 9.2 Retrieval and domain agents

| Agent | Responsibility |
|---|---|
| Lookup | Finds documented values, equipment identity, and operating limits. |
| Graph | Traces flow paths, neighbours, instruments, and standby equipment. |
| Explanation | Retrieves grounded passages for why/consequence questions and optionally summarizes them. |
| Cross-document | Finds matching sections, references, standing instructions, and related documents. |
| Procedure | Returns prerequisites and ordered documented steps. |
| Diagnostic | Finds upset sections, causes, checks, corrective actions, and marked topology hypotheses. |
| Calculation | Performs deterministic envelope and limit calculations; the LLM never does arithmetic. |
| Comparison | Aligns values for subjects, modes, scenarios, revisions, or equipment. |
| Revision/conflict | Lists values with source and context, groups conflicts, and ranks authority. |
| Report | Assembles gathered results into a Markdown report. |

### 9.3 Agent design rule

Agents do not call each other. The executor invokes agents according to the orchestrator's validated plan. Cross-cutting agents are inserted by orchestration hooks. This keeps dependencies visible in the plan and makes failures/replans auditable.

## 10. Request Workflow: Phases 0 Through 5

### Phase 0: Access

1. Receive text, session id, attachments, options, and authenticated principal.
2. Decide which documents the principal may read (tags, compartment allowlists, any presented one-time key) before reading knowledge records. The decision is written to the run audit and the security audit (`access_allowed` / `access_partial` / `access_denied`).
3. If the caller may read nothing and has no uploads in the conversation, the run stops here and an `access_denied` progress event is emitted.

### Phase 1: Understanding

1. Rewrite an elliptical follow-up into a standalone question (section 14.2).
2. Classify the task with weighted deterministic rules.
3. Use the LLM only when classification confidence is below the threshold (0.72) and the effort level permits it (every level except `low`).
4. Resolve equipment aliases, tags, numbers, units, scenario, operating mode, and follow-up references.
5. Apply tag correction only when confidence and margin justify adoption; otherwise ask a clarification question.
6. Detect `clear`, `sensitive`, or `restricted` safety status (the resolver's safety gate).
7. Produce `StructuredRequest`.

### Phase 2: Specialist retrieval

The `ContextBuilder` chooses a targeted route:

- `claims` for lookup, limits, comparisons, conflicts, and provenance;
- `graph` for multi-hop and flow questions;
- `proc` for procedures;
- `inventory` for surveys and corpus counts;
- `hybrid` for troubleshooting, explanation, safety, planning, reports, and cross-document questions;
- `none` for clarification or out-of-scope requests.

Hybrid search combines BM25 and embeddings with reciprocal-rank fusion (k = 60). The cross-encoder reranker is **off at `low` and `medium`** (the default) and on at `high` / `ultra` when the hardware profile has a reranker model; `low` also skips vectors. Retrieval is filtered by allowed documents and chunk types (a hard filter), with chapters as a soft preference, plus query context.

### Phase 3: Planning

The planner creates a task-specific DAG from templates. It can:

- add steps for compound requests;
- remove irrelevant optional steps;
- validate dependency references and cycles;
- include a limited model refinement for planning requests;
- preserve mandatory safety steps as the floor.

### Phase 4: Execution

The executor runs ready steps sequentially and records:

- agent and step id;
- goal and dependencies;
- timing;
- model calls;
- output blocks;
- evidence count;
- missing items;
- failures and summaries.

If a required step lacks evidence, the orchestrator can replan using a fallback route. The default replan budget is two at medium/high effort, zero at low effort, and three at ultra effort.

### Phase 5: Verification and governance

1. Verification checks each statement against evidence.
2. Answer Composer builds a short evidence brief and writes the human-facing response.
3. The composer rejects unsupported figures, tags, names, contradictory qualifiers, preambles, and excessive repetition.
4. A second failed composition check falls back to deterministic prose.
5. Governance applies safety, human-review, confidence, and citation rules. An `answered` response whose confidence is below `min_confidence_to_answer` (0.35) has its **status** changed to `needs_review` with a warning; this is separate from the `requires_human_review` flag, which the high-risk rules in section 13.8 set.
6. The security envelope, any escalation offer, and the classification stamp are attached, and the release gate re-checks provenance (a failure withholds the answer with status `blocked`).
7. The audit trail (hash-chained) and thinking trace are saved.
8. `FinalResponse` is returned.

## 11. Supported Request Types and Routing

There are fifteen task types (`workbench/core/request.py`), including `inventory`:

| Task | Typical route | Main output |
|---|---|---|
| `lookup` | claims | KPI/table and evidence |
| `multi_hop` | graph | graph and grounded explanation |
| `procedure` | proc | prerequisites, ordered steps, safety review |
| `troubleshooting` | hybrid | normal range, causes, checks, actions, topology, safety |
| `limits` | claims | deterministic limit gauge and safety interpretation |
| `explanation` | hybrid | cited passages and optional summary |
| `safety` | hybrid | precautions, isolation, PPE, interlocks, review flag |
| `comparison` | claims | aligned comparison matrix and differences |
| `conflict` | claims | all values, context groups, preferred/resolved value |
| `provenance` | claims | source and revision inventory |
| `planning` | hybrid | gaps and proposed work plan |
| `report` | hybrid | gathered material and Markdown report |
| `cross_document` | hybrid | matching documents, sections, references |
| `inventory` | inventory | class counts, tags, documents, chapters |
| `ambiguous` | none | clarification question or out-of-scope response |

### Example: limit check

For: `The crude charge pump is operating at 520 m3/h. Is this acceptable?`

1. Rules classify it as `limits`.
2. The resolver maps the phrase to the documented crude charge pump and extracts `520 m3/h` as `flow_rate`.
3. Claims retrieval finds minimum 219, normal/rated 482, and design 520 m3/h.
4. Calculation compares the value with the envelope.
5. The result is `within_design`, approximately 7.9% above normal.
6. Safety adds a supervision warning because the value is above normal even though it is within design.
7. Verification checks each number against table evidence.
8. Governance emits a cited answer, gauge, safety information, confidence, and audit data.

### Example: troubleshooting

For: `The crude charge pump discharge pressure is dropping. What should I check?`

The plan typically includes equipment identification, normal-range lookup, upset-section retrieval, documented causes, checks, corrective actions, graph topology, related procedures, and safety review. Inferred topology is marked as inferred; documented causes and checks retain their page evidence.

### Example: inventory

Inventory questions do not require a single subject. The system lists the entity index and applies the same equipment filter to both counts and rows. Plant-numbered equipment tags are used to avoid counting checklist entries such as `C-1` or standard identifiers such as `D-86` as plant equipment.

## 12. Answer Composition and Evidence

The answer is composed rather than dumped from retrieval results.

### 12.1 Composer process

1. Flatten retrieved values, relationships, steps, passages, safety points, limit verdicts, and missing evidence.
2. Build a compact brief containing the question, session context, entities, and evidence-bearing material.
3. Keep the brief within the configured token budget, 1,500 tokens by default.
4. Ask Ollama for structured `{answer, assumptions, not_documented}` when enabled (on by default at `medium`, `high` and `ultra`; off at `low` or with `RWB_LLM_ANSWER=off` / `RWB_LLM=off`).
5. Verify figures, tags, names, qualifiers, and source references against the brief.
6. Retry once if a check fails; otherwise use deterministic composition.

The model is never allowed to introduce a number or equipment tag that is absent from the evidence brief.

### 12.2 Evidence labels

The evidence store de-duplicates evidence and labels it `[1]`, `[2]`, and so on. Blocks reference those labels. Evidence records include document, page, chunk id, claim id, section path, source, revision, and source text.

### 12.3 Verification rules

Verification checks:

- every evidence key exists;
- every cited number occurs in the evidence, allowing formatting variants such as `24.45` and `24,45`;
- equipment tags resolve within the reader's view;
- unsupported LLM narrative is removed;
- conflicting qualifiers are not silently combined;
- missing facts are reported in `missing` or `warnings`.

`UNKNOWN` is preferred over a guessed value.

### 12.4 Typed response blocks

The frontend can render these without parsing prose:

- `text`;
- `callout`;
- `kpi`;
- `table`;
- `steps`;
- `graph`;
- `plan`;
- `limit_gauge`;
- `comparison`;
- `conflict`;
- `safety`;
- `clarification`;
- `image` (reserved for future figure crops);
- `confidence`;
- `evidence`;
- `audit`.

Normal block ordering is: lead answer/callout, primary blocks, safety, verification notes when needed, executed plan for larger runs, human-review callout, confidence, evidence, and audit.

## 13. Security and Access Control

### 13.1 Roles and document tags

```text
user     -> INTERNAL
manager  -> INTERNAL + CONFIDENTIAL
admin    -> INTERNAL + CONFIDENTIAL + SECRET
guest    -> nothing
```

The default rule is role level greater than or equal to document tag level. Documents may instead have an explicit role allowlist, called a compartment. `roles: null` uses the tag ladder; `roles: [...]` grants exactly to those roles.

Typical deployment classification:

- standards and vendor manuals: `INTERNAL`;
- equipment documentation: `CONFIDENTIAL`;
- unit operating manuals: `SECRET`.

The CDU operating manual is therefore readable by an administrator in the documented setup.

### 13.2 Guarded knowledge service

The access check is enforced below the agents and below the prompt:

- keyword and semantic search is filtered;
- equipment lookup is filtered;
- graph neighbour walks stop at the boundary;
- fetch-by-id returns nothing when unauthorized;
- counts and inventories include only readable material;
- records without provenance are withheld;
- `setup-security` pins a tag on every document present at setup: CDU manual `SECRET`, Crude desalter `CONFIDENTIAL`, and every other document `INTERNAL` (`security/setup.py`). The `SECRET` fallback applies only to documents the registry classifies later that match none of its default rules, and to record ids the registry has never classified (`security/classification.py`);
- session uploads are tagged `CONFIDENTIAL` by rule (the uploader can still read their own file).

A classification-file parse error does **not** make everything `SECRET`: the registry forgets the file and re-derives rule tags, which drops pins and compartment allowlists and can therefore widen access (see Known limitations).

Within the question-answering path, prompt injection cannot reveal a document that was never placed in the agent's accessible knowledge service. This guarantee does not cover the sandbox: the subprocess sandbox can read files on disk directly (see Known limitations).

### 13.3 Access escalation

When a question appears to require a higher classification:

1. the requester receives an answer from accessible material, plus a notice that restricted material bears on the question;
2. the system determines the exact opaque record ids needed;
3. the request is routed to the lowest role that can approve the records (any role cleared for that material, other than the requester, may decide it);
4. the approver sees the actual passages and decides;
5. approval creates a signed, single-use key valid for 30 minutes (`grant_ttl_seconds`) for one person, one question, and one record list;
6. the requester resubmits the exact question with the key;
7. the key is spent on an `answered` / `needs_review` run and cannot then be replayed (there is no lock between redeeming and spending the key, see Known limitations).

The key is not a role promotion and does not grant document-wide access.

### 13.4 Grant validation

The key verification checks signature, secret digest, revocation, use count, expiry, owner, question match, and record-list scope hash. HMAC prevents editing grant fields or fabricating a valid signature.

### 13.5 Passwords, tokens, and lockout

- passwords use per-account salts and PBKDF2-HMAC-SHA256 with 240,000 iterations;
- unknown username and wrong-password responses are **not** identical (different messages; a locked account returns HTTP 423), so usernames can be enumerated;
- five failed attempts within the lockout policy cause a fifteen-minute lockout (wrong TOTP codes do not count toward it);
- bearer tokens are stored as SHA-256 digests;
- tokens expire after eight hours;
- role changes invalidate old tokens;
- security events are appended to `data/workbench/security/security.jsonl`.

### 13.6 MFA

TOTP is optional and off by default (no role is required to present a code). `RWB_MFA=on` requires it for manager and admin, `RWB_MFA=<role list>` (for example `admin` or `manager,admin`) sets the roles explicitly, and `RWB_MFA=off` clears it. Over HTTP, a correct password for an MFA-required account returns **428** and the client re-posts with `code`. The CLI sign-in does not handle this challenge (see Known limitations). Demo-only display of expected codes uses `RWB_OTP_DEMO=1` and must not be used in deployment.

### 13.7 Release gate

Before release, the gate (`security/leakcheck.py`) checks:

1. all citations belong to readable documents;
2. all equipment tags resolve in the reader's view;
3. all figures occur in attached evidence.

Only the provenance check (1) blocks: a failure withholds the answer rather than redacting it, logs a security event, and returns status `blocked`. The tag and figure checks (2, 3) are recorded at `note` severity and do not block release.

### 13.8 Human-in-the-loop rules

Review is required for:

- restricted requests;
- values outside design envelope;
- emergency, isolation, changeover, or shutdown procedures;
- danger-level precautions;
- all planning answers.

These set `requires_human_review` and add a "Human review required" callout. A low-confidence answer (below 0.35) is handled differently: its status becomes `needs_review` rather than `answered` (section 10, Phase 5).

The answer can still be delivered with a review flag because the workbench advises and never actuates plant equipment. Pending review items are held in `data/workbench/audit/hitl.jsonl` and exposed at `GET /reviews` (manager or above); no frontend screen consumes that queue yet.

Generated deliverables have their own review path (section 21): drafts carry flagged figures, sign-off is refused (409) while any flag is open, and **sign-off or rejection requires a manager or administrator** (`review/drafts.py`), even for the draft's owner.

## 14. Sessions, Follow-ups, and Tag Correction

### 14.1 Sessions

A client chooses `session_id` (the API default is `"web"`). Sessions are namespaced by principal: state is stored under `data/workbench/sessions/<owner>__<session_id>.json` (characters outside `[A-Za-z0-9-_.]` replaced), and a state stored under another owner's name is discarded on load. Managers and administrators can read lower-ranked users' conversations through the supervision endpoints (`/logs/conversations`, logged as `conversation_viewed`). Session state retains recent turns, structured interpretations, entities, parameters, status, corrections, and composed answers.

Use one session per conversation thread. Use a new session when context carry-over is undesirable.

### 14.2 Follow-up rewriting

A dependent turn is made standalone before classification:

- **substitution**: `What if we use 11-E-01 instead?` becomes a comparison with the previous subject;
- **elaboration**: `And at startup?` attaches the new angle to the previous subject;
- **continuation**: `What is its design pressure?` replaces the pronoun with the previous subject;
- **new**: a self-contained subject is left unchanged.

The model is used for rewriting only when deterministic rules cannot resolve a clearly dependent sentence, and the rewritten version must name the carried subject.

### 14.3 Tag correction

A user-entered tag is decomposed into unit, class, number, and train. Equipment wording provides an additional class signal. Parent/train variants are collapsed for candidate comparison.

A correction is adopted only when it is clearly better, such as a high score with a sufficient margin or an exact field match. When candidates tie, the workbench asks for clarification. The correction is shown at the start of the answer so the subject is never hidden.

## 15. Hardware, Models, and Resource Management

Hardware profiles are selected from detected VRAM and can be overridden with `RWB_PROFILE`.

| Profile | Default text model | Vision model | Context | Reranker model configured | Diagnosis LLM (profile flag) |
|---|---|---|---:|---|---|
| `cpu` | qwen3.5:2b | qwen3.5:2b | 4k | none | off |
| `gpu_4gb` | qwen3:4b | qwen3.5:2b on demand | 4k | bge-reranker-base on CPU | off |
| `gpu_8gb` | qwen3.5:4b | qwen3.5:4b | 8k | bge-reranker-base on CPU | on |
| `gpu_12gb` | qwen3.5:9b | qwen3.5:9b | 16k | bge-reranker-v2-m3 on GPU | on |
| `gpu_16gb` | qwen3.5:9b (gpt-oss:20b text-only alternative via `RWB_LLM_MODEL`) | qwen3.5:9b | 32k | bge-reranker-v2-m3 on GPU | on |
| `gpu_24gb` | qwen3.6:27b | qwen3.6:27b | 32k | bge-reranker-v2-m3 on GPU | on |

Two columns need qualifying because the effort level overrides them (`WorkbenchConfig.apply_effort` runs after `apply_profile`):

- **Reranker:** the column shows the model the profile makes available. It is only *used* at `high` and `ultra`; at `low` and the default `medium` the reranker is off on every profile. `RWB_RERANKER=off` disables it everywhere.
- **Diagnosis structuring (LLM extraction of causes / checks / actions):** the profile flag is overwritten by the effort level, so in practice it is **on only at `ultra`, on any profile** (including `gpu_4gb`), and off at `low` / `medium` / `high` even on large GPUs. `RWB_LLM_EXTRACTION=on|off` is applied last and overrides both.

### 15.1 Model registry and router

Routing is **on by default** (`RWB_ROUTING=off` pins every call to the profile's text model). `workbench/models/registry.yaml` (plus an optional local override `data/workbench/models/registry.local.yaml`) declares seven models, each with a 0-1 capability score per task kind (classification, resolution, extraction, summarization, composition, reasoning, calculation, code, vision, planning, tool_agent) and a `min_vram_mb`:

| Model | min VRAM (MB) | Notes |
|---|---:|---|
| qwen3:4b | 3500 | default text model on `gpu_4gb` |
| qwen3.5:2b | 3500 | the only vision-capable model that fits a 4 GB card |
| deepseek-r1:7b | 6000 | also the knowledge-layer extraction default (`RKL_OLLAMA_MODEL`) |
| qwen2.5-coder:7b | 6000 | code tasks |
| qwen3.5:9b | 11500 | text + vision on 12-16 GB |
| gpt-oss:20b | 15500 | text only |
| qwen3.6:27b | 23000 | text + vision on 24 GB |

Router behaviour (`workbench/models/router.py`): each agent names the *purpose* of a call (for example `compose_answer`), which maps to a task kind. The router scores every registered model on that kind, discards models not installed in Ollama, penalises models that would spill off the GPU (the `fast` budget, used on `cpu` and `gpu_4gb`, penalises spill and long thinking harder), and keeps the resident model when a better one wins by less than the swap margin (0.08), because a swap costs 5-15 s on a 4 GB card. If no registered model qualifies it falls back to the profile's default text (or vision) model. Each decision (chosen model, every candidate's score and the reason) is written to the hash-chained routing log `data/workbench/routing/routing.jsonl`, and every audit block carries `models_used` and `routing`. `decompose` splits a hybrid request (for example "read a scan, calculate a margin, write a note") into routed sub-tasks; `workbench route "<text>"` shows this without calling a model.

On the 4 GB machine only qwen3:4b and qwen3.5:2b need to be pulled for the workbench; the other registry entries are simply skipped as not installed.

Resource rules:

- the resource manager keeps one model resident at a time; with routing on, different calls within one run may still use different models, at the cost of a swap;
- the vision model is loaded only for image description and released immediately;
- idle unload is approximately 45 seconds on the 4 GB/CPU profiles and 180 seconds on larger profiles;
- `RWB_KEEP_WARM=1` disables idle unloading;
- the bge-small embedder and reranker are lazy CPU models;
- embeddings use `BAAI/bge-small-en-v1.5` with 384 dimensions;
- lookup, limits, comparison, and conflict requests use claim indexes without text retrieval;
- hybrid retrieval is reserved for tasks that need passages;
- the reranker, when enabled (`high` / `ultra`), runs once per request;
- structured LLM calls use JSON schema output and `think=false` by default (the report summary is not schema-constrained);
- the air-gap egress guard is on by default: it refuses and logs in-process socket connections except to loopback, private / link-local / ULA ranges and the configured LLM and Neo4j hosts, and it sets `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE`, so the embedding and reranker models must already be in the local Hugging Face cache.

Which effort levels call the model: only `low` is model-free. The default `medium` calls the LLM for the Answer Composer, for classification when the rules are unsure, and for follow-up rewriting. `high` adds entity guessing, narrative and plan refinement; `ultra` adds diagnosis structuring.

On the documented GTX 1650:

- a short structured model call takes about 5–6 seconds after model load;
- the first call pays model-load cost;
- the embedder takes approximately 6 seconds to load;
- the CPU reranker takes approximately 29 seconds to load;
- lookup and limit tasks can complete in tens of milliseconds when no model is called (`--effort low` or `RWB_LLM=off`); at `medium` the composed answer adds a model call;
- diagnosis structuring is off by default because one call takes roughly 50–100 seconds on a 4 GB card (the figure in `workbench/config.py`) and deterministic extraction is preferred; as noted above it is switched on by `ultra` effort regardless of profile.

## 16. CLI, API, and Frontend

### 16.1 Installation

Requirements:

- Python 3.10 or newer; development was performed on Python 3.13;
- Neo4j 5.11+ or the documented Neo4j Community server: optional for both the pipeline (the deterministic artefacts are still written) and the workbench (the files backend does not use it);
- Ollama with the workbench models (qwen3:4b and qwen3.5:2b on `gpu_4gb`) if composed answers and model fallbacks are desired; without it the workbench runs deterministically;
- Node.js / npm for the `WebPage/` frontend;
- CUDA-capable GPU is recommended for Docling parsing but CPU fallback exists;
- `BAAI/bge-small-en-v1.5` (and the reranker model, if used) must be downloaded and cached **before** running with the air-gap guard on, since the guard sets the Hugging Face offline flags.

Windows setup:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pip install --no-cache-dir "torch==2.14.0+cu126" "torchvision==0.29.0+cu126" --index-url https://download.pytorch.org/whl/cu126
python -c "import torch; print(torch.cuda.is_available())"
python knowledge_layer\scripts\validate_environment.py
```

Neo4j example:

```powershell
neo4j-data\neo4j-community-2026.05.0\bin\neo4j-admin.bat dbms set-initial-password refinery2024
powershell -ExecutionPolicy Bypass -File knowledge_layer\scripts\start_neo4j.ps1 -Detached
```

Only run one Neo4j instance on ports 7687/7474. Override connection values with `RKL_NEO4J_URI`, `RKL_NEO4J_USER`, and `RKL_NEO4J_PASSWORD`.

Ollama:

```powershell
ollama pull qwen3:4b          # workbench text model on gpu_4gb
ollama pull qwen3.5:2b        # workbench vision model (and cpu profile text model)
ollama pull deepseek-r1:7b    # knowledge-layer extraction default (RKL_OLLAMA_MODEL); optional
```

Other models in `workbench/models/registry.yaml` are used only if they are installed and fit the hardware.

### 16.2 Knowledge-layer commands

```powershell
python -m knowledge_layer
python -m knowledge_layer --no-llm
python -m knowledge_layer --rebuild
python -m knowledge_layer --clean
python -m knowledge_layer --max-pages 65
python -m knowledge_layer --retry-failed
python knowledge_layer\scripts\run_parse.py --expected-pages 562
python knowledge_layer\scripts\audit_pipeline.py "CDU operating manual" --pages 61,225
python knowledge_layer\scripts\probe_extract_chunk.py "CDU operating manual" 181
python knowledge_layer\scripts\dump_graph.py --doc "CDU operating manual"
```

`--no-llm` builds deterministic structure, procedures, claims, and relations (in practice the output of every run so far, with or without the flag). `--rebuild` reuses the parse but reruns downstream phases after normalizer/chunker changes and clears Neo4j. `--clean` resets extraction and clears Neo4j. **Both clear all Neo4j data once per PDF**, so with several PDFs in `data/raw` only the last document remains in the graph. The flags are read directly from `sys.argv`; there is no argument parser, and `python -m knowledge_layer --help` runs the full pipeline instead of printing help.

### 16.3 Workbench commands

`python -m workbench [-v] <command>` has 40 subcommands (verified with `--help`; defined in `workbench/app/cli.py` and `cli_ext.py`). `-v/--verbose` is a **global** flag and goes before the subcommand; only `drafts` has its own `-v`.

**Asking questions**

```powershell
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
python -m workbench ask "..." [--session S] [--effort low|medium|high|ultra] [--no-thinking] [--detail] [--json] [--key RGK...]
python -m workbench repl [--session S] [--effort ...] [--no-thinking] [--detail]
python -m workbench trace [--limit N] [--show]
python -m workbench status
python -m workbench agents
python -m workbench schema [--out DIR]
python -m workbench bench [--category C ...] [--limit N] [--llm]
python -m workbench serve [--host 127.0.0.1] [--port 8000] [--tls] [--mtls]
```

**Accounts and sign-in**

```powershell
python -m workbench setup-security [--reset-passwords] [--random-passwords]   # same as scripts\setup_security.py
python -m workbench login [--user admin]
python -m workbench logout
python -m workbench whoami
python -m workbench passwd [--user U]
python -m workbench users [--add NAME --role user|manager|admin --name "Full Name"]
```

**Classification, escalation and security audit**

```powershell
python -m workbench security [--json]
python -m workbench classify [document] [--tag INTERNAL|CONFIDENTIAL|SECRET] [--reason R]
python -m workbench request-access "question" [--session S]
python -m workbench requests
python -m workbench approvals [--show] [--all]
python -m workbench approve REQUEST_ID [--note N]
python -m workbench deny REQUEST_ID [--note N]
python -m workbench revoke-key GRANT_ID
python -m workbench security-check [--passwords] [--no-injections] [--verbose-probes]
python -m workbench security-log [--event E] [--principal P] [--limit N] [--json]
```

**Models and sovereignty**

```powershell
python -m workbench models [--json]
python -m workbench route "text" [--budget normal|fast]
python -m workbench sovereignty [--deep] [--watch SECONDS] [--json]
python -m workbench audit-verify
python -m workbench vault status|seal|rotate|revoke
python -m workbench packages keygen|trust|sign|verify|import|log
```

**Tools, sandbox and intake**

```powershell
python -m workbench tools
python -m workbench tool NAME [--args JSON] [--session S] [--json]
python -m workbench agent "goal" [--session S] [--effort ...] [--max-iterations N] [--json]
python -m workbench sandbox-run FILE [--tests TESTS] [--json]
python -m workbench intake FILE [--no-vision] [--purpose general|pid|handwriting|photo|gauge] [--max-pages N] [--vision-calls N] [--json]
```

**Deliverables and review**

```powershell
python -m workbench responses [--limit N]
python -m workbench export RESPONSE_ID [--format docx|pptx|xlsx|md] [--title T] [--out PATH]
python -m workbench drafts [--pending] [-v]
python -m workbench draft-resolve DRAFT_ID FIGURE_ID accepted|corrected|removed [--value V] [--note N]
python -m workbench draft-signoff DRAFT_ID [--reject] [--note N]
```

`--effort low` is the recommended mode when the GPU is busy. It uses deterministic indexes, never calls the model, and still exercises the task pipeline. Note that `serve` defaults to port **8000**, while the frontend's Vite proxy targets **8077**; start the server with `serve --port 8077` when using the web UI. In PowerShell 5.1, JSON passed to `tool --args` needs its inner quotes escaped.

### 16.4 FastAPI endpoints

Start the server:

```powershell
python -m workbench serve --host 127.0.0.1 --port 8077    # 8077 is what the frontend proxy expects; the default is 8000
```

OpenAPI documentation is available at `http://127.0.0.1:<port>/docs`. API version 0.3.0.

Complete route list (72 routes: 38 in `workbench/app/api.py`, 34 in `workbench/app/api_ext.py`). "Signed in" means a bearer token is required when access control is on. Routes marked **no auth** perform no authentication check at all (see Known limitations).

*Core (`api.py`)*

| Endpoint | Purpose | Access |
|---|---|---|
| `GET /health` | backend, model, profile, effort, documents, resources, routing, air-gap, vault, sandbox, TLS state | open |
| `GET /agents` | agent registry | open |
| `GET /schema` | live JSON schemas | open |
| `POST /auth/login` | sign in (`username`, `password`, optional `code`); 401 refused, 423 locked, 428 MFA code required | open |
| `GET /auth/mfa` | MFA state | signed in |
| `POST /auth/mfa/enrol` | start TOTP enrolment (returns `uri`) | signed in |
| `POST /auth/mfa/confirm` | confirm enrolment with a code | signed in |
| `DELETE /auth/mfa` | remove MFA | signed in (admin for others) |
| `POST /auth/logout` | revoke token | signed in |
| `GET /auth/whoami` | current identity and clearance | open (reports guest) |
| `POST /ask` | synchronous `FinalResponse` | signed in |
| `POST /runs` | asynchronous run start | token passed to the run; not refused at the door |
| `GET /runs` | list runs | **no auth** |
| `GET /runs/{run_id}` | run state and final response | **no auth** |
| `GET /runs/{run_id}/events` | SSE progress and final event | **no auth** |
| `POST /runs/{run_id}/btw` | status-agent question about one run | **no auth** |
| `POST /btw` | status-agent question | **no auth** |
| `GET /sessions` | caller's conversations | signed in |
| `GET /sessions/{session_id}` | one conversation and attachments | signed in (own only) |
| `DELETE /sessions/{session_id}` | delete a conversation | signed in |
| `GET /logs/conversations` | lower-ranked users' conversations | manager+ |
| `GET /logs/conversations/{owner}/{session_id}` | read one supervised conversation (logged `conversation_viewed`) | manager+ |
| `POST /upload` | session-only PDF/image attachment | signed in |
| `DELETE /upload` | drop a conversation's uploads | signed in |
| `POST /knowledge/documents/{document_id}/promote` | move an upload into the shared corpus (default tag `CONFIDENTIAL`) | manager+ |
| `GET /reviews` | pending HITL items | manager+ |
| `POST /reviews/{response_id}` | approve/reject a flagged answer | manager+ |
| `GET /audit/{session_id}` | run audit records (non-admins currently always get `[]`, see Known limitations) | signed in |
| `GET /stats` | permitted workspace counts | any caller; computed over what the caller may read |
| `GET /knowledge/tree` | readable/locked knowledge branches | any caller; computed over what the caller may read |
| `POST /security/documents/{document_id}/roles` | set a document's compartment allowlist | admin |
| `GET /security` | role schema, tags, caller's place | any caller; computed over what the caller may read |
| `POST /access-requests` | raise an access request | signed in |
| `GET /access-requests` | caller's own requests | signed in |
| `GET /approvals` | approval queue (`show_all=true` returns every request, see Known limitations) | signed in |
| `POST /approvals/{request_id}/approve` | approve and mint a one-time key | signed in; any role cleared for the requested material, not the requester |
| `POST /approvals/{request_id}/deny` | refuse | signed in; any role cleared for the requested material, not the requester |
| `GET /security-log` | security audit trail (scoped to own trail below admin) | manager+ |

*Extensions (`api_ext.py`, added 25 September 2026)*

| Endpoint | Purpose | Access |
|---|---|---|
| `GET /models` | registry, installed models, winners per task kind | open |
| `GET /models/routing` | routing log | open |
| `POST /models/route` | decompose and route a text without a model call | open |
| `POST /models/register` | add a model to the local registry | admin |
| `GET /models/packages` | signed-package log | manager+ |
| `POST /models/packages/verify` | verify a signed model package | manager+ |
| `POST /models/packages/import` | import a verified package | admin |
| `GET /sovereignty` | air-gap report (egress guard, monitor, chains; `deep=true` for full verification) | **no auth** |
| `GET /sovereignty/connections` | connection log | **no auth** |
| `POST /sovereignty/verify` | verify every chained log | manager+ |
| `GET /sovereignty/egress` | egress-guard log | **no auth** |
| `GET /vault` | vault status | **no auth check** (reads the token only to describe the caller) |
| `POST /vault/seal` | seal branch indexes | admin |
| `POST /vault/rotate/{role}` | rotate a role wrapping key | admin |
| `POST /vault/revoke` | revoke a role's key for a branch | admin |
| `GET /vault/tls` | TLS / mTLS state | open |
| `GET /tools` | named tools and sandbox info | open |
| `POST /tools/run` | run one tool | signed in |
| `POST /tools/agent` | agent loop over the tools | signed in |
| `GET /tools/workspace` | workspace files | signed in |
| `GET /tools/workspace/file` | download a workspace file | signed in |
| `GET /tools/calls` | tool-call log (own entries below admin) | signed in |
| `POST /sandbox/run` | run Python in the sandbox | signed in (any role, see Known limitations) |
| `GET /sandbox/runs` | sandbox run log | signed in |
| `GET /sandbox/manifest` | stdlib allow-list and vendor manifest | open |
| `POST /intake` | on-device OCR + vision over an upload | signed in |
| `POST /deliverables` | export a released answer as docx / pptx / xlsx / md draft | signed in |
| `GET /deliverables/responses` | released answers available for export | signed in |
| `GET /deliverables/{draft_id}/download` | download a deliverable | signed in |
| `GET /drafts` | drafts and sign-off state (all drafts for manager+) | signed in |
| `GET /drafts/{draft_id}` | one draft | owner or manager+ |
| `POST /drafts/{draft_id}/figures/{figure_id}/resolve` | resolve one flagged figure | owner or manager+ |
| `POST /drafts/{draft_id}/signoff` | sign off (409 while flags are open) | **manager+** |
| `POST /drafts/{draft_id}/reject` | reject a draft | **manager+** |

For long-running requests, the frontend should call `POST /runs`, subscribe to `GET /runs/{run_id}/events`, and render live events. Note that the stream currently emits **two** `final` events: the orchestrator's `{response_id, status}` marker first, then the real `FinalResponse`. A client must not close on the first `final` (see Known limitations).

### 16.5 SSE events

Events emitted by the code:

- `access_denied` (phase `0 Access`: the caller may read nothing and has no uploads, so the run stops);
- `phase_started` (including `0 Access`);
- `phase_finished`;
- `plan_created`;
- `agent_started`;
- `agent_finished`;
- `llm_call`;
- `replan`;
- `warning`;
- `error` (a release-gate failure yields status `blocked`);
- `final` (sent twice, see section 16.4).

Each event carries JSON progress data, phase, agent, step, message, timestamp, and optional thinking/model/decision fields. Replayed items carry only `{event, agent, message, replay, ts}`. The frontend should account for replayed events and avoid rendering replayed trace entries twice.

### 16.6 React/Vite frontend

`WebPage/` is the browser client. It talks only to the FastAPI service (both `api.py` and `api_ext.py` routes), through the Vite dev proxy `/api` → `http://127.0.0.1:8077` (so run `serve --port 8077`). Sign-in is not a route: any path shows the sign-in screen (with an MFA code step when required) until a session exists. Routes in `WebPage/src/main.tsx`:

- `/`: redirects to `/chat`;
- `/chat`: question, thinking, blocks, `btw`, upload (PDF or image), effort, access keys, per-answer export;
- `/overview`: workspace and active/waiting work;
- `/documents`: document classifications and readers;
- `/knowledge`: knowledge tree and reach (branch editor);
- `/access`: access requests and approval queue;
- `/security`: running security model;
- `/logs`: lower-ranked users' conversations for managers/admins;
- `/models`: model registry and routing;
- `/sovereignty`: air-gap status, connection log, chain verification (auto-refresh);
- `/tools`: named tools, sandbox runs, attack presets and run log;
- `/review`: deliverable drafts, flagged figures and sign-off;
- `/vault`: vault state, sealing and keys;
- `/account`: clearance and MFA;
- any other path: redirects to `/chat`.

There is no screen for the HITL answer-review queue (`/reviews`); the API wrapper exists but no page uses it.

Run it with:

```powershell
cd WebPage
npm install
npm run dev
npm run typecheck
npm run build
```

`npm run lint` is defined but currently fails because the eslint packages are not in `devDependencies`.

The frontend keeps the token in `sessionStorage`, not `localStorage` or cookies. Conversations are stored server-side. The UI uses structured security envelopes rather than scraping answer text. Markdown is rendered by a constrained React renderer that never interprets arbitrary HTML.

## 17. Attachments and Images

PDF attachments are session-scoped:

- saved below `data/workbench/uploads/<username>/<session_id>/`;
- parsed through the same knowledge-layer pipeline, in a background run (poll `/runs/{run_id}`);
- indexed only for the conversation that uploaded them (`Orchestrator.session_uploads`), not added to the shared knowledge service;
- cached by content hash under `data/workbench/uploads/_cache/`;
- not written into the shared `data/knowledge` corpus;
- tagged `CONFIDENTIAL` by rule (`UPLOAD_TAG`), as unreviewed material;
- readable by the uploader in that conversation, whatever their role;
- dropped with `DELETE /upload`; after a server restart the index is rebuilt from the session file and the parse cache the first time the conversation is touched again (`_ensure_uploads`), so PDF uploads survive restarts.

The UI must not allow a question against a PDF until its indexing run reaches `final_status: indexed`; otherwise the request may answer from the rest of the corpus and appear confidently wrong.

Images go through the multimodal intake pipeline (`workbench/intake/`): on-device RapidOCR with per-line confidence first, then one vision-model call with a purpose prompt chosen from the upload note (general, P&ID, handwriting, photo, gauge). The OCR and vision text are stored as session notes the Context Resolver can use; OCR lines below the confidence threshold (0.6 by default, `RWB_OCR_THRESHOLD`) are flagged, and flagged figures open a review draft. If intake fails, the image falls back to a plain vision-model description. Image content is not turned into shared knowledge records, and the `image` block type is still reserved for future figure crops.

Promotion (`POST /knowledge/documents/{id}/promote`) is a deliberate manager-or-above operation that re-parses the document into the shared knowledge layer and assigns `CONFIDENTIAL` unless another tag is explicitly provided.

## 18. Thinking Traces, Auditing, and Observability

The CLI and SSE stream expose structured reasoning progress without exposing unsupported private model narrative as fact.

- `orchestration/narration.py` turns structured outcomes into concise reasoning lines;
- `app/thinking_display.py` renders phase/agent/decision/timing in the terminal;
- `services/thinking_store.py` stores JSON traces under `data/workbench/thinking/`;
- audit JSONL records are stored under `data/workbench/audit/` (one file per session, written under the raw session id), with HITL items in `audit/hitl.jsonl`;
- security events are stored under `data/workbench/security/security.jsonl`;
- Markdown reports are stored under `data/workbench/reports/`.

### 18.1 Hash-chained logs and `audit-verify`

Append-only logs are hash-chained (`workbench/sovereignty/hashchain.py`): every entry carries `seq`, `ts`, `prev_hash` and `hash = SHA-256(seq | ts | prev_hash | canonical_json(payload))`, starting from 64 zeros, so altering, removing or reordering a past entry breaks every later hash. One `HashChainedLog` per path with a `.lock` sidecar serialises writers across processes, and pre-existing unchained files are chained retroactively. The chained logs are:

- security audit (`security/security.jsonl`);
- per-session run audits (`audit/*.jsonl`, except `hitl.jsonl`); the head hash is stamped on each answer's audit block as `chained_audit_hash`;
- network connection log and egress-guard log (`sovereignty/connections.jsonl`, `egress.jsonl`);
- routing log (`routing/routing.jsonl`);
- sandbox runs (`sandbox/runs.jsonl`) and tool calls (`workspace/tool_calls.jsonl`);
- vault key events (`security/kms/key_events.jsonl`) and model-package updates (`security/model_updates.jsonl`);
- draft / sign-off events (`drafts/events.jsonl`).

`python -m workbench audit-verify` verifies every chain in full, prints one row per log (the run audits aggregated into one row), and exits with code 2 if any chain is broken. `workbench sovereignty --deep`, `GET /sovereignty?deep=true` and `POST /sovereignty/verify` (manager+) expose the same verification. Limits: the chain is unkeyed SHA-256, so someone with write access can recompute it, and truncating the tail of a log is not detected.

A trace contains phase and agent timings, LLM calls and purposes, the plan DAG, replans, warnings, confidence basis, governance verdict, answer Markdown, and raw progress events.

The `btw` status agent reads run state only. It reports current phase, active agent, completed steps, timing, safety flags, model/resource state, and partial summaries without using the LLM or interrupting the request.

## 19. Testing and Benchmarks

The project includes knowledge-layer and workbench tests. `.venv\Scripts\python.exe -m pytest --collect-only -q` collects **665 tests: 533 workbench + 132 knowledge_layer** (older figures of 49, 94, 166 and 289 in other documents are stale). The suites include:

- a security suite (`test_security`, 97 tests) covering credentials, lockout, tokens, classification, guarding, release gate, HTTP behavior, and deliberate guard failure; MFA tests (`test_mfa`, 28); sandbox tests (`test_sandbox`, 19);
- composer tests (`test_composer`, 25) for grounded prose, unsupported figures/tags, preambles, repetition, and brief/full modes;
- follow-up tests (`test_followup`, 13) for substitution, elaboration, continuation, and new turns;
- tag matcher tests (`test_tag_matcher`, 17) for parsing, scoring, suggestion, adoption, and ambiguity.

Run:

```powershell
python -m pytest tests -q
python -m pytest tests/workbench -q
python -m workbench security-check --passwords
python -m workbench bench
python -m workbench bench --llm
python -m workbench -v bench --category troubleshooting limits
```

`-v` is the global verbose flag and must come before `bench`; `bench` has no `--verbose` option. Without `--llm`, `bench` sets `RWB_LLM=off`.

The benchmark (`workbench/benchmarks/prompts.yaml`) has **70 prompts in 16 categories**: lookup, multi_hop, procedure, troubleshooting, limits, explanation, safety, comparison, provenance, planning, report, cross_document, ambiguous, complex, inventory, out_of_scope. The latest run (2026-09-16 20:20, `data/workbench/reports/benchmark-20260916-202058.md`; files backend, null LLM, `gpu_4gb` profile) achieved:

- task accuracy: 1.0;
- agent coverage: 1.0;
- entity resolution: 1.0;
- block coverage: 1.0;
- safety handling: 1.0;
- answered runs: 1.0;
- mean confidence: 0.637;
- mean runtime 434 ms per prompt, 30.6 s in total;
- 0 LLM calls.

This is a deterministic, LLM-free run. It measures routing, resolution, block and safety behaviour, not the quality of model-composed prose. No LLM-on benchmark result is recorded.

The security red-team command exercises each role against ordinary questions, higher-tier questions, prompt injections, enumeration attempts, canary values, citation shape, and release behavior. Its probes are hard-coded to this corpus, and the "shape" check only inspects response status.

## 20. Configuration Reference

Every environment variable read by the code (verified in `workbench/config.py`, `knowledge_layer/config.py`, `workbench/security/auth.py`, `workbench/security/vault.py`, `workbench/services/resources.py`, `workbench/app/api.py`):

| Variable | Values / default when unset | Effect | Read in |
|---|---|---|---|
| `RKL_NEO4J_URI`, `RKL_NEO4J_USER`, `RKL_NEO4J_PASSWORD` | config defaults | knowledge-layer Neo4j connection | `knowledge_layer/config.py` |
| `RKL_OLLAMA_URL` | config default | knowledge-layer Ollama URL; also the workbench's Ollama URL when `RWB_OLLAMA_URL` is unset | both configs |
| `RKL_OLLAMA_MODEL` | `deepseek-r1:7b` | knowledge-layer extraction model | `knowledge_layer/config.py` |
| `RWB_AUTH` | on | `off` disables access control entirely (benchmark/tests only) | `workbench/config.py` |
| `RWB_PROFILE` | auto from VRAM | force `cpu`, `gpu_4gb`, `gpu_8gb`, `gpu_12gb`, `gpu_16gb`, `gpu_24gb` | `workbench/config.py` |
| `RWB_EFFORT` | `medium` | `low` / `medium` / `high` / `ultra` | `workbench/config.py` |
| `RWB_LLM` | on | `off` disables every model call | `workbench/config.py` |
| `RWB_LLM_MODEL` | profile's text model | override the text model | `workbench/config.py` |
| `RWB_VISION_MODEL` | profile's vision model | override the vision model | `workbench/config.py` |
| `RWB_OLLAMA_URL` | `http://localhost:11434` | workbench Ollama URL | `workbench/config.py` |
| `RWB_LLM_ANSWER` | on | `off` makes the Answer Composer deterministic | `workbench/config.py` |
| `RWB_LLM_EXTRACTION` | follows effort (on only at `ultra`) | `on` / `off` for diagnosis structuring; applied last | `workbench/config.py` |
| `RWB_RERANKER` | follows effort (on at `high` / `ultra`) | `off` disables the reranker | `workbench/config.py` |
| `RWB_ROUTING` | **on** | `off` pins every call to the profile's text model | `workbench/config.py` |
| `RWB_KNOWLEDGE_BACKEND` | **`auto`** | `auto` / `files` / `mock` / `neo4j` (neo4j raises `NotImplementedError`) | `workbench/config.py` |
| `RWB_DOCS` | all discovered | comma-separated document ids to load | `workbench/config.py` |
| `RWB_ANSWER_STYLE` | `brief` | `brief` / `full` | `workbench/config.py` |
| `RWB_MFA` | off (no role required) | `on` = manager + admin; a role list sets roles; `off` clears | `workbench/config.py` |
| `RWB_OTP_DEMO` | off | `1` returns expected TOTP codes in the login challenge (demo only) | `workbench/config.py` |
| `RWB_AIRGAP` | **on** | `off` removes the in-process egress guard (never the monitor) | `workbench/config.py` |
| `RWB_NETMON` | **on** | `off` stops the host network monitor | `workbench/config.py` |
| `RWB_VAULT` | **off** | `on` loads branch indexes from the sealed vault | `workbench/config.py` |
| `RWB_MASTER_KEY_HEX` | unset (key file `kms/master.key` is used) | 64-hex-character vault master key | `workbench/security/vault.py` |
| `RWB_SANDBOX_BACKEND` | `auto` | `auto` / `subprocess` / `docker` | `workbench/config.py` |
| `RWB_OCR_THRESHOLD` | `0.6` | OCR confidence below which lines are flagged | `workbench/config.py` |
| `RWB_KEEP_WARM` | unset | `1` disables idle model unloading | `workbench/services/resources.py` |
| `RWB_WARM_START` | `1` | anything else skips the full warm start of the API orchestrator | `workbench/app/api.py` |
| `RWB_ADMIN_PASSWORD`, `RWB_MANAGER_PASSWORD`, `RWB_USER_PASSWORD` | seed passwords `Admin#2026`, `Manager#2026`, `User#2026` | passwords for the three seeded accounts `admin`, `manager`, `user` | `workbench/security/auth.py` |

Important defaults and invariants:

- authentication is on unless explicitly disabled;
- the backend setting defaults to `auto`, which resolves to `files` whenever knowledge-layer artefacts exist;
- multi-model routing, the air-gap egress guard and the network monitor are **on** by default; the vault is **off** by default; MFA is not required by default;
- the seeded accounts use well-known passwords unless the `RWB_*_PASSWORD` variables or `setup-security --random-passwords` are used; must-change is not enforced;
- CORS allows all origins (`allow_origins=["*"]`), and TLS/mTLS is opt-in (`serve --tls|--mtls`);
- escalation keys are single-use and expire after 30 minutes; undecided requests expire after 24 hours; bearer tokens expire after 8 hours;
- brief answer style is the default;
- medium effort is the default, and it does call the model (composer, unsure classification, follow-up rewrite);
- `low` effort never calls the model;
- the model never performs arithmetic;
- restricted requests never receive bypass instructions;
- all statements require evidence;
- two claims must have matching context keys before conflict comparison;
- missing information is reported, not fabricated;
- idle resources are unloaded according to profile.

## 21. Known Limitations and Current Status

### Implemented and operational

- deterministic parse, normalize, table, profile, glossary, chunk, claim, procedure, and relation layers;
- files backend (selected by the default `auto` setting) and cached in-memory index over six documents;
- seventeen agent keys and fifteen task types with routing;
- plan templates, compound extensions, bounded replanning;
- verification, governance, citations, confidence, HITL, and hash-chained audit;
- local Ollama client with structured calls (JSON-schema constrained except the report summary);
- CLI, REPL, FastAPI, SSE, uploads, reviews, status side channel, and benchmark runner;
- security guard, release gate, password hashing, lockout, tokens, access grants, audit records;
- React/Vite frontend structure and API integration contract.

### Pending or lightly validated

1. **Neo4j workbench backend**: `workbench/services/backends/neo4j_backend.py` is a stub that raises `NotImplementedError`; implement it against the frozen `KnowledgeService` protocol and typed records.
2. **LLM extraction**: the `llm_pass1` / `llm_pass2` passes are wired into phase 8 but have produced no output in any run (every report: LLM ok 0). Richer `FEEDS`, `DISCHARGES_TO`, and other model-derived relations do not exist yet; the graph is deterministic only.
3. **LLM-on quality pass**: all recorded benchmark runs are LLM-free. Benchmark qwen3:4b and larger profiles for composed answers, classifier fallback, explanation summaries, diagnosis structuring, planner refinement, and grounded narrative removal.
4. **Real upload tests**: test a small PDF and image end to end before attempting large manuals.
5. **Vision integration**: images now go through OCR + vision intake into session notes and review drafts; real image blocks / figure crops are still to do.
6. **Frontend integration validation**: run the browser client against the actual server and verify all API/SSE/security flows (including the double `final` event and the missing HITL review screen).
7. **More documents**: six documents are loaded, but the benchmark and demonstrations exercise mainly the CDU manual; multi-document authority ranking exists but needs broader corpus validation.
8. **Scaling**: the in-memory files index is appropriate for tens of documents; Neo4j is the scale path.
9. **Parallel execution**: optional parallel plan execution can be considered for larger GPUs, but sequential execution is the current predictable behavior.
10. **Heuristic instrumentation edges**: instrument/equipment links may be inferred from nearby text and tag-description rules; they must remain marked inferred until stronger relations are available.

### Update, 25 September 2026

Implemented and tested since the note above (operator manual: `docs/manual/index.html`):

- **Multi-model routing** (`workbench/models/`): capability-profile registry (`registry.yaml` + local override), per-call router with hardware fit and swap penalties, hybrid-request decomposition, chained routing log, `models_used` / `routing` on every audit block.
- **Named local tools and agent loop** (`workbench/tools/`): read/write/list files, run_python, spreadsheet read/write, search_documents (guarded), calculate (steps shown), ocr_image, describe_image, make_docx/xlsx/pptx; model-driven loop with deterministic fallback; chained tool-call log.
- **Sandbox** (`workbench/sandbox/`): subprocess backend with Windows job objects (memory, CPU, process count), no sockets, import hook with a banned list, stdlib allow-list plus checksummed vendor manifest, write confinement and disk cap, ephemeral workdir, static analysis + task tests = verified, deterministic seeds, chained run log. Docker backend implemented but untested on this host. **Caveat:** without Docker the subprocess backend's isolation is Python-level only; file reads are not confined and static-analysis failures are advisory, so it is not a security boundary (see Known limitations).
- **Multimodal intake** (`workbench/intake/`): RapidOCR on device with per-line confidence, local vision model with purpose prompts (P&ID, handwriting, gauge, photo), flagged low-confidence lines; image uploads go through it.
- **Deliverables and review** (`workbench/deliverables/`, `workbench/review/`): docx / xlsx (live formulas) / pptx / md with classification, provenance, calculation steps and "pending human sign-off"; drafts with provenance-linked figures, confidence flags, mandatory resolution, sign-off blocked (409) while flags are open; sign-off and rejection need a manager or administrator.
- **Vault** (`workbench/security/vault.py`, `vault_backend.py`): AES-256-GCM envelope encryption per branch, local KMS with versioned role wrapping keys, master key from file or `RWB_MASTER_KEY_HEX`, rotation and revocation without re-encrypting data, session keyrings zeroed on logout, branches decrypted into memory per session under `RWB_VAULT=on`. Off by default; only the index cache is sealed, and the master key file sits on the same disk.
- **Sovereignty** (`workbench/sovereignty/`): hash-chained logs (section 18.1; with retroactive chaining of pre-existing files and cross-process locking), `audit-verify`, in-process egress guard (on by default; it does not cover native code or child processes), host-wide network monitor polling every 2 s with interface up/down detection, signed Ed25519 model packages (verify manager+, import admin), local TLS / mTLS (`serve --tls|--mtls`, opt-in).
- Released answers are persisted (`workbench/services/response_store.py`) so any turn can become a deliverable.

Still pending: the Neo4j workbench backend (below), the Docker sandbox path on a Docker host, confidential computing / TEE, a formal compliance mapping.

### Known limitations (open code issues, 2026-09-26)

These are behaviours of the current code, found in the documentation audit (`docs/DOC_AUDIT.md` §1). None is fixed yet. Several qualify stronger claims in `docs/SECURITY.md`, so read that document together with this list.

1. **Sandbox escape (critical):** without Docker, any signed-in account (including `user`) can call `POST /sandbox/run` and read files on disk such as `data/parsed`, `users.json`, `secret.key` and `kms/master.key`. Reads are unconfined, `io.FileIO` and `_socket` are not blocked, and static-analysis failures are advisory.
2. **Unauthenticated endpoints:** `/runs`, `/runs/{id}`, `/runs/{id}/events`, `/btw`, `/runs/{id}/btw`, `GET /vault`, `GET /sovereignty`, `/sovereignty/connections`, `/sovereignty/egress`.
3. **`GET /audit/{session_id}` returns `[]` for non-admins:** audit files are written under the raw session id, but the check expects the `<username>__` prefix.
4. **SSE sends two `final` events:** the first is not a `FinalResponse`, so a client that closes on the first `final` breaks.
5. **`--clean` / `--rebuild` wipe Neo4j before every PDF,** leaving only the last document in the graph.
6. **CLI sign-in crashes for MFA-required accounts** (`MfaRequired` is not caught).
7. **Username enumeration:** unknown user and wrong password give different messages; a locked account returns 423.
8. **CLI commands without role checks:** `users --add` (anyone can add an admin), `security-log`, `security-check`, `vault rotate`, `vault revoke`; `revoke-key` blocks only `user`, so a guest passes.
9. **Approval queue over-exposure:** `approvals --all` / `GET /approvals?show_all=true` return every request to any signed-in user. The preview gate picks the "hardest" tag by string order and ignores compartment allowlists.
10. **Grant key race:** there is no lock between redeeming and spending a key, and a key is spent only on `answered` / `needs_review`. Also latent: `grant.max_uses` is set after signing, so any `grant_max_uses` other than 1 would break every key.
11. **TOTP brute force:** wrong codes never count toward lockout, and a correct password resets the counter. The TOTP secret is stored in plaintext in `users.json`.
12. **Classification file parse error** re-derives rule tags and drops pins and allowlists, which can widen access.
13. **Weak hardening defaults:** `workbench passwd` accepts 4-character passwords, `chmod(0o600)` does nothing on Windows, and CORS allows all origins.
14. **`python -m knowledge_layer --help`** runs the full pipeline instead of printing help.
15. **`npm run lint` is broken** (eslint is not in `devDependencies`).

## 22. Neo4j Backend Handoff

The pending `Neo4jKnowledgeBackend` must return the same typed records as the files backend.

Required mapping includes:

- `DocumentInfo` from `Document` nodes;
- `EntityRecord` from `Entity` nodes, including train and `SAME_AS` behavior;
- `ClaimRecord` from `HAS_CLAIM` nodes, mapping `uid` to `claim_id`, normalized unit to `unit`, and document revision;
- `ConflictRecord` from `CONFLICTS_WITH` and `CORROBORATES` edges using the same context grouping as `IndexStore.conflicts_for`;
- `RelationRecord` from entity-to-entity relations, with source inferred from raw predicate metadata;
- `ProcedureRecord` and `StepRecord` from procedures, ordered steps, applies-to links, mentions, and described chunks;
- `ChunkRecord` from chunk nodes and section paths;
- `SectionRecord`, glossary, references, standing instructions, and cross references from their graph nodes and edges.

Retrieval requirements:

- use the existing 384-dimensional `bge-small` vector index;
- add full-text indexes for chunks and procedure titles;
- use reciprocal-rank fusion with the files index's ranking behavior;
- preserve chunk-type and chapter filters;
- resolve both scoped and unscoped canonical tags;
- include parent and train-child claims in entity claim retrieval;
- keep the composite backend so session uploads continue to work.

Validation after implementation:

```powershell
$env:RWB_KNOWLEDGE_BACKEND = "neo4j"    # today this fails fast with NotImplementedError
python -m workbench bench
```

Before validating, rebuild the graph document by document or without `--clean` / `--rebuild` across several PDFs, since those flags leave only the last PDF in Neo4j.

Compare task types, entities, blocks, and safety handling with the files-backend benchmark. The same prompts should retain equivalent behavior.

## 23. Demonstration Sequence

Access control is on by default and the CDU manual is `SECRET`, so first run `python -m workbench setup-security` and `python -m workbench login --user admin` (or set `RWB_AUTH=off` for a demo without access control). The planning and "why" demonstrations need the Qwen models pulled. With the model available, timings at the default `medium` effort include a composer call; the tens-of-milliseconds figures apply to `--effort low`.

A concise demonstration should run in this order:

1. inventory: `What are all the equipments in the refinery?`
2. procedure: `What is the recommended way to start the CDU?`
3. limits: `The crude charge pump is operating at 520 m3/h. Is this acceptable?`
4. troubleshooting: `The crude charge pump discharge pressure is dropping. What should I check?`
5. planning with `--effort high`;
6. ambiguous: `How do I start it?`
7. out of scope: `What is the capital of France?`
8. restricted: `Can I bypass the vacuum heater low fuel gas pressure trip temporarily?`
9. `python -m workbench trace --show` to demonstrate the saved audit/thinking trace.

Useful additional examples:

```powershell
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
python -m workbench ask "Trace the crude flow from the crude charge pump to the atmospheric column."
python -m workbench ask "How do I change over from the running crude charge pump to the standby pump?"
python -m workbench ask "Why is the crude heated before entering the atmospheric column?" --effort high
python -m workbench ask "Compare the documented operating conditions for Basrah crude and Bombay High crude."
python -m workbench ask "I found two different normal flow values for the crude charge pump. Which one should I trust?"
python -m workbench ask "Which documents describe the startup procedure for the atmospheric heater?"
python -m workbench ask "Prepare a report on the atmospheric column operating envelope."
```

The most important demonstration point is not that every question receives a long answer. It is that the system chooses among a grounded answer, a clarification, an out-of-scope response, a restricted response, or an access request according to evidence and policy.

## 24. Design Invariants for Future Changes

Do not break these invariants:

1. Every answer statement maps to evidence containing the stated number, tag, or fact.
2. Claims are compared only when context keys match.
3. The LLM never performs arithmetic or silently writes procedure steps.
4. Restricted requests never receive bypass instructions.
5. Access enforcement remains in the knowledge service and release gate, not only in prompts or the UI.
6. Unknown provenance, and ids the classification registry has never seen, fail closed (`SECRET`).
7. Session uploads remain isolated until deliberate promotion.
8. Human-review flags remain visible to the caller and frontend.
9. Progress and audit data remain available for long-running requests.
10. Checkpointed work can resume without repeating successful phases.
11. Deterministic paths remain functional when Ollama or Neo4j is unavailable.
12. The frontend renders the typed response contract rather than reverse-engineering prose.

## 25. One-Paragraph Handoff

This repository implements a local, source-grounded refinery engineering assistant. The knowledge layer parses PDFs with Docling, deterministically reconstructs document structure, procedures, tables, claims, relations, glossary terms, and references, and persists them to JSON artefacts and (optionally) Neo4j; an Ollama extraction pass is wired in but has not yet produced any output, so the knowledge is deterministic only. The workbench sits above a swappable `KnowledgeService`, whose default `auto` setting selects a files backend that builds cached indexes from those artefacts for six documents. It classifies each request, resolves equipment and conversational context, guards all retrieval by role/document policy, routes the question through a task-specific DAG of agents, performs arithmetic and comparison deterministically, verifies every statement against evidence, composes readable prose, applies safety and human-review governance, and returns typed blocks through CLI, FastAPI/SSE, and the React frontend. The main next engineering tasks are closing the open security issues listed under Known limitations (starting with the sandbox escape and the unauthenticated endpoints) and the Neo4j-backed service adapter, followed by making LLM extraction produce output, LLM-on quality validation, real upload/image tests, and frontend integration verification.

## 26. Source Documents Consolidated Here

This context document was assembled from:

- `README.md`;
- `SETUP.md`;
- `walkthrough.md`;
- `docs/PLAN.md`;
- `docs/HOW_IT_WORKS.md`;
- `docs/API.md`;
- `docs/SECURITY.md`;
- `docs/ACCESS_AND_ANSWERS.md`;
- `docs/DEMO.md`;
- `docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md`;
- `workbench/fixtures/README.md`;
- `WebPage/README.md`;
- parser reports under `data/parsed/*/parse_report.md`;
- package metadata in `pyproject.toml`;
- `docs/DOC_AUDIT.md` (2026-09-26), with each corrected claim re-checked against the source code.

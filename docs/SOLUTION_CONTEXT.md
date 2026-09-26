# Refinery Engineering AI Workbench: Complete Solution Context

> A durable technical context document for understanding, operating, demonstrating, extending, and handing off the Refinery project.
>
> This document consolidates the repository documentation, `walkthrough.md`, parser reports, setup notes, API contract, security model, implementation plan, and knowledge-layer handoff. It describes the current solution as documented on 2026-09-22. Where a feature is implemented but still pending validation or integration, that status is stated explicitly.

## 1. Executive Summary

The project is a fully local refinery engineering knowledge and question-answering system. It converts refinery and engineering PDFs into structured, source-grounded knowledge and exposes that knowledge through a local agentic workbench and a browser API.

The system has two layers:

1. **Knowledge layer** (`knowledge_layer/`): parses documents, removes noise, reconstructs structure and tables, creates typed chunks, extracts deterministic engineering claims and relationships, optionally uses Ollama for additional entities and relations, validates everything, and writes a document/domain/evidence graph to Neo4j plus JSON artefacts.
2. **Agentic workbench** (`workbench/`): accepts an engineer's request, classifies the task, resolves equipment and context, enforces access control, retrieves only permitted evidence, builds and executes a bounded plan, verifies the result, composes a readable answer, applies safety and governance rules, and returns typed answer blocks with citations and an audit trail.

The core principle is:

> The LLM is not the system's memory and is not trusted to invent engineering truth. Structured external knowledge, deterministic rules, evidence, validation, and governance control what can be answered.

The workbench currently uses the **files backend** as its production path. It reads the knowledge layer's on-disk artefacts, rebuilds deterministic indexes, and serves answers without requiring Neo4j. A Neo4j-backed `KnowledgeService` adapter is implemented only as a stub and is the main pending integration task.

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
Docling parser + checkpointing
  |
  v
Parsed document and tables
  |
  v
Deterministic normalizer
  - remove page furniture
  - recover chapters and sections
  - identify procedures
  |
  v
Table classification and reconstruction
  |
  v
Document profile + glossary
  |
  v
Structure-aware typed chunks
  |
  +------------------------------+
  |                              |
  v                              v
Deterministic claims/relations   Embeddings and retrieval index
  |                              |
  +---------------+--------------+
                  |
                  v
      Optional Ollama extraction
      entities + relationships + summaries
                  |
                  v
        Validation and entity resolution
                  |
                  v
   Neo4j graph + JSON knowledge artefacts
                  |
                  v
       Workbench KnowledgeService
                  |
  +---------------+----------------+
  |                                |
  v                                v
Files backend                    Neo4j backend (pending)
  |
  v
Phase 0/1: classify, resolve, safety gate
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

The deterministic document graph and deterministic claims are written independently of the LLM. If Ollama is unavailable, the document can still produce structure, procedures, claims, references, and rule-based relations.

## 4. Repository Map

```text
Refinery/
├── knowledge_layer/          PDF-to-knowledge extraction pipeline
│   ├── parser.py             Docling parsing and checkpoints
│   ├── normalizer.py         deterministic cleanup and structure
│   ├── table_classifier.py   table type classification
│   ├── table_reconstructor.py merged-cell propagation and deduplication
│   ├── document_profile.py   document orientation and metadata
│   ├── glossary_builder.py   abbreviations and terminology
│   ├── chunker.py            typed, structure-aware chunks
│   ├── spec_claims.py        deterministic specification/prose claims
│   ├── table_claims.py       table cells to claims
│   ├── rule_relations.py     deterministic domain relations
│   ├── entity_identity.py    tag normalization and stable identity
│   ├── entity_resolver.py    cross-document identity handling
│   ├── extractor.py          constrained Ollama extraction
│   ├── validator.py          evidence, units, domain, contradiction checks
│   ├── graph.py              idempotent Neo4j insertion
│   ├── memory.py             Neo4j operations and vector search
│   ├── embedder.py           chunk/query embeddings
│   ├── reporter.py           Markdown and JSON reports
│   ├── checkpoint.py         phase/chunk resume state
│   ├── pipeline.py           main pipeline orchestrator
│   ├── schemas/              Pydantic schemas
│   ├── prompts/              extraction prompts
│   └── scripts/              parsing, auditing, graph, and Neo4j utilities
├── workbench/                local agentic question-answering layer
│   ├── core/                 request, knowledge, plans, blocks, results, events
│   ├── agents/               classifiers, retrieval, procedure, safety, etc.
│   ├── services/             backends, indexes, retrieval, audit, ingest, resources
│   ├── orchestration/        routing, DAG executor, replans, runs, HITL, status
│   ├── llm/                  Ollama client, prompts, fake/null clients
│   ├── memory/               session and follow-up context
│   ├── app/                  CLI and FastAPI API
│   ├── prompts/              agent prompts
│   ├── benchmarks/           canonical prompts and runner
│   ├── fixtures/             small mock knowledge artefacts
│   └── security/             authentication, guarding, release gate, grants
├── data/
│   ├── raw/                  input PDFs
│   ├── parsed/               parser output and parse reports
│   ├── normalized/           cleaned document structures
│   ├── knowledge/            profiles, glossaries, chunks, embeddings
│   ├── checkpoints/          resumable state
│   ├── reports/              knowledge-layer reports and graph dumps
│   └── workbench/            indexes, sessions, traces, audits, uploads, reports
├── WebPage/                  React/Vite frontend
├── tests/                    knowledge-layer and workbench tests
├── docs/                     project documentation and schemas
├── neo4j-data/               local Neo4j Community installation, gitignored
├── pyproject.toml            Python package and dependency definition
├── SETUP.md                  setup and runtime prerequisites
├── walkthrough.md            original implementation walkthrough
└── README.md                 project overview and commands
```

The knowledge layer was moved from the older `src/` and `schemas/` layout into `knowledge_layer/`; imports were rewritten without changing the intended logic. The workbench depends on the knowledge layer through the `KnowledgeService` protocol instead of importing extraction internals directly.

## 5. Knowledge Layer: Document-to-Knowledge Pipeline

### 5.1 Pipeline phases

The original walkthrough describes the document pipeline as nine operational phases. The exact implementation is resumable and phases can be skipped when checkpoints exist:

1. **Parse with Docling**
   - Scans `data/raw/` for PDFs.
   - Uses layout and table models, OCR, page windows, and provenance metadata.
   - Writes parsed elements, pages, tables, figures, metadata, and a parse report under `data/parsed/<document>/`.
   - Checkpoints page windows so an interrupted parse can resume.

2. **Normalize**
   - Removes page furniture such as repeated headers, footers, running titles, approval blocks, table-of-contents noise, and figure placeholders.
   - Strips articles in headings where appropriate, for example `The Vacuum Distillation Unit` to `Vacuum Distillation Unit`.
   - Builds chapters from table-of-contents information and page headers.
   - Builds section hierarchy from heading numbers and chapter context.
   - Retains provenance and section paths for kept elements.
   - Detects ordered procedure blocks.

3. **Classify tables**
   - Uses deterministic rules and regular expressions, not an LLM.
   - Recognizes table families such as abbreviations, equipment, operating limits, safety, procedures, material balance, and comparison tables.
   - Propagates merged-cell headers and removes duplicate/repeated content.

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

7. **Create deterministic knowledge**
   - Extracts specification blocks, prose values, and table-cell claims.
   - Creates deterministic domain relations such as routing, composition, suction/discharge, and procedure ordering.
   - Builds procedures with ordered `ProcedureStep` records and `NEXT` links.
   - Creates document graph structure: chapters, sections, chunks, procedures, standing instructions, references, and cross references.

8. **Optional LLM extraction and validation**
   - Ollama adds typed entities, relationships, and claims from engineering chunks.
   - Prompts are constrained by chunk type and schema.
   - Every model-produced item must include evidence.
   - Validator checks schema, evidence grounding, units, domain compatibility, and contradictions before insertion.
   - Malformed or unsupported results are rejected, not repaired into facts.

9. **Graph insertion and reporting**
   - Neo4j schema is initialized when available.
   - Documents, terms, chunks, entities, claims, relations, procedures, references, and evidence links are inserted idempotently.
   - A Markdown report, source breakdown, graph dump, and checkpoint state are generated.

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
- source type, for example `rule`, `table`, `llm_pass1`, or `llm_pass2`;
- grounding and confidence.

The system does not treat co-occurrence as a process relation. For example, two items appearing in the same paragraph do not automatically create a `FEEDS` edge. Similarly, a motor does not receive a `SUCTION_FROM` relation merely because it is near a pump.

### 5.4 Claim context keys

A claim is not just `subject / predicate / value`. Its context determines whether it can be compared to another claim.

The context key includes:

- parameter role: `design`, `rated`, `normal`, `operating`, `minimum`, `maximum`, `mechanical_design`, `test`, `relief`, `alarm`, `trip`;
- location: `suction`, `discharge`, `inlet`, `outlet`, `top`, `bottom`, `flash_zone`, `shell`, `tube`;
- operating mode: `normal_operation`, `startup`, `shutdown`, `emergency`, `temporary`, `upset`;
- scenario: Basrah, Bombay High, BH mode, PG mode, SKO operation, Kuwait, Kirkuk, and similar cases;
- pressure basis: absolute versus gauge;
- temporal status: current, historical, design, defunct;
- qualifier: table label, case, reference condition, distillation point, or similar qualifier.

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

The repository contains parsed artefacts for refinery, vendor, standards, and engineering documents. The CDU operating manual is the primary end-to-end corpus used by the workbench.

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
- no parser errors or warnings;
- consistent provenance and bounding boxes;
- CUDA Docling processing on an NVIDIA GTX 1650 4 GB;
- approximately 43 minutes 55 seconds for parsing.

The deterministic normalization/chunking results documented for the manual are:

- 36 chapters;
- 1,119 sections;
- 182 procedures;
- 1,503 ordered procedure steps;
- 467 typed chunks;
- 1,528 table/specification/prose claims;
- 531 entities and 169 typed relations in the deterministic graph;
- 1,503 procedure steps with `NEXT` order;
- 649 chunk-to-section links;
- 64 referenced documents;
- 38 in-document cross references;
- 19 standing instructions;
- 96 abbreviations in the document profile and 172 glossary terms in the workbench-oriented index.

The reports use slightly different aggregate counts at different stages because the knowledge-layer graph, the files index, and the workbench index apply different filters and aggregation rules. Those figures should be interpreted as stage-specific metrics rather than expected equality.

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

Every relation retains evidence, page, document, chunk, source, and confidence properties where applicable. Rule-derived relations are distinguishable from LLM-derived relations.

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

The files backend loads the knowledge artefacts and builds an in-memory `IndexStore`. Documented figures from the current documentation include approximately 1,052 indexed entities, 1,531 claims, around 270 relations, 182 procedures, and 467 chunks. The cache lives at `data/workbench/cache/<document>/index.json`.

A cold index build is about 15 seconds and a cached load is about 3 seconds in the documented GTX 1650 environment.

## 9. The Agent System

Sixteen registered agent keys are described: twelve primary named agents plus four retrieval specialists. Agents return `AgentResult` objects containing render blocks, evidence, checkable statements, confidence, safety flags, and missing requirements.

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

### Phase 0/1: Understanding

1. Receive text, session id, attachments, options, and authenticated principal.
2. Verify access before reading knowledge records.
3. Classify the task with weighted deterministic rules.
4. Use the LLM only when classification confidence is insufficient and the profile permits it.
5. Resolve equipment aliases, tags, numbers, units, scenario, operating mode, and follow-up references.
6. Apply tag correction only when confidence and margin justify adoption; otherwise ask a clarification question.
7. Detect `clear`, `sensitive`, or `restricted` safety status.
8. Produce `StructuredRequest`.

### Phase 2: Specialist retrieval

The `ContextBuilder` chooses a targeted route:

- `claims` for lookup, limits, comparisons, conflicts, and provenance;
- `graph` for multi-hop and flow questions;
- `proc` for procedures;
- `inventory` for surveys and corpus counts;
- `hybrid` for troubleshooting, explanation, safety, planning, reports, and cross-document questions;
- `none` for clarification or out-of-scope requests.

Hybrid search may combine BM25, embeddings, reciprocal-rank fusion, and a CPU reranker, subject to effort and hardware settings. Retrieval is filtered by allowed documents, chunk types, chapters, and query context.

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
5. Governance applies safety, human-review, confidence, citation, and release rules.
6. The audit trail and thinking trace are saved.
7. `FinalResponse` is returned.

## 11. Supported Request Types and Routing

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
4. Ask Ollama for structured `{answer, assumptions, not_documented}` when enabled.
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
- classification parse failures fail closed as `SECRET`.

Prompt injection cannot reveal a document that was never placed in the agent's accessible knowledge service.

### 13.3 Access escalation

When a question appears to require a higher classification:

1. the requester receives an answer from accessible material, plus a notice that restricted material bears on the question;
2. the system determines the exact opaque record ids needed;
3. the request is routed to the lowest role that can approve the records;
4. the approver sees the actual passages and decides;
5. approval creates a signed, one-time, short-lived key for one person, one question, and one record list;
6. the requester resubmits the exact question with the key;
7. the key is spent and cannot be replayed.

The key is not a role promotion and does not grant document-wide access.

### 13.4 Grant validation

The key verification checks signature, secret digest, revocation, use count, expiry, owner, question match, and record-list scope hash. HMAC prevents editing grant fields or fabricating a valid signature.

### 13.5 Passwords, tokens, and lockout

- passwords use per-account salts and PBKDF2-HMAC-SHA256 with 240,000 iterations;
- unknown username and wrong-password responses are intentionally similar;
- five failed attempts within the lockout policy cause a fifteen-minute lockout;
- bearer tokens are stored as SHA-256 digests;
- tokens expire after eight hours;
- role changes invalidate old tokens;
- security events are appended to `data/workbench/security/security.jsonl`.

### 13.6 MFA

TOTP is optional and off by default. It can be enabled with `RWB_MFA=on`, `RWB_MFA=admin`, or `RWB_MFA=off`. Demo-only display of expected codes uses `RWB_OTP_DEMO=1` and must not be used in deployment.

### 13.7 Release gate

Before release, the gate checks:

1. all citations belong to readable documents;
2. all equipment tags resolve in the reader's view;
3. all figures occur in attached evidence.

A failed release is withheld rather than redacted, logged as a security event, and flagged for review.

### 13.8 Human-in-the-loop rules

Review is required for:

- restricted requests;
- values outside design envelope;
- emergency, isolation, changeover, or shutdown procedures;
- danger-level precautions;
- all planning answers;
- low-confidence responses below the configured threshold.

The answer can still be delivered with a review flag because the workbench advises and never actuates plant equipment.

## 14. Sessions, Follow-ups, and Tag Correction

### 14.1 Sessions

A client chooses `session_id`. Session state is stored under `data/workbench/sessions/<id>.json` and retains recent turns, structured interpretations, entities, parameters, status, corrections, and composed answers.

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

| Profile | Text model | Vision model | Context | Reranker |
|---|---|---|---:|---|
| `cpu` | qwen3.5:2b | qwen3.5:2b | 4k | off |
| `gpu_4gb` | qwen3:4b | qwen3.5:2b on demand | 4k | bge-reranker-base on CPU |
| `gpu_8gb` | qwen3.5:4b | qwen3.5:4b | 8k | bge-reranker-base on CPU |
| `gpu_12gb` | qwen3.5:9b | qwen3.5:9b | 16k | bge-reranker-v2-m3 on GPU |
| `gpu_16gb` | qwen3.5:9b or gpt-oss:20b text-only | qwen3.5:9b | 32k | bge-reranker-v2-m3 on GPU |
| `gpu_24gb` | qwen3.6:27b | qwen3.6:27b | 32k | bge-reranker-v2-m3 on GPU |

Resource rules:

- one model is resident at a time;
- the vision model is loaded only for image description and released immediately;
- idle unload is approximately 45 seconds on the 4 GB/CPU profiles and 180 seconds on larger profiles;
- `RWB_KEEP_WARM=1` disables idle unloading;
- the bge-small embedder and reranker are lazy CPU models;
- embeddings use `BAAI/bge-small-en-v1.5` with 384 dimensions;
- lookup, limits, comparison, and conflict requests use claim indexes without text retrieval;
- hybrid retrieval is reserved for tasks that need passages;
- the reranker runs once per request;
- structured LLM calls use JSON schema output and `think=false` by default.

On the documented GTX 1650:

- a short structured model call takes about 5–6 seconds after model load;
- the first call pays model-load cost;
- the embedder takes approximately 6 seconds to load;
- the CPU reranker takes approximately 29 seconds to load;
- lookup and limit tasks can complete in tens of milliseconds without the LLM;
- diagnosis structuring is disabled by default on `gpu_4gb` because it took 90–120 seconds per call and deterministic extraction is preferred.

## 16. CLI, API, and Frontend

### 16.1 Installation

Requirements:

- Python 3.10 or newer; development was performed on Python 3.13;
- Neo4j 5.11+ or the documented Neo4j Community server;
- Ollama and an appropriate model if model-assisted extraction/answers are desired;
- CUDA-capable GPU is recommended for Docling parsing but CPU fallback exists;
- `BAAI/bge-small-en-v1.5` is downloaded on first use unless already cached.

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
ollama pull deepseek-r1:7b
```

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

`--no-llm` builds deterministic structure, procedures, claims, and relations. `--rebuild` reuses the parse but reruns downstream phases after normalizer/chunker changes. `--clean` focuses on extraction and clears the graph according to the pipeline's clean behavior.

### 16.3 Workbench commands

```powershell
python -m workbench setup-security
python -m workbench login
python -m workbench logout
python -m workbench security
python -m workbench security-check --passwords
python -m workbench status
python -m workbench whoami
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
python -m workbench ask "..." --effort low
python -m workbench ask "..." --no-thinking
python -m workbench ask "..." --json
python -m workbench ask "..." --detail
python -m workbench trace
python -m workbench trace --show
python -m workbench repl
python -m workbench serve --port 8000
python -m workbench bench
python -m workbench schema
```

`--effort low` is the recommended mode when the GPU is busy. It uses deterministic indexes, avoids the model, and still exercises the task pipeline.

### 16.4 FastAPI endpoints

Start the server:

```powershell
python -m workbench serve --host 127.0.0.1 --port 8000
```

OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

Important endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | backend, model, profile, effort, documents, resource state |
| `GET /agents` | agent registry |
| `GET /schema` | live JSON schemas |
| `POST /auth/login` | login and readable tags/documents |
| `POST /auth/logout` | revoke token |
| `GET /auth/whoami` | current identity and clearance |
| `GET /stats` | permitted workspace counts |
| `GET /knowledge/tree` | readable/locked knowledge branches |
| `POST /ask` | synchronous `FinalResponse` |
| `POST /runs` | asynchronous run start |
| `GET /runs/{id}` | run state and final response |
| `GET /runs/{id}/events` | SSE progress and final event |
| `POST /runs/{id}/btw` | status-agent question |
| `GET /sessions` | caller's conversations |
| `GET /sessions/{id}` | conversation and attachments |
| `POST /upload` | session-only PDF/image attachment |
| `POST /knowledge/documents/{id}/promote` | manager+ promotion into shared corpus |
| `GET /reviews` | pending HITL items |
| `POST /reviews/{response_id}` | approve/reject a flagged answer |
| `GET /audit/{session_id}` | audit records |

**Added 25 September 2026** (`workbench/app/api_ext.py`, full table in `docs/API.md` §9):
`/models` (registry, routing log, `route`, `register`, signed packages), `/sovereignty` (egress guard,
network monitor, connection log, chain verification), `/vault` (seal, rotate, revoke, TLS state),
`/tools` and `/sandbox` (named tools, agent loop, workspace, chained call/run logs, manifest),
`/intake` (OCR + vision), `/deliverables` and `/drafts` (Word/PowerPoint/Excel exports under
mandatory sign-off).

For long-running requests, the frontend should call `POST /runs`, subscribe to `GET /runs/{run_id}/events`, render live events, and close the stream after the `final` event.

### 16.5 SSE events

Events include:

- `phase_started`;
- `phase_finished`;
- `plan_created`;
- `agent_started`;
- `agent_finished`;
- `llm_call`;
- `replan`;
- `warning`;
- `error`;
- `final`.

Each event carries JSON progress data, phase, agent, step, message, timestamp, and optional thinking/model/decision fields. The frontend should account for replayed events and avoid rendering replayed trace entries twice.

### 16.6 React/Vite frontend

`WebPage/` is the browser client. It talks only to the FastAPI service. Its documented routes are:

- `/`: sign-in;
- `/chat`: question, thinking, blocks, `btw`, upload, effort, access keys;
- `/overview`: workspace and active/waiting work;
- `/documents`: document classifications and readers;
- `/knowledge`: knowledge tree and reach;
- `/access`: access requests and approval queue;
- `/logs`: lower-ranked users' conversations for managers/admins;
- `/security`: running security model;
- `/account`: clearance and MFA.

Run it with:

```powershell
cd WebPage
npm install
npm run dev
npm run typecheck
npm run build
```

The frontend keeps the token in `sessionStorage`, not `localStorage` or cookies. Conversations are stored server-side. The UI uses structured security envelopes rather than scraping answer text. Markdown is rendered by a constrained React renderer that never interprets arbitrary HTML.

## 17. Attachments and Images

PDF attachments are session-scoped:

- saved below `data/workbench/uploads/<session_id>/`;
- parsed through the same knowledge-layer pipeline;
- indexed only for the conversation that uploaded them;
- cached by content hash under `data/workbench/uploads/_cache/`;
- not written into the shared `data/knowledge` corpus;
- not classified until deliberately promoted;
- readable by the uploader in that conversation only;
- removed when forgotten or after restart according to the documented session behavior.

The UI must not allow a question against a PDF until its indexing run reaches `final_status: indexed`; otherwise the request may answer from the rest of the corpus and appear confidently wrong.

Images are described synchronously by the profile's vision model and stored as session notes. They are not yet used as full knowledge records, and figure/image blocks are reserved for future work.

Promotion is a deliberate manager-or-above operation that re-parses the document into the shared knowledge layer and assigns `CONFIDENTIAL` unless another tag is explicitly provided.

## 18. Thinking Traces, Auditing, and Observability

The CLI and SSE stream expose structured reasoning progress without exposing unsupported private model narrative as fact.

- `orchestration/narration.py` turns structured outcomes into concise reasoning lines;
- `app/thinking_display.py` renders phase/agent/decision/timing in the terminal;
- `services/thinking_store.py` stores JSON traces under `data/workbench/thinking/`;
- audit JSONL records are stored under `data/workbench/audit/`;
- security events are stored under `data/workbench/security/security.jsonl`;
- Markdown reports are stored under `data/workbench/reports/`.

A trace contains phase and agent timings, LLM calls and purposes, the plan DAG, replans, warnings, confidence basis, governance verdict, answer Markdown, and raw progress events.

The `btw` status agent reads run state only. It reports current phase, active agent, completed steps, timing, safety flags, model/resource state, and partial summaries without using the LLM or interrupting the request.

## 19. Testing and Benchmarks

The project includes knowledge-layer and workbench tests. Documentation reports include:

- 49 passing tests in the original walkthrough snapshot;
- later workbench documentation describing 289 workbench tests plus 132 knowledge-layer tests;
- a security suite covering credentials, lockout, tokens, classification, guarding, release gate, HTTP behavior, and deliberate guard failure;
- composer tests for grounded prose, unsupported figures/tags, preambles, repetition, and brief/full modes;
- follow-up tests for substitution, elaboration, continuation, and new turns;
- tag matcher tests for parsing, scoring, suggestion, adoption, and ambiguity.

Run:

```powershell
python -m pytest tests -q
python -m pytest tests/workbench -q
python -m workbench security-check --passwords
python -m workbench bench
python -m workbench bench --llm
python -m workbench bench --category troubleshooting limits --verbose
```

The documented deterministic benchmark used 62 prompts over 14 categories and achieved:

- task accuracy: 1.00;
- agent coverage: 1.00;
- entity resolution: 1.00;
- block coverage: 1.00;
- safety handling: 1.00;
- answered runs: 1.00;
- mean confidence: approximately 0.60;
- mean runtime approximately 2.4 seconds in the reported run.

The security red-team command exercises each role against ordinary questions, higher-tier questions, prompt injections, enumeration attempts, canary values, citation shape, and release behavior.

## 20. Configuration Reference

Common environment variables:

```text
RKL_NEO4J_URI
RKL_NEO4J_USER
RKL_NEO4J_PASSWORD
RKL_OLLAMA_MODEL
RWB_AUTH=on|off
RWB_PROFILE=cpu|gpu_4gb|gpu_8gb|gpu_12gb|gpu_16gb|gpu_24gb
RWB_LLM=on|off
RWB_LLM_MODEL
RWB_OLLAMA_URL
RWB_LLM_ANSWER=on|off
RWB_LLM_EXTRACTION=on|off
RWB_RERANKER=on|off
RWB_KEEP_WARM=1
RWB_WARM_START=1
RWB_KNOWLEDGE_BACKEND=files|mock|neo4j
RWB_ANSWER_STYLE=brief|full
RWB_EFFORT=low|medium|high|ultra
RWB_MFA=off|on|admin
RWB_OTP_DEMO=1
RWB_ADMIN_PASSWORD
RWB_MANAGER_PASSWORD
RWB_USER_PASSWORD
```

Important defaults and invariants:

- authentication is on unless explicitly disabled;
- files backend is the current default;
- brief answer style is the default;
- medium effort is the default;
- `low` effort avoids the model;
- the model never performs arithmetic;
- restricted requests never receive bypass instructions;
- all statements require evidence;
- two claims must have matching context keys before conflict comparison;
- missing information is reported, not fabricated;
- idle resources are unloaded according to profile.

## 21. Known Limitations and Current Status

### Implemented and operational

- deterministic parse, normalize, table, profile, glossary, chunk, claim, procedure, and relation layers;
- files backend and cached in-memory index;
- sixteen agent keys and task routing;
- plan templates, compound extensions, bounded replanning;
- verification, governance, citations, confidence, HITL, and audit;
- local Ollama client with schema-constrained calls;
- CLI, REPL, FastAPI, SSE, uploads, reviews, status side channel, and benchmark runner;
- security guard, release gate, password hashing, lockout, tokens, access grants, audit records;
- React/Vite frontend structure and API integration contract.

### Pending or lightly validated

1. **Neo4j workbench backend**: implement `workbench/services/backends/neo4j_backend.py` against the frozen `KnowledgeService` protocol and typed records.
2. **LLM-extracted graph density**: wait for or complete the LLM passes that add richer `FEEDS`, `DISCHARGES_TO`, and other relations.
3. **LLM-on quality pass**: benchmark qwen3:4b and larger profiles for classifier fallback, explanation summaries, diagnosis structuring, planner refinement, and grounded narrative removal.
4. **Real upload tests**: test a small PDF and image end to end before attempting large manuals.
5. **Vision integration**: use image notes in context resolution and add real image blocks/figure crops.
6. **Frontend integration validation**: run the browser client against the actual server and verify all API/SSE/security flows.
7. **More documents**: the workbench is primarily exercised against the CDU manual; multi-document authority ranking exists but needs broader corpus validation.
8. **Scaling**: the in-memory files index is appropriate for tens of documents; Neo4j is the scale path.
9. **Parallel execution**: optional parallel plan execution can be considered for larger GPUs, but sequential execution is the current predictable behavior.
10. **Heuristic instrumentation edges**: instrument/equipment links may be inferred from nearby text and tag-description rules; they must remain marked inferred until stronger relations are available.

### Update, 25 September 2026

Implemented and tested since the note above (operator manual: `docs/manual/index.html`):

- **Multi-model routing** (`workbench/models/`): capability-profile registry (`registry.yaml` + local override), per-call router with hardware fit and swap penalties, hybrid-request decomposition, chained routing log, `models_used` / `routing` on every audit block.
- **Named local tools and agent loop** (`workbench/tools/`): read/write/list files, run_python, spreadsheet read/write, search_documents (guarded), calculate (steps shown), ocr_image, describe_image, make_docx/xlsx/pptx; model-driven loop with deterministic fallback; chained tool-call log.
- **Sandbox** (`workbench/sandbox/`): subprocess backend with Windows job objects (memory, CPU, process count), no sockets, import hook with a banned list, stdlib allow-list plus checksummed vendor manifest, write confinement and disk cap, ephemeral workdir, static analysis + task tests = verified, deterministic seeds, chained run log. Docker backend implemented but untested on this host.
- **Multimodal intake** (`workbench/intake/`): RapidOCR on device with per-line confidence, local vision model with purpose prompts (P&ID, handwriting, gauge, photo), flagged low-confidence lines; image uploads go through it.
- **Deliverables and review** (`workbench/deliverables/`, `workbench/review/`): docx / xlsx (live formulas) / pptx / md with classification, provenance, calculation steps and "pending human sign-off"; drafts with provenance-linked figures, confidence flags, mandatory resolution, sign-off blocked (409) while flags are open.
- **Vault** (`workbench/security/vault.py`, `vault_backend.py`): AES-256-GCM envelope encryption per branch, local KMS with versioned role wrapping keys, master key from file or `RWB_MASTER_KEY_HEX`, rotation and revocation without re-encrypting data, session keyrings zeroed on logout, branches decrypted into memory per session under `RWB_VAULT=on`.
- **Sovereignty** (`workbench/sovereignty/`): hash-chained logs everywhere (with retroactive chaining of pre-existing files and cross-process locking), in-process egress guard, host-wide network monitor with interface up/down detection, signed Ed25519 model packages, local TLS / mTLS (`serve --tls|--mtls`).
- Released answers are persisted (`workbench/services/response_store.py`) so any turn can become a deliverable.

Still pending: the Neo4j workbench backend (below), the Docker sandbox path on a Docker host, confidential computing / TEE, a formal compliance mapping.

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
$env:RWB_KNOWLEDGE_BACKEND = "neo4j"
python -m workbench bench
```

Compare task types, entities, blocks, and safety handling with the files-backend benchmark. The same prompts should retain equivalent behavior.

## 23. Demonstration Sequence

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
6. Unknown or unclassified provenance fails closed.
7. Session uploads remain isolated until deliberate promotion.
8. Human-review flags remain visible to the caller and frontend.
9. Progress and audit data remain available for long-running requests.
10. Checkpointed work can resume without repeating successful phases.
11. Deterministic paths remain functional when Ollama or Neo4j is unavailable.
12. The frontend renders the typed response contract rather than reverse-engineering prose.

## 25. One-Paragraph Handoff

This repository implements a local, source-grounded refinery engineering assistant. The knowledge layer parses PDFs with Docling, deterministically reconstructs document structure, procedures, tables, claims, relations, glossary terms, and references, then optionally adds validated Ollama extraction and Neo4j persistence. The workbench sits above a swappable `KnowledgeService`, currently using a files backend to build cached indexes from those artefacts. It classifies each request, resolves equipment and conversational context, guards all retrieval by role/document policy, routes the question through a task-specific DAG of agents, performs arithmetic and comparison deterministically, verifies every statement against evidence, composes readable prose, applies safety and human-review governance, and returns typed blocks through CLI, FastAPI/SSE, and the React frontend. The main next engineering task is the Neo4j-backed service adapter, followed by LLM-on quality validation, real upload/image tests, and frontend integration verification.

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
- package metadata in `pyproject.toml`.

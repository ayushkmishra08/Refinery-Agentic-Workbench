# How the Refinery Engineering AI Workbench works

*A plain-language walk-through of the whole system, written for a presentation. File paths point at the code so
every statement can be checked. Revised 2026-09-26 against the code; where the code has a known limitation it is
stated next to the mechanism.*

---

## 1. What it is

The workbench is a fully local assistant for refinery process engineers. It reads engineering documents, turns
them into structured knowledge, and answers engineering questions with the exact page and sentence that supports
each statement. Nothing is meant to leave the machine: the language models run in Ollama, the embeddings and
reranker run on the CPU (on the GPU for the 12 GB+ profiles), and the knowledge lives in JSON files on disk. When
the documents do not contain an answer, the workbench says so ("not documented") instead of guessing.

Six documents are loaded today (every directory under `data/knowledge/` that has both `chunks.json` and a
`data/normalized/<doc>_normalized.json`, excluding `smoke_test` and `test`; `WorkbenchConfig.discovered_documents`
in `workbench/config.py`). Index sizes are the workbench's own build statistics
(`data/workbench/cache/<doc>/index.json`); tags come from `data/workbench/security/classifications.json`:

| document id | tag | entities | claims | relations | procedures | chunks | sections |
|---|---|---|---|---|---|---|---|
| CDU operating manual (562 pages, 814 tables) | SECRET | 1,056 (703 tagged) | 1,531 | 276 | 182 | 467 | 1,119 |
| Crude desalter | CONFIDENTIAL | 45 | 58 | 6 | 3 | 58 | 71 |
| API610 pump operation manual | INTERNAL | 8 | 0 | 0 | 28 | 72 | 77 |
| API560-Comparison-All-in-One-05.12.17-Rev+C | INTERNAL | 57 | 50 | 3 | 3 | 124 | 165 |
| Bulletin-5EH-Steam-Jet-Ejectors | INTERNAL | 6 | 0 | 1 | 0 | 42 | 57 |
| ESBWR | INTERNAL | 387 | 128 | 203 | 8 | 405 | 739 |

The CDU-II operating manual is the reference document for everything below; the other five were added later and
are much thinner in structured facts (two of them yield no claims at all). Most examples in this document are
CDU questions.

It handles fifteen kinds of request (`workbench/core/request.py`, `TaskType`):

| task type | example |
|---|---|
| `lookup` | What is the normal flow rate of the crude charge pump? |
| `multi_hop` | Trace the crude flow from the crude charge pump to the atmospheric column. |
| `procedure` | How do I change over from the running crude charge pump to the standby pump? |
| `troubleshooting` | The crude charge pump discharge pressure is dropping. What should I check? |
| `limits` | The crude charge pump is operating at 520 m3/h. Is this acceptable? |
| `explanation` | Why is the crude heated before entering the atmospheric column? |
| `safety` | What isolation requirements apply before maintenance on this pump? |
| `comparison` | Compare the documented operating conditions for Basrah crude and Bombay High crude. |
| `conflict` | I found two different normal flow values for the crude charge pump. Which one should I trust? |
| `provenance` | Show me all documented values for the pump discharge pressure and their sources. |
| `planning` | Prepare an engineering investigation plan for repeated trips of the vacuum heater. |
| `report` | Prepare a report on the atmospheric column operating envelope. |
| `cross_document` | Which documents describe the startup procedure for the atmospheric heater? |
| `inventory` | What are all the equipments in the refinery? / What does this manual cover? |
| `ambiguous` | Can I run this at 500? (→ the workbench asks which equipment, which parameter, which unit) |

Three things sit around those fifteen and are described in `docs/ACCESS_AND_ANSWERS.md`:

- **who is asking.** Every document carries a tag (`INTERNAL` < `CONFIDENTIAL` < `SECRET`) and every role a level
  (`guest` 0, `user` 1, `manager` 2, `admin` 3; `workbench/security/roles.py`). The CDU manual is `SECRET`, so only
  an administrator reads it; a `user` reads the four `INTERNAL` documents. The workbench asks who you are before it
  takes a question at all. The gate is at the knowledge service (`GuardedKnowledgeService`), not at the UI or the
  prompt, so no agent and no wording reaches around it. A question above your level can be escalated for a signed,
  single-use key over named records. See `docs/SECURITY.md` (and §8.1 below for the path through a run).
- **what comes back.** The retrieved claims, edges, steps and passages are context; an Answer Composer writes the
  reply from them and checks it against them. The typed blocks are still on the response for a frontend.
- **what came before.** "What if we use 11-E-01 instead?" is rewritten into a standalone comparison against the
  previous turn's subject, and a tag the documents do not contain ("12-3-01") is matched to the ones they do before
  the question is refused (§4.4).

---

## 2. Two layers

### 2.1 The knowledge layer (`knowledge_layer/`)

The extraction pipeline, run with `python -m knowledge_layer` (it processes every PDF in `data/raw/`;
`knowledge_layer/pipeline.py`, `process_document`). The phases, with the labels the pipeline prints:

1. **Phase 1 — Parse** (Docling) → `data/parsed/<doc>/parsed_document.json` and `tables.json` (814 tables in the
   CDU manual), then **parse validation** → `data/parsed/<doc>/parse_report.md`. A failed validation stops the
   document here (§2.2).
2. **Phase 2 — Table classification and reconstruction** (§2.2). Not checkpointed; it re-runs every time.
3. **Phase 3 — Normalize** → `data/normalized/<doc>_normalized.json`: page furniture removed, chapter structure from
   the table of contents, procedures detected. For the CDU manual: 36 chapters, 1,119 sections, 182 procedures with
   1,503 ordered steps, and every kept element with its section path.
4. **Phase 4 — Document profile** → `data/knowledge/<doc>/document_profile.json` (cross references, referenced
   documents, standing instructions, abbreviations; CDU: 20 cross references, 19 standing instructions,
   96 abbreviations).
5. **Phase 5 — Glossary** → `data/knowledge/<doc>/glossary.json` (CDU: 172 terms).
6. **Phase 6 — Chunk** → `data/knowledge/<doc>/chunks.json`: typed chunks (procedure, table, safety, upset,
   control, equipment, narrative, document_control) with canonical text (CDU: 467).
7. **Phase 7 — Neo4j schema, Document node and glossary Terms.**
8. **Phase 7a — Document graph** in Neo4j: chapters, sections, procedures, steps, references (§2.2).
9. **Phase 7b — Embeddings** → `data/knowledge/<doc>/chunk_embeddings.json` (`BAAI/bge-small-en-v1.5`,
   384 dimensions, normalized, one vector per chunk, computed over the chunk's retrieval text), also stored on the
   Neo4j `Chunk` nodes with a cosine vector index.
10. **Phase 8 — Per-chunk extraction.** For each chunk: graph context from `MemoryRetriever`; LLM pass 1 (entities)
    and pass 2 (relationships and claims) *if* an Ollama model is available; then the deterministic passes
    (rule relations, specification claims, prose claims, table claims); the `ExtractionValidator`; the
    `GraphInserter`; the ontology manager. Writes `data/reports/<doc>/extraction_by_source.json`.
11. **Phase 9 — Report** → `data/reports/<doc>/report.md`.

Every claim carries a **context key**: predicate + parameter role (normal/minimum/design/trip...) + location
(suction/discharge...) + operating mode + scenario (Basrah, Bombay High, BH/PG mode) + pressure basis
(absolute/gauge) + qualifier.

**LLM extraction has never produced graph output.** Every `extraction_by_source.json` in `data/reports/` records
only `rule` and `table` sources (for the CDU manual: 364 table claims, 40 rule claims plus 21 weak, 116 rule
relationships); `API610 pump operation manual` and `Bulletin-5EH-Steam-Jet-Ejectors` record nothing. The graph is
therefore deterministic-only. The workbench does not read Neo4j at all (§2.3); it re-runs the same deterministic
modules over the JSON artefacts.

Known pipeline limitations (from the code): `--clean` and `--rebuild` run `MATCH (n) DETACH DELETE n` at Phase 7
of *every* document, so with several PDFs in `data/raw/` only the last one's graph survives; if Neo4j is down,
Phase 8 still runs and can mark extraction complete with nothing stored; `python -m knowledge_layer --help` is
advertised in `__main__.py` but there is no argument parser, so it runs the pipeline.

### 2.2 Knowledge-layer mechanisms

**Parse validation** (`parse_validation.py`, `validate_parsed_document`). A list of checks, each with a severity.
Error-level checks: every page covered by a successful Docling window, at least 80 % of pages with text, provenance
pages consistent, reading order unique, tables detected with cells and no "(N cells)" placeholders, content tables
beyond the page-header box, text extracted, no parser errors. Warning-level checks include runs of 3+ empty pages,
bounding-box coverage, heading detection, and a "representative table" check for seven categories (abbreviations,
TOC, material balance, equipment, operating limits, safety, checklists). Any failed error-level check prints each
failure, writes `parse_report.md`, and stops the document without marking the parse phase complete.

**Table classification** (`table_classifier.py`). First, page-template tables are detected: cell text (digits
replaced by `#`) that repeats on ≥ 50 % of pages marks a table as page furniture. Then a first-match cascade assigns
one class: header, approval, TOC, abbreviation, equipment / instrument (≥ 3 tag matches), material balance,
operating limit, process data, safety, maintenance, chemical, decorative, else unknown. The normalizer drops TOC,
approval, header/footer, administrative and decorative tables. The schema has 18 classes; footer, signature,
administrative and engineering are declared but never assigned.

**Table reconstruction** (`table_reconstructor.py`), for tables whose class enters extraction: merged-cell
propagation, header normalization with a numeric/tag/text data type per column, exact duplicate-row removal.
Limitation: only the document profile consumes the reconstructed tables; `extract_table_claims` reads the raw parsed
table and ignores its `classification` argument, so unknown-class tables that survive normalization still produce
claims.

**Table context** (`table_context.py`, `build_table_contexts`). Walking the normalized elements in reading order,
each table gets a `TableContext`: the short label that introduces it (a heading, list item or paragraph of ≤ 120
characters that ends in ":", is numbered, or is mostly capitals), the enclosing numbered heading, section path,
page, the scenario in force (Basrah, Bombay High, Kuwait, Kirkuk, Arabian, SKO, ATF, design/check case, BH/PG mode;
scoped until the next numbered heading), and `continuation_of` when a header-less table with the same column count
continues the previous one. The label (or heading) becomes the claim `qualifier`, which is part of the context key.

**Structure and TOC** (`structure.py`, `normalizer.py`). `parse_toc` reads the contents table into chapter nodes
with page ranges; `chapter_page_map` assigns each page to a chapter ("Chapter No: N" template cells first, then
loose text, then TOC ranges); `running_title_texts` finds running titles to drop. The normalizer emits one synthetic
level-1 heading per chapter, infers heading levels from numbering ("3.2.1" → level 3), and keeps a heading stack to
build `section_path`. `detect_procedures` marks runs of ≥ 3 list items as a procedure when ≥ 50 % are imperative
(≥ 25 % when a procedure heading introduces them), numbers the steps, types the procedure from keywords (emergency,
changeover, shutdown, startup, isolation, ...) and records the tags it applies to.

**Document graph** (`document_graph.py`, Phase 7a). Writes Document → Chapter → Section → Subsection; Procedure →
ProcedureStep (with `NEXT` between steps), `IN_SECTION`, `APPLIES_TO` / `MENTIONS` to Entity stubs; section cross
references (`REFERENCES`); standing instructions (`INCORPORATED_IN` a chapter); referenced documents.

**Extractor passes** (`extractor.py`, `OllamaExtractor`). The pipeline checks `GET /api/tags` for the configured
model (`deepseek-r1:7b` by default, `RKL_OLLAMA_MODEL` to override); if it is absent, or `--no-llm` is given, only
deterministic passes run. The LLM runs on engineering chunks that are not `document_control` or `toc`. Pass 1 uses
`prompts/entity_extraction.txt`; pass 2 uses `prompts/relationship_extraction.txt` and runs whenever pass 1 found
≥ 2 entities. Calls go to `/api/generate` with `format: "json"`, `num_ctx` 8192, temperature 0.1, `num_predict`
3072, up to 2 attempts on invalid JSON; failures are written to `data/failures/<chunk>_failure.json`, and the
deterministic passes still run for that chunk. The other seven prompt files in `knowledge_layer/prompts/` are not
used by any code.

**Deterministic passes.** `spec_claims.py` turns label/value specification blocks and prose sentences into claims
(`source="rule"`, confidence 0.75); `table_claims.py` maps table grids to claims in four layouts (property rows ×
condition columns, subject rows × property columns, property rows × subject columns, row label names the property;
`source="table"`, confidence 0.8); `rule_relations.py` emits FEEDS, PART_OF, RECEIVES_FROM, DISCHARGES_TO,
SUCTION_FROM, PROTECTED_BY, CONTROLLED_BY, UPSTREAM_OF/DOWNSTREAM_OF from regex rules (0.75 when both ends are
known, 0.6 when one is); `glossary_builder.py` takes abbreviation-table entries and inline "CDU (Crude ...)"
definitions; `reference_tracker.py` marks referenced documents present or missing in `data/raw/` (per document, in
memory only).

**Validator** (`validator.py`, `ExtractionValidator`). Entities are rejected with no name, or when neither the quote
nor the name is found in the chunk (a paraphrased quote is re-anchored to the naming sentence with a warning).
Claims and relationships are rejected when the subject is a value or axis label, when grounding is "none", when the
unit family is impossible for the predicate, or when the relationship is physically impossible (a motor FEEDS
something). "Weak" grounding (subject and value both in the chunk but apart) is kept with confidence capped at 0.5.
Items with an error are not inserted. The contradiction layer is effectively inactive, because it needs
`subject_uid`, which is only set later by the inserter.

**Entity identity and resolution** (`entity_identity.py`, `entity_resolver.py`). Tags follow
`[plant-]PREFIX-number[suffix]`; "A/B" is a train list whose canonical form is the parent, with `HAS_TRAIN` edges to
the children; `entity_uid = md5(canonical)[:16]` is global. A tag match is the only automatic merge. An untagged
entity gets a document-scoped uid plus `SAME_AS` candidates (glossary match 0.9, same-unit name match 0.85,
vector-assisted similarity); only candidates ≥ 0.8 are kept, at most five, each with confidence, method and
evidence.

**GraphInserter: CONFLICTS_WITH / CORROBORATES** (`graph.py`, Cypher in `memory.py`, `upsert_claim`). A new claim
is compared with claims on the same subject entity that share its context key and unit, come from a different table
(or have no table), and are not the same sentence. A different value → `CONFLICTS_WITH` and both become
`potential_conflict` (unless already confirmed, superseded or rejected); an equal value → `CORROBORATES` and
unreviewed claims become `supported`. A missing value is never a conflict. Claims are never overwritten
(`MERGE ... ON CREATE`).

**MemoryRetriever** (`retriever.py`). Runs per chunk before extraction when Neo4j is up: existing claims and
relationships for tag-like names, 1-hop related equipment, glossary terms, known identities, similar chunks,
referenced procedures. Only part of it is used — the rendered `graph_context` in both LLM prompts and the
known-identity names for rule relations; similar chunks, section entities and procedures are fetched but not
rendered.

**Ontology manager** (`ontology_manager.py`). Tracks entity types and predicates the model produced outside the
preferred vocabulary. A label seen in ≥ 2 documents and ≥ 5 chunks is promoted into `data/knowledge/ontology.json`
and written between marker comments into `prompts/entity_extraction.txt` (the pass-1 prompt file is
self-modifying). With LLM extraction never having produced output, nothing has been promoted from model output.

**Checkpoint and resume** (`checkpoint.py`). `data/checkpoints/<doc>_pipeline.json` holds completed phases,
completed and failed chunks, and the source hash; writes are atomic; a changed source hash resets it. Resume is
implicit — rerunning skips completed phases and chunks. Flags (read from `sys.argv`, no parser): `--clean` resets
the post-parse phases and clears Neo4j; `--rebuild` keeps only the parse; `--retry-failed` processes only failed
chunks; `--no-llm` runs the deterministic passes only; `--max-pages N` limits extraction to chunks starting on or
before page N. `scripts/reset_phases.py` resets named phases.

**Reporter** (`reporter.py`). `report.md` per document: overview, structure, normalization, chunks, procedures,
tables and classes, extraction summary, document graph, recall by source, glossary, cross references, standing
instructions, referenced documents present/missing. The "Validation" section never renders, because the pipeline
does not pass the validation summary to it.

### 2.3 The workbench (`workbench/`)

The workbench is the agent layer. It never imports extraction internals; it talks to the knowledge through one
interface, `KnowledgeService` (`workbench/services/protocols.py`), with typed records
(`workbench/core/knowledge.py`: `EntityRecord`, `ClaimRecord`, `RelationRecord`, `ProcedureRecord`, `ChunkRecord`,
`SectionRecord`, ...). The backend is chosen by `knowledge_backend` (default `auto`, `RWB_KNOWLEDGE_BACKEND` to
force): `auto` means `files` when knowledge-layer artefacts exist, else `mock`.

- **`files`** (`workbench/services/backends/files_backend.py`) — what `auto` selects today. It loads the artefacts
  above, re-runs the knowledge layer's own deterministic modules (`spec_claims`, `table_claims`, `rule_relations`,
  `entity_identity`) and builds an in-memory index per document (`workbench/services/index/builder.py`; sizes in the
  table in §1). The index is cached in `data/workbench/cache/<doc>/index.json`; for the CDU manual a cold build took
  about 15 s and a cached load about 3 s when measured on 2026-09-15.
- **`mock`** (`mock_backend.py`) — tiny fixtures for machines without artefacts.
- **`neo4j`** (`neo4j_backend.py`) — a 20-line stub. `RWB_KNOWLEDGE_BACKEND=neo4j` raises `NotImplementedError`
  with a pointer to `docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md`. Switching to Neo4j later means implementing this
  one class; no agent changes.

### 2.4 Uploaded documents

Uploads are **per conversation**, not added to the shared corpus. A PDF attached in chat is indexed in a
background thread through the knowledge layer's own modules (Docling parse → table classifier → normalizer →
chunker → profiler → glossary → `build_document_index`, `origin="upload"`, `authority_rank=40`;
`workbench/services/ingest.py`), cached by content hash under `data/workbench/uploads/_cache/`, and held in
`Orchestrator.session_uploads[<username>__<session_id>]` as a `SessionDocumentsBackend`. `_knowledge_for` gives
that session a `CompositeKnowledgeService(shared, [its uploads])` (`backends/composite.py`); no other session or
role sees the file. The uploader may read it whatever their role (its id is added to the allowed set for the guard
and the release gate), and when the question refers to the attachment ("this document", "the attached file") the
whole run is narrowed to it. Upload backends search with BM25 only (no vectors, no reranker). After a restart,
`_ensure_uploads` rebuilds the backend from the PDF paths recorded in the session file. Images are OCR'd and
described (§15.4) but not indexed.

`promote_upload` (`POST /knowledge/documents/{id}/promote`) is the deliberate opposite: a manager or admin only; it
re-indexes the PDF with `persist=True` into the knowledge layer's directories, adds it to the shared service,
classifies it with the given tag or `UPLOAD_TAG` (`CONFIDENTIAL`), drops the session copy, and writes an
`upload_promoted` security-audit event.

---

## 3. The agents

Seventeen agent keys are registered in `workbench/agents/registry.py`: the twelve named agents of the design,
four retrieval specialists (the "Graph Retrieval / Document Retrieval / Equipment-Specification /
Evidence-Provenance" boxes of the architecture diagram), and the Answer Composer. In the implementation the
specialists run as Phase 4 plan steps, not in Phase 2 (§4).

### 3.1 The twelve named agents

| agent (key) | phase | job in one sentence | input → output | used by task types |
|---|---|---|---|---|
| Task Classifier (`task_classifier`) | 0/1 | Decides the task type with weighted phrase rules; asks the LLM only when the rules are unsure (confidence < 0.72). | request text → `ClassifierOutput` (type, secondary types, confidence, method) | every request |
| Context Resolver (`context_resolver`) | 0/1 | Turns text into a `StructuredRequest`: equipment tags/names, values with units, symptom, action, scenario, operating mode, session pronouns, ambiguity, safety status. | text + session → `StructuredRequest`; as a plan step produces the `clarification` block | every request; `ambiguous` |
| Planner (`planner`) | 3 | Builds the DAG from a template per task type, extends it for compound requests, prunes it, validates it, caps it at 12 steps; refines planning requests with one LLM call at high/ultra; as a step lists gaps and assembles a work plan. | `StructuredRequest` → `Plan` | every request; `planning` (steps `gaps`, `assemble`) |
| Procedure Agent (`procedure`) | 4 | Finds documented procedures and returns prerequisites and verbatim ordered steps with page evidence. | entity + action → `steps` blocks | `procedure`, `troubleshooting`, `safety`, `planning`, `report` |
| Diagnostic Agent (`diagnostic`) | 4 | Finds the documented upset section, extracts causes, ordered checks and corrective actions; adds topology hypotheses marked *inferred*. | symptom + entity → tables and `steps` blocks | `troubleshooting`, `planning` |
| Calculation Agent (`calculation`) | 4 | Builds the min/normal/max/design envelope from claims and compares a given value with it; pure arithmetic, no LLM. | claims (+ value) → `limit_gauge` | `limits`, `troubleshooting` |
| Comparison Agent (`comparison`) | 4 | Aligns values for two or more subjects (equipment, crude cases, modes, procedures) on matched context. | claims → `comparison` block | `comparison` |
| Safety Agent (`safety`) | 4 (plan step) | Retrieves documented precautions, isolation, PPE, interlocks; reviews procedures, actions and limit verdicts; enforces the bypass policy. | context → `safety` block + flags | `procedure`, `troubleshooting`, `limits`, `safety`, `planning`, plus restricted requests |
| Revision/Conflict Agent (`revision_conflict`) | 4 | Lists every documented value with document, revision, page and source; groups by context key; ranks differing values by authority and explains. | claims → `conflict` blocks, provenance table | `lookup`, `limits`, `comparison`, `conflict`, `provenance`, `report` |
| Report Agent (`report`) | 4 | Assembles the other agents' blocks under ordered headings, adds an executive summary, saves Markdown under `data/workbench/reports/`. | prior results → ordered report | `report` (and any task with `want_report`) |
| Verification Agent (`verification`) | 5 | Checks each statement's evidence exists and contains its numbers, that tags are known, and removes ungrounded LLM narrative. | all results → per-result score and `overall_score` | every request (run by the orchestrator, not a plan step) |
| Governance Agent (`governance`) | 5 | Applies policy (restricted requests, human review), aggregates confidence, labels evidence `[n]`, composes the `FinalResponse`. | results + plan → `FinalResponse` | every request |

The safety *gate* (clear / sensitive / restricted) is set by the Context Resolver in Phase 0/1; the Safety Agent
itself runs only where the plan puts a `safety` step. There is no separate safety pass in Phase 5.

The seventeenth key, the **Answer Composer** (`answer_composer`, `workbench/agents/composer.py`), is not a plan
step. It runs in Phase 5 between verification and governance on every answered request, and turns everything the
run gathered into the prose the engineer reads. It builds a compact brief from the material — documented values as
sentences, relationships with both endpoints named, condensed procedure steps, trimmed passages, the limit verdict,
what is missing — capped at `answer_brief_tokens` (1,500), asks the model for a structured
`{answer, assumptions, not_documented}` (JSON schema, purpose `compose_answer`), and checks every figure, tag and
equipment name in the reply against that brief. A reply that fails is re-asked once (`answer_retries`) naming the
problem, then replaced by the deterministic composer, which writes the same shape of answer from the same material;
the deterministic composer is also used whenever the model is off or absent (effort `low`, `RWB_LLM=off`, Ollama
down). Composition is skipped for restricted requests, clarifications, and runs that gathered no material
(`Orchestrator._skip_composition`). See `docs/ACCESS_AND_ANSWERS.md` §2.

### 3.2 The four retrieval specialists (`workbench/agents/retrieval.py`)

| agent (key) | job | used by |
|---|---|---|
| `lookup` (Equipment / Specification retrieval) | documented values for an entity (KPI or table), entity identification, limit gathering, inventory listing; falls back to grounded text passages | `lookup`, `limits`, `troubleshooting`, `planning`, `report`, `inventory` |
| `graph` (Graph retrieval) | flow traces, neighbourhoods, instruments (with the measured variable derived from the ISA tag letters), standby equipment | `multi_hop`, `explanation`, `troubleshooting`, `report` |
| `explanation` (Document retrieval) | grounded passages for why-questions, supporting text, consequences of deviation; optional 2–4 sentence LLM summary (high/ultra) | `explanation`, `multi_hop`, `limits`, `report`, replan fallbacks |
| `cross_document` (Evidence / Provenance retrieval) | matching sections, cross references, referenced documents, standing instructions | `cross_document`, `procedure`, `safety`, `inventory` |

Every agent subclasses `BaseAgent` (`workbench/agents/base.py`) and returns an `AgentResult`: render blocks,
evidence, checkable statements, a confidence, safety flags, and `missing` items when evidence requirements
were not met.

---

## 4. The workflow

### 4.1 Flowchart

Read from `Orchestrator._run` (`workbench/orchestration/orchestrator.py`).

```mermaid
flowchart TD
  U[User request<br/>text, session, token, access key, attachments] --> P0
  subgraph P0["Phase 0 — Access"]
    ID[Resolve principal from token<br/>open vault branches] --> KEY[Redeem access key if given<br/>bad key: warn, continue at own role]
    KEY --> DEC[AccessPolicy.decide over loaded documents<br/>+ session uploads + attachment scoping]
    DEC --> NA{cleared for nothing<br/>and no upload?}
  end
  NA -- yes --> DENY[unauthorized response<br/>no retrieval runs]
  NA -- no --> G[GuardedKnowledgeService<br/>for every agent]
  G --> P1
  subgraph P1["Phase 0/1 — Understanding"]
    FU[Follow-up resolution<br/>rewrite elliptical turn] --> TC[Task Classifier<br/>rules → LLM if unsure]
    TC --> CR[Context Resolver<br/>entities, values, scenario, ambiguity,<br/>safety gate]
    CR --> SV{attachment question<br/>with no entity?}
    SV -- yes --> INV[task type → inventory]
    SV -- no --> MR
    INV --> MR[Model router plan<br/>if routing active]
  end
  MR --> P2
  subgraph P2["Phase 2 — Retrieval"]
    CB[ContextBuilder.build<br/>route: claims / graph / proc / hybrid / inventory / none] --> PKG[ContextPackage]
  end
  PKG --> P3
  subgraph P3["Phase 3 — Planning"]
    PL[Planner: template per task type<br/>+ compound extensions + pruning<br/>+ LLM refinement for planning at high/ultra<br/>+ validation + 12-step cap]
  end
  P3 --> P4
  subgraph P4["Phase 4 — Execution (sequential)"]
    EX[Executor runs ready steps in order<br/>specialists, procedure, diagnostic, calculation,<br/>comparison, revision_conflict, safety, report, planner] --> RP{required step<br/>needs_replan?}
    RP -- "yes, within budget" --> RE[Replace step with fallback route] --> EX
  end
  RP -- no / budget spent --> P5
  subgraph P5["Phase 5 — Governance"]
    VE[Verification<br/>overall_score] --> SK{restricted, clarification<br/>or no material?}
    SK -- no --> AC[Answer Composer<br/>LLM or deterministic]
    SK -- yes --> GO
    AC --> GO[Governance<br/>blocks, confidence, HITL, citations]
    GO --> ENV[Security envelope → escalation offer<br/>→ classification stamp]
    ENV --> RG{Release gate<br/>provenance leak check}
    RG -- fail --> WH[answer withheld]
    RG -- pass --> OUT[FinalResponse]
  end
  OUT --> AU[HITL record · session turn · response store ·<br/>final audit entry · chained audit hash on audit block]
  AU -. events, btw status .-> UI[CLI / Web UI]
```

ASCII version:

```
request ─► [0 Access]         principal ─► access key ─► access decision ─► nothing readable? → refuse
        ─► [0/1 Understanding] follow-up rewrite ─► classify ─► resolve (+ safety gate)
                               ─► attachment survey instead of clarification ─► model routing plan
        ─► [2 Retrieval]       ContextBuilder: claims | graph | proc | hybrid | inventory | none ─► ContextPackage
        ─► [3 Planning]        template DAG (+ extensions, pruning, LLM refinement, validation, ≤ 12 steps)
        ─► [4 Execution]       run ready steps in order ──► results
                                  └─ required step asks for replan? → fallback step (budget 0/2/2/3 by effort)
        ─► [5 Governance]      verification → answer composer → governance
                               → security envelope → escalation offer → classification stamp → release gate
        ─► HITL record, session memory, response store, audit (hash-chained)
        (The safety gate is set in 0/1; the Safety agent runs only as Phase 4 plan steps.
         Audit entries are written at request, access, structured request, plan, replan, each step, final.)
```

### 4.2 One request, step by step

Request: **"The crude charge pump is operating at 520 m3/h. Is this acceptable?"**, asked by `admin` at the
default effort `medium`.

0. **Access** (`_run`, "Phase 0 Access"). The token resolves to the principal; no access key is given. The access
   policy finds all six documents readable for `admin`. (A `user` would be cleared for the four `INTERNAL` documents
   only: the run would continue over those, and the escalation offer in step 10 would say that restricted material
   bears on the question.) Every agent receives a `GuardedKnowledgeService` over the readable documents plus any
   uploads of this session. The request and the access decision are written to the audit trail and the security
   log.
1. **Follow-up resolution** (`memory/followup.py`). It is the first turn of the session, so it is `new` and the
   text is unchanged.
2. **Classify** (`agents/task_classifier.py`). "is this acceptable" and "operating at 520" match the `limits`
   rules with high confidence, so no LLM call is made. Result: `limits`.
3. **Resolve context** (`agents/context_resolver.py`). "crude charge pump" resolves through the alias index to
   the tagged entity 11-P-01 (with twins 11-PM-01 and 11-PT-01 that share the name). `find_quantities` extracts
   520 m3/h and maps the unit family to the parameter `flow_rate`. `limits` is in the safety-reviewed set, so
   `safety_status = sensitive`. Nothing is ambiguous, and there is no attachment, so no survey rewrite.
4. **Model routing** (`_route_models`). When the LLM client is the routed one (Ollama up and the profile model
   pulled), the router decomposes the request into sub-tasks and records which model would take each; the plan is
   emitted as a thinking step and logged before any model is called.
5. **Retrieve** (`services/context_builder.py`). Route `claims`: the claims for 11-P-01 and its twins are collected
   (482 m3/h normal, 219 minimum, 482 rated capacity, 520 design, discharge 24.45 kg/cm2, design pressure
   31.7 kg/cm2 ...) plus any conflicts. No text search, no embedder, no reranker.
6. **Plan** (`orchestration/router.py`, `template_plan` for `LIMITS` with a value):
   - `claims` — lookup (mode `limits`): retrieve normal / minimum / maximum / design values
   - `revision` — revision_conflict (mode `check`, optional): differing values?
   - `compare` — calculation (mode `check_value`): compare 520 m3/h with the envelope
   - `safety` — safety (mode `review_limits`, safety-sensitive): consequences of operating outside the envelope
7. **Execute** (`orchestration/executor.py`). The lookup returns a KPI block; the conflict check finds none; the
   calculation agent (`services/calculators/limits.py`) finds 520 equals the design flow and exceeds normal by
   +7.9 %, verdict `within_design`, and emits a `limit_gauge`; the safety agent adds a warning that operation above
   normal but within design needs supervision. No required step asked for a replan.
8. **Verify** (`agents/verification.py`). Every number in the agents' statements is found in the cited table rows;
   `overall_score` is the share of checked statements that are grounded.
9. **Compose and govern.** The Answer Composer builds the brief (values, verdict, safety note) and, at `medium` with
   a model available, asks it for the structured answer; the figures and tags in the reply are checked against the
   brief (otherwise the deterministic composer writes it). Governance then decides `requires_human_review = false`
   (the value is inside design), computes confidence, labels the evidence `[1]..[n]`, and builds the blocks: the
   composed answer block, then the agents' blocks in plan order (KPI, gauge, safety ...), verification notes,
   the executed plan (more than three steps), confidence, evidence and audit. `answer_markdown` in the default
   `brief` presentation is the composed prose, at most two supporting blocks of the kinds prose cannot carry
   (here the limit gauge and, if present, a table, trimmed to 8 rows; `kpi` is not one of the kept types), any
   DANGER/warning safety block, and one
   "*Source: CDU operating manual, p. ...*" line (§9).
10. **Security** (`_fill_security_envelope`, `_escalation_offer`, `_stamp_classification`, `_release_gate`). The
    response's `security` fields record principal, role, classification of the sources (`SECRET`), source,
    readable and withheld documents. Nothing was withheld, so no escalation offer. A classification footer line is
    appended. The release gate re-checks the provenance of every piece of evidence against what the caller may read
    and would withhold the whole answer on a failure.
11. **Bookkeeping.** HITL registry (only if flagged), the session turn, the full response in the response store,
    the `final` audit entry, and the audit chain's head hash stamped on the audit block.

In the deterministic benchmark (no LLM, access control off) this prompt took 411 ms end to end on 2026-09-16; with
the model composing the answer, the composer call dominates (seconds on the 4 GB card).

### 4.3 The architecture diagram versus the code

`docs/architecture/agent_workflow.png` is the original design brief and does not match the implementation. The
main differences:

- There is a **Phase 0 Access** step (identity, access key, access decision, refusal) that the diagram does not
  draw; its "Phase 0: Foundation" boxes (classifier, resolver, entity resolution, safety gate) are in practice one
  Understanding phase with just the Task Classifier and the Context Resolver, which does entity, scenario, mode,
  ambiguity, evidence and safety-gate work itself. Follow-up rewriting and model routing are not drawn.
- **Phase 2 is one `ContextBuilder.build` call**, not a set of agents. The retrieval agents run later, as Phase 4
  plan steps. There is no Instrumentation/Control agent.
- The planner does template, extension, pruning, validation and the 12-step cap in one step; execution is
  **sequential**, and a replan happens only when a required step returns `needs_replan`.
- **Phase 5 order** is Verification → Answer Composer → Governance → security envelope / escalation /
  classification stamp → release gate. There is no Safety pass in Phase 5, and the Report is a plan step, not a
  Phase 5 box.
- The Neo4j box describes a backend that is a stub today; the routing strip has no `inventory` or out-of-scope
  column.

### 4.4 Understanding-phase mechanisms

**Follow-up resolution** (`workbench/memory/followup.py`). Before classification, `classify_followup` sorts the turn
into `new`, `substitution` ("instead", "rather than", "what if we use"), `elaboration` ("and", "why", "what about"
with no subject, or a short turn), or `continuation` (a pronoun-only or ≤ 8-word turn with no subject). The first
turn of a session is always `new`; a turn whose previous turn resolved no entity is treated as `new`. The rewrite is
a deterministic template built around the previous subject ("— instead of X. Compare the proposed item with X ...",
"— for X" plus an angle word, pronouns replaced by X). When `use_llm_for_followup` and the effort's `llm_followup`
are on (medium and above), the model is also asked for a rewrite (structured, purpose `followup_rewrite`, 140 tokens,
last two turns) for every dependent turn; its version is kept only if it names a carried subject and is 3–60 words.
The original text is kept as `asked_text`.

**Tag matching** (`workbench/services/tag_matcher.py`). A tag the documents do not contain is parsed into unit,
class, number and train and scored against known tags: unit 0.30, number 0.35, class 0.25 (same class 1.0, same
family 0.85), string similarity 0.10, with a bonus when the sentence names a matching equipment type. The top
candidate is adopted silently when it scores ≥ 0.72 and is alone, leads by ≥ 0.08, or is near-certain; otherwise up
to four candidates ≥ 0.45 are offered back to the user. Misspelt names are matched by string ratio (≥ 0.62).

**Model router** (`workbench/models/router.py`, `models/registry.yaml`). On by default (`routing_enabled`,
`RWB_ROUTING=off` to pin every call to the profile model), and active when the LLM client is the routed one, i.e.
Ollama is reachable and the profile's model is pulled (`llm/client.py`, `build_llm`); otherwise the workbench runs
with no model. Each call names a purpose (`classify`, `compose_answer`, `diagnose`, ...), which maps to one of
eleven task kinds (classification, resolution, extraction, summarization, composition, reasoning, calculation,
code, vision, planning, tool_agent). The registry declares seven models with a score per kind: qwen3:4b,
qwen3.5:2b, deepseek-r1:7b, qwen2.5-coder:7b, qwen3.5:9b, gpt-oss:20b, qwen3.6:27b (an optional
`data/workbench/models/registry.local.yaml` adds or overrides entries). Not-installed models score 0; models that
do not fit the VRAM are penalised (×0.6, and ×0.5 more on the `fast` budget used by the cpu and gpu_4gb profiles);
thinking models get ×0.8 on `fast`; the resident model gets +0.02 and is kept unless another beats it by 0.08;
vision calls need image input. With nothing usable it falls back to the profile model. Every decision is written to
the hash-chained `data/workbench/routing/routing.jsonl`, and the per-call routing rows and `models_used` are
stamped on the response's audit block. On the 4 GB card only qwen3:4b and qwen3.5:2b fit; deepseek-r1:7b is the
knowledge layer's extraction default, not something the workbench needs.

---

## 5. Routing matrix and plan size

`ROUTING_MATRIX` and `RETRIEVAL_ROUTE` in `workbench/orchestration/router.py`:

| task type | primary agents | retrieval route | template steps |
|---|---|---|---|
| lookup | lookup (+ revision_conflict check) | claims | 2 |
| multi_hop | graph (+ explanation support) | graph | 2 |
| procedure | procedure ×3 (find, prerequisites, steps), cross_document, safety | proc | 5 |
| troubleshooting | lookup identify, calculation range, diagnostic ×4, graph, procedure, safety | hybrid | 9 |
| limits | lookup limits, revision_conflict, calculation, safety (or explanation consequences) | claims | 3–4 |
| explanation | explanation, graph | hybrid | 2 |
| safety | safety, procedure (safety/isolation types), cross_document | hybrid | 3 |
| comparison | comparison ×2, revision_conflict | claims | 3 |
| conflict / provenance | revision_conflict ×2 (collect, resolve) | claims | 2 |
| planning | lookup, procedure, lookup limits, diagnostic, safety, planner gaps, planner assemble | hybrid | 7 |
| report | lookup ×2, graph, explanation, procedure, revision_conflict, report | hybrid | 7 |
| cross_document | cross_document ×2 (find, follow) | hybrid | 2 |
| inventory | lookup inventory, cross_document scope | inventory | 2 |
| ambiguous | context_resolver (clarify, or the capability reply when out of scope) | none | 1 |

The plan size follows the question: a lookup needs one retrieval and one conflict check; troubleshooting needs
identification, normal range, upset section, causes, checks, actions, topology, related procedures and a safety
review, each depending on the previous ones. Compound requests ("investigate the causes ... and give me the
changeover procedure ... with supporting documents") get extra optional steps from `extend_for_secondary` in
`agents/planner.py`. Steps that need an entity (graph, procedure, calculation) become optional when none resolved.
The plan is capped at 12 steps. Verification, composition and governance are always run by the orchestrator, never
planned.

**Replanning.** When a required step returns `needs_replan` (or crashes), `_replan` marks it failed and appends one
optional fallback step — `explanation` (support or consequences mode) for procedure, diagnostic, calculation,
lookup, graph, comparison and revision_conflict failures, `cross_document` (find) otherwise — and re-points its
dependants at the fallback, making them optional. The budget is `max_replan_iterations` from the effort level
(0 / 2 / 2 / 3); when it is exhausted, remaining pending steps are skipped.

### 5.1 Survey questions

"What are all the equipments in the refinery?", "List all the pumps", "How many columns are there?" and "What
does this manual cover?" have no single subject, so demanding one would be wrong. They classify as `inventory`
and take the `inventory` retrieval route, which lists the knowledge layer's entity index instead of searching
it: a class table (how many of each kind, with the most-referenced tags), the items themselves with their tag
and page range, then the documents, chapters and standing instructions in scope.

Counting and listing share one filter, so the summary never contradicts the table, and the filter is the
manual's own tag convention: an item is equipment when its tag is plant-numbered (`11-C-01`), not when a tag
pattern merely matches. That keeps checklist rows (`C-1`) and standard names (`D-86`, the ASTM distillation
method) out of the equipment count — 6 columns and 6 heaters rather than 13 and 12. A named plant section
filters further on the plant number itself (11- atmospheric, 12- vacuum; `SCOPE_TAG_PREFIXES`). When fewer than
five plant-tagged items are found (a vendor catalogue or a standard), the count is widened to untagged names
(`THIN_INVENTORY`). The number of rows follows the effort level (30 / 30 / 120 / 400).

A question that points at a document attached in this conversation and names no equipment ("tell me about this
document") is turned into an inventory survey of that attachment instead of a clarification
(`_survey_instead_of_clarifying`).

A request the documents cannot serve at all — a greeting, a general-knowledge question, "write me a poem", or
text with nothing recognisable in it — is not sent to clarification either. The resolver marks it out of scope
and the answer says what the workbench does answer, with examples built from the documents actually loaded.

### 5.2 Phase 2 retrieval: the ContextBuilder

`ContextBuilder.build` (`workbench/services/context_builder.py`) fills one `ContextPackage` along the route of the
task type:

- **none** (ambiguous): nothing is retrieved.
- **inventory**: entity-type counts, the entity list (effort `inventory_limit`), matching sections, standing
  instructions.
- **claims** (lookup, limits, comparison, conflict, provenance): the claims of every resolved entity and its tag
  twins, plus conflicts; with no entity, claims searched by the unresolved subject text ("Basrah").
- **graph** (multi_hop): relations up to `graph_hops` + 1 hops, plus up to six chunks that mention the entity.
- **proc** (procedure): the procedure index (applies-to tags, step tags, title/section match, action), top 8, plus
  standing instructions.
- **hybrid** (troubleshooting, explanation, safety, cross_document, report, planning): `search_chunks` with the
  task's chunk types and chapter preferences, anchored on the resolved entities; cross-document questions also get
  sections, document references, cross references and standing instructions.

Several task types also collect claims, relations or procedures besides their route (troubleshooting, planning and
report collect all three).

Chunk-type and chapter preferences per task type (`CHUNK_TYPE_PREFS`, `CHAPTER_PREFS`):

| task type | chunk types (hard filter) | chapters (soft preference) |
|---|---|---|
| troubleshooting | upset, safety, control, narrative, procedure, equipment | 17, 18, 14, 16, 7, 15 |
| explanation | narrative, equipment, control, safety | 5, 6, 7, 2, 3 |
| safety | safety, procedure, narrative, equipment, upset | 27, 30, 31, 23, 32, 19, 16 |
| limits | table, equipment, safety, narrative, control | 14, 16, 25, 3 |
| procedure | — | 16, 32, 12, 13, 20, 21, 15 |
| cross_document | procedure, narrative, safety, document_control, equipment | 1, 16, 32, 34 |
| report | narrative, equipment, table, control, procedure, safety | 3, 5, 6, 14, 16, 25 |
| planning | procedure, safety, upset, equipment, narrative | 16, 32, 14, 17, 27, 31 |
| multi_hop | narrative, equipment, control, procedure | — |

The chapter numbers are the CDU manual's (Operating Limits = 14, Upsets = 17–18, Emergency/Restart = 19–20,
Shutdown = 21, SOPs = 32, Safety = 27/30/31). They are applied to every loaded document, where they mean nothing in
particular; because they are soft, this only affects ordering.

**Retrieval maths** (`workbench/services/index/store.py`, `search_chunks`). BM25 (over section path + text) and
vector search (cosine against the bge-small vectors) each return a pool of `max(4k, 24)` chunks, where `k` is the
effort's `retrieval_k` (5 / 8 / 12 / 20). The two rankings are fused with reciprocal rank fusion,
`1 / (rrf_k + rank)` with `rrf_k = 60` and rank starting at 0. Chunks that mention a resolved entity (or its train
children) get an entity bonus of `+1/rrf_k` if already in the fused set, `+0.5/rrf_k` if not. Filters: `chunk_types`
and `document_ids` are **hard** (a chunk of another type is never returned); `chapters` is **soft** — when fewer
than `k` candidates survive it, the unfiltered ranking (still restricted to the chunk types) is appended.
Candidates are capped at `max(2k, 12)`. When `rerank` is set — only in the Phase 2 hybrid pass, and only when the
effort and the profile allow the reranker (high and ultra) — a cross-encoder rescores the first 900 characters of
each candidate; agents' own follow-up searches never rerank. With vectors off (effort `low`), BM25 and the entity
bonus alone decide. (`RetrievalSettings.bm25_k`, `vector_k` and `fused_k` exist in the config but nothing reads
them.)

---

## 6. Five worked examples

These describe the material the agents gather (the typed blocks). In the default `brief` presentation the
engineer reads composed prose on top of this material (§9).

**Lookup — "What is the normal flow rate of the crude charge pump?"**
Route `claims`; the lookup agent filters the pump's claims to flow-rate predicates and the role *normal*.
Material: "volumetric flow rate = 482 m3/h (normal)", a `kpi` block, the conflict check (none), the evidence with
the table row from page 225.

**Procedure — "How do I change over from the running crude charge pump to the standby pump?"**
Route `proc`; the procedure index scores procedures by applies-to tags, step tags, title/section match and the
requested action (`changeover`). Material: a table of matching procedures, a `steps` block of prerequisites, one
`steps` block per procedure with page per step and inline warning badges, a cross-reference table, a `safety`
review block, the executed `plan`, and `requires_human_review = true` because changeover is a high-risk
procedure type.

**Troubleshooting — "The crude charge pump discharge pressure is dropping. What should I check?"**
Route `hybrid` with chunk types upset / safety / control / narrative / procedure / equipment and chapter preference
17, 18, 14, 16, 7, 15. Material: equipment identification table, the normal-range `limit_gauge`, a table of the
upset sections consulted, a *Possible causes* table (documented causes with page; topology hypotheses marked
*inferred*), a `steps` block of diagnostic checks in the manual's order, a `steps` block of corrective actions, a
`graph` of upstream equipment and instruments, related procedures, a `safety` block on the actions, the nine-step
executed plan.

**Conflict / provenance — "I found two different normal flow values for the crude charge pump. Which one should I trust?"**
Route `claims`; the revision/conflict agent lists every value with document, revision, page and source type, then
groups by context key. The 482 m3/h *normal* (p.225, 11-PM-01A/B) and the 482 m3/h *rated capacity* (p.60,
11-P-01A/B) have different context keys, so the answer is a "values that differ because their context differs"
table plus a callout explaining roles, locations, cases and pressure basis, not a conflict card. Where two values
share a context key and differ, a `conflict` block ranks them by authority (§7) and names the preferred one; the
other is kept as evidence.

**Planning — "Prepare an engineering investigation plan for repeated trips of the vacuum heater."**
Route `hybrid`; the template runs scope → procedures → limits → causes → safety → gaps → assemble. Material: an
identification table, procedures found, limits, causes and checks, safety items, an *Information still required*
table (for example field readings and DCS trip history), and a `plan` block — the proposed work plan as a DAG
with safety-sensitive tasks marked — plus `requires_human_review = true` because plans are proposals.

---

## 7. Evidence and verification

- **Claims with context keys.** The knowledge layer records 482 m3/h *normal*, 219 m3/h *minimum* and 520 m3/h
  *design* for the crude charge pump as three different facts, because the context key includes the parameter
  role. Suction 2.0 kg/cm2 and discharge 24.45 kg/cm2 never meet because location is part of the key, and
  kg/cm2A and kg/cm2G are different quantities because pressure basis is part of the key. Only two values with the
  same key from different places count as a potential conflict.
- **Authority ordering** (`workbench/services/revision_resolver.py`, `authority_score`). Differing values with the
  same key are ranked by the tuple (temporal status: current 3, design/unspecified 2, historical 1, defunct 0;
  document authority rank, default 60, uploads 40; revision number; source type: table/specification 3, rule/prose
  2, LLM 1; confidence; page), highest first. `explain_preference` writes the "Preferred because ..." text; the
  losing value stays as evidence.
- **Citations.** Every agent attaches `Evidence` objects (document, page, chunk id, claim id, sentence or table
  row). The governance agent de-duplicates them and labels them `[1]`, `[2]`, ... in order of first use; blocks
  point at the labels, and the `evidence` block lists them (`workbench/services/evidence_store.py`).
- **Verification** (`agents/verification.py`) runs in Phase 5, called by the orchestrator after execution and
  before composition. For each statement an agent made it checks: its evidence keys exist; the numbers in the
  statement appear in the cited evidence text (tolerant to "24.45" vs "24,45"; page and step numbers are ignored,
  and derived percentages in calculations are allowed when the inputs are in evidence); equipment tags in text and
  callout blocks resolve to known entities; LLM narrative blocks titled "Answer" or "Executive summary" whose
  numbers cannot be found are removed. It records a per-result score for the trace, but only the aggregate
  `overall_score` (grounded statements / checked statements) feeds the final confidence. The composed answer is
  not checked by this agent; the composer runs its own check against its brief (§3.1).
- **"Not documented" over guess.** When nothing is documented, agents put the requirement in `missing`, show a
  warning callout, and the response lists "not documented: ..." in `warnings`; the composer's `not_documented`
  list is printed under the answer.

---

## 8. Safety and governance

- **Safety status** (set by the Context Resolver): `clear`; `sensitive` (safety-reviewed task types — procedure,
  troubleshooting, limits, safety, planning — or safety wording; the plan's safety step reviews the answer);
  `restricted` (wording matching `governance.restricted_patterns` in `config.py`: `bypass`/`bypassing`, `defeat`,
  `disable ... trip|interlock|protection|alarm|safeguard`, `override`, `jumper`, `force ... interlock|trip`,
  `inhibit ... trip|interlock`).
- **Bypass policy** (`agents/safety.py`, `_restricted`). A restricted request never gets bypass steps under any
  wording. The answer contains a DANGER flag, the documented authorization sentences (approval, shift in-charge,
  management of change) found in the documents, the standing instructions that may apply, and
  `requires_human_review = true`. It is returned verbatim — the Answer Composer is skipped. If the equipment is not
  even named, the clarification still carries the restricted banner.
- **Human in the loop** (`agents/governance.py`, `_review_decision`). Review is required for restricted requests,
  a value outside the design envelope, emergency / isolation / changeover / shutdown procedures, any DANGER-level
  documented precaution, and every planning answer. Flagged responses are recorded in
  `data/workbench/audit/hitl.jsonl` and listed by `GET /reviews`; the answer is still delivered with the flag,
  because the workbench advises and never actuates.
- **Audit trail** (`services/audit_store.py`). One hash-chained JSONL file per session in `data/workbench/audit/`
  (`HashChainedLog`: each entry's hash is SHA-256 over sequence, timestamp, previous hash and the canonical
  payload, starting from 64 zeros). A run writes `request`, `access`, `structured_request`, `plan`, `replan`,
  `step_result` (one per step), `final` and, on a crash, `error`. After the `final` entry the chain is verified and
  its head hash is stamped on the response's audit block (`chained_audit_hash`). The `audit_trail_id` is in every
  response. Limitation: the files are named by the raw session id (not the `<owner>__<session>` key), and the
  response store saves the response before the head hash is stamped, so stored responses do not carry it.
- **Confidence** (in words): a weighted mean of the agents' confidences (optional steps count half, failed steps
  0.3 of that; verification, governance and report excluded; 0.3 when there is nothing to weigh), multiplied by
  `0.6 + 0.4 × overall_score`, minus 0.05 per missing evidence item (capped at 0.25). Below 0.35
  (`min_confidence_to_answer`) an `answered` status becomes `needs_review` ("a lead, not an answer"). Levels: high
  ≥ 0.75, medium ≥ 0.45, low otherwise.

### 8.1 The security path through a run

A summary; the full model, the escalation workflow and the known weaknesses are in `docs/SECURITY.md`.

1. **Identity first.** The principal is resolved from the token before the session is opened or the question is
   classified; sealed vault branches the role holds keys for are opened.
2. **Access key.** An access key is verified against the caller and this exact question (`escalations.redeem`); a
   bad key is logged (`key_rejected`) and the run continues at the caller's own role.
3. **Decision.** `AccessPolicy.decide` over the loaded documents gives allowed and withheld documents plus any
   granted records. A caller cleared for nothing, with no upload of their own, gets an `unauthorized` response
   listing the documents and who may read them; no retrieval runs.
4. **Guard.** Every agent gets a `GuardedKnowledgeService` (`security/guard.py`) that drops any record from a
   document the caller may not read, unless a grant names that record.
5. **After governance, in order:** `_fill_security_envelope` (structured fields: principal, role, classification
   of the sources, source / readable / withheld / attached documents, grant, key error); `_escalation_offer` (when
   material was withheld and the caller is signed in without a grant: a callout saying how much restricted material
   bears on the question and who can release it, and, with `auto_raise_requests`, an access request raised
   automatically); `_stamp_classification` (a footer line with the classification, sources and recipient);
   `_release_gate` (`leakcheck.check_response` re-checks the provenance of the finished answer; a provenance
   failure withholds the whole answer; on release a grant is consumed).
6. **Logging.** Access decisions, key use, raised requests, blocked releases and consumed grants go to the
   security log (`data/workbench/security/security.jsonl`).

---

## 9. What the user sees: the blocks and the answer

Answers carry a list of typed blocks (`workbench/core/blocks.py`), so the web UI can draw the right component for
each piece instead of parsing prose. There are sixteen block types:

- **text** — prose: the composed answer, narrative, document listings.
- **callout** — the one-line lead (green/amber/red), warnings, the human-review notice, the escalation offer.
- **kpi** and **table** — documented values with per-row citations.
- **steps** — ordered procedure steps with prerequisites, page numbers, warning badges and equipment tags.
- **graph** — nodes and edges (documented links solid, inferred links dashed) plus Mermaid source.
- **plan** — the DAG that produced the answer, or a proposed work plan, with per-task status.
- **limit_gauge** — the measured value against minimum / normal / maximum / design / trip markers.
- **comparison** — a matrix of subjects × attributes with a differences list.
- **conflict** — each documented value with source, the preferred one and the reason.
- **safety** — flags by severity, with a lock icon when authorization is needed.
- **clarification** — the question back to the user with clickable options.
- **image** — an uploaded or referenced image.
- **confidence**, **evidence**, **audit** — the footer of every answer (the audit block carries phases, LLM call
  count, routing rows, models used and the chained audit hash).

**Block order** (`GovernanceAgent.compose`). For a clarification: the clarification (with the restricted banner
first when restricted). For a restricted request: the restricted lead and the safety agent's blocks. For a composed
answer: the composer's blocks first, then either the report's blocks or every agent's blocks in plan order. Without
a composed answer (the pre-composer path): a lead callout (a limit verdict if there is a gauge), then the agents'
blocks in plan order with the safety blocks last (first for `safety` questions). In every case follow a safety
summary if no safety block is present, verification notes, the executed plan (answered runs with more than three
steps), the human-review callout, confidence, evidence and audit. Duplicate blocks are dropped.

**The released text** (`answer_markdown`, `PresentationSettings`). In the default `brief` style
(`RWB_ANSWER_STYLE=full` to change it), when a composed answer exists, the text is: the composed prose; at most two
supporting blocks (one per kind) of the types prose cannot carry — `steps`, `limit_gauge`, `comparison`, `table` —
trimmed to 8 rows; any safety block with a DANGER or warning flag; "Not covered by the documents" and "Assumed" lines
from the composer; the grant or withheld-documents note; the human-review sentence; and one "*Source: document,
p. ...*" line naming at most six pages that reached the composer. The `full` style (and any run without a composed
answer) renders every block in order. After the release gate the classification footer and any escalation note are
appended. The CLI's `--detail` prints all blocks.

---

## 10. Resource strategy (why it runs on a 4 GB card)

Hardware profiles (`PROFILES` in `workbench/config.py`; chosen automatically from the detected VRAM, overridable
with `RWB_PROFILE`):

| profile | min VRAM | text model | vision model | context | reranker |
|---|---|---|---|---|---|
| cpu | 0 | qwen3.5:2b | qwen3.5:2b | 4k | off |
| gpu_4gb | 3.5 GB | qwen3:4b (2.5 GB) | qwen3.5:2b (2.7 GB, on demand) | 4k | bge-reranker-base on CPU |
| gpu_8gb | 7.5 GB | qwen3.5:4b | qwen3.5:4b | 8k | bge-reranker-base on CPU |
| gpu_12gb | 11.5 GB | qwen3.5:9b | qwen3.5:9b | 16k | bge-reranker-v2-m3 on GPU |
| gpu_16gb | 15.5 GB | qwen3.5:9b (gpt-oss:20b as text-only alternative) | qwen3.5:9b | 32k | bge-reranker-v2-m3 on GPU |
| gpu_24gb | 23 GB | qwen3.6:27b | qwen3.6:27b | 32k | bge-reranker-v2-m3 on GPU |

The profile model is the router's fallback and, with `RWB_ROUTING=off`, the model for every call. (The knowledge
layer's extraction uses its own setting, `deepseek-r1:7b` with an 8k context.)

Rules applied everywhere:

- **Few model swaps.** With the router on, each call may go to a different installed model, but the resident
  model is favoured (+0.02 and a 0.08 margin before a swap), and on the `fast` budget (cpu, gpu_4gb) models that
  spill off the GPU or think at length are penalised; on the 4 GB card that leaves qwen3:4b for text and qwen3.5:2b
  for images. The vision model is loaded only when an image is uploaded (`services/resources.py`,
  `describe_image`).
- **Idle unload.** After the last active request the model is unloaded from VRAM after 45 s on the 4 GB / CPU
  profiles (180 s on larger cards); `RWB_KEEP_WARM=1` disables this for demos.
- **Lazy CPU models.** The bge-small query encoder (~6 s to load) and the reranker (~29 s to load, ~2 s for 20
  passages) are loaded on first use, or warmed in the background when the server starts; they never touch VRAM on
  the 4 GB profile.
- **Cheapest route per task.** Lookups, limits, comparisons and conflicts use only the claim index (no text
  search); procedures use the procedure index; multi-hop uses relations; only troubleshooting, explanation,
  safety, cross-document, planning and reports use hybrid text retrieval, restricted to the chunk types that matter
  (§5.2). The reranker is used only at `high` and `ultra`.
- **LLM only where it is asked for.** Every call goes through `llm_json` (`agents/base.py`) with
  `format = <JSON schema>` and `think = false`, except two (below). The calls, by purpose:

  | purpose | where | schema | when |
  |---|---|---|---|
  | `classify` | `agents/task_classifier.py` | yes | rule confidence < 0.72 and effort ≥ medium |
  | `followup_rewrite` | `memory/followup.py` | yes | a dependent turn, effort ≥ medium |
  | `entity_guess` | `agents/context_resolver.py` | yes | nothing resolved, effort high/ultra |
  | `why_summary` | `agents/retrieval.py` (explanation) | yes | narrative on (high/ultra) |
  | `diagnose` | `agents/diagnostic.py` | yes | LLM extraction on (ultra, or `RWB_LLM_EXTRACTION=on`) |
  | `plan_refine` | `agents/planner.py` | yes | planning requests, effort high/ultra |
  | `compose_answer` | `agents/composer.py` | yes | every answered request, effort ≥ medium |
  | `report_summary` | `agents/report.py` | **no** (free text, filtered by `usable_narrative`) | narrative on (high/ultra) |
  | `tool_agent` | `tools/agent_loop.py` | yes | the agent tool loop, when a model is available |
  | vision | `llm/client.py`, `intake/vision.py` | **no** | image uploads and intake |

  So the default `medium` effort **does** call the model: the Answer Composer on every answered request, plus
  classification when the rules are unsure and follow-up rewrites. Only `low` (or `RWB_LLM=off`) is model-free.
  With no model every agent still works with template wording and the deterministic composer.
- **Measured on 2026-09-15** (GTX 1650, 4 GB, with only the CDU manual loaded): index
  build ~15 s cold / ~3 s cached; qwen3:4b ~5–6 s per short structured call once loaded (~14 s including the first
  load); reranker load ~29 s on CPU; embedder load ~6 s; a lookup end to end 45 ms, a limit check 58 ms, a nine-step
  troubleshooting run ~0.5 s without the LLM. These have not been re-measured with six documents.
- **LLM extraction is an effort decision.** With the model on, the explanation summary cost ~18 s (61 output
  tokens) and planning refinement ~20 s, but structuring causes/checks/actions from three passages produced 800+
  output tokens and took 90–120 s on the 4 GB card. The hardware profiles set `llm_extraction` off for `cpu` and
  `gpu_4gb`, but `apply_effort` then overwrites the flag with the effort level's value, so in practice the call is
  on only at `ultra` (on any profile) or with `RWB_LLM_EXTRACTION=on`, which is applied last. Otherwise the
  deterministic sentence extraction answers. When on, it is typed (`DiagItem{text, quote}`), capped at 700 tokens
  and five items per list, and computed once per request for the causes, checks and actions steps.
- **The reranker runs once per request**, in the Phase 2 retrieval pass; the agents' own follow-up searches use
  BM25 + vectors only. This cut a troubleshooting run from ~35 s to ~6 s on the CPU when it was introduced.

---

## 11. The thinking trace: seeing the agents reason

Every CLI run shows its reasoning as it happens and saves a structured copy for later analysis. Three pieces:

- **`orchestration/narration.py`** turns each stage's structured output into a few plain sentences: which rules
  matched and what they scored, what an entity resolved to and how, why a retrieval route was chosen, how the
  template became a DAG, what a step found in its own trace, how the answer was composed, and how governance
  reached its verdict. It is the single source of the reasoning text, so the terminal, the SSE stream and the saved
  JSON always agree.
- **`app/thinking_display.py`** renders that to the terminal phase by phase: an agent header, the reasoning
  lines in a gutter, the decision, and the timing / evidence / model line. Colour is used when the terminal
  supports it and dropped otherwise; every line is wrapped to the terminal width.
- **`services/thinking_store.py`** collects the same ProgressEvents into one JSON file per run under
  `data/workbench/thinking/{timestamp}_{audit_id}.json`.

`ProgressEvent` carries three extra fields for this: `thinking` (the narration), `model` (which model ran, when
one did) and `decision` (the one-line outcome). They are optional, so existing subscribers are unaffected, and
the SSE stream forwards them to the web UI.

```powershell
python -m workbench ask "What is the recommended way to start the CDU?"   # thinking is the default
python -m workbench ask "..." --no-thinking                               # answer only
python -m workbench ask "..." --json                                      # FinalResponse JSON, no thinking
python -m workbench trace                                                 # list saved traces
python -m workbench trace --show                                          # replay the newest one
```

What the terminal shows per phase:

```
── Phase 0/1 · Understanding ───────────────────────────────────────────────────

  [cls] task_classifier · classify
     Scoring the wording against the rule patterns of every task type
     │ Matched phrase patterns: "recommended way to, way to start".
     │ Rule scores: procedure=4.4, lookup=1.0.
     │ Rules were decisive (0.98), so the LLM was not called.
     │ Intent: Retrieve an ordered procedure with prerequisites.
   └▸ task type = procedure (confidence 0.98, rules)
     0 ms
```

An LLM call appears where it happens, with the model name and how long it took:

```
  [pln] planner · plan
     ⟨calling qwen3:4b — plan_refine⟩
     │ The LLM refined the step list for this planning request; the template steps are kept as the floor.
   └▸ template 'planning', 9 steps
     19.1 s · 1 LLM call(s) · qwen3:4b
```

The saved JSON holds more than the terminal: per-phase and per-agent timings and LLM counts, every LLM call with
its purpose and prompt name, the plan DAG with each step's final status, replans, warnings, the confidence
basis, the governance verdict, the answer markdown and the raw event list.

Because small local models sometimes restate the prompt or narrate the task instead of doing it, narrative
output passes `usable_narrative()` (`agents/base.py`) before it reaches an answer; rejected prose is replaced by
the deterministic rendering and the reason is recorded in the step's trace. The composer additionally strips
meta openings ("Okay, the user wants ...") and rejects a reply that repeats the previous answer.

---

## 12. Effort levels: how much work one request may do

`--effort low|medium|high|ultra` (or `RWB_EFFORT`) sizes the request. Every level answers from the same
evidence and the same agents; the higher ones look at more of it and let the model do more.
`EffortSettings` in `workbench/config.py` holds the knobs, and `WorkbenchConfig.apply_effort()` re-points
retrieval, governance and the LLM switches at the chosen level. The hardware profile keeps the last word only for
the reranker (a level cannot switch on a reranker the profile has no model for); the LLM switches follow the level,
and nothing calls a model that is disabled (`RWB_LLM=off`) or absent.

| | low | medium (default) | high | ultra |
|---|---|---|---|---|
| passages per hybrid search | 5 | 8 | 12 | 20 |
| graph hops (+1 for multi_hop) | 1 | 1 | 2 | 3 |
| procedure candidates | 4 | 6 | 10 | 16 |
| rows in a survey answer | 30 | 30 | 120 | 400 |
| replan budget | 0 | 2 | 2 | 3 |
| vector search | — | yes | yes | yes |
| reranker | — | — | yes | yes |
| model: follow-up rewrite (`llm_followup`) | — | yes | yes | yes |
| model: unsure classification | — | yes | yes | yes |
| model: missed equipment | — | — | yes | yes |
| model: narrative prose (why-summary, report summary) | — | — | yes | yes |
| model: causes / checks structuring | — | — | — | yes |
| model: plan refinement | — | — | yes | yes |
| model: composed answer (`llm_answer`) | — | yes | yes | yes |
| target answer length (`answer_words`) | 120 | 170 | 220 | 300 |

The composer's token budget is `max(profile num_predict_long, answer_words × 2.2 + 120)`.

Measured on the GTX 1650, same three questions at each level (development measurement, September 2026; not
re-measured for this revision):

| question | low | medium | high |
|---|---|---|---|
| "The crude charge pump discharge pressure is dropping..." | 0.7 s, 10 citations | 17.8 s, 10 citations | 22.3 s, 20 citations |
| "What are all the equipments in the refinery?" | 17 ms | 19 ms | 41 ms |
| "Why is the crude heated before entering the atmospheric column?" | 9 ms, no model | 29 ms, no model | 31.8 s, 1 model call |

`low` is the one to reach for during a live demo when the GPU is busy: it answers every question type from the
indexes alone, with the deterministic composer. `ultra` is minutes per request on a 4 GB card, because the model
structures the diagnosis and refines the plan.

---

## 13. The "btw" side channel

While a request runs, the user can type "btw, what is going on?" in the REPL (`python -m workbench repl`) or
call `POST /runs/{id}/btw` from the UI. A small status agent (`orchestration/status_agent.py`) answers from the
live run state — current phase and agent, steps done / total, partial summaries, timing and LLM-call counts,
safety status, loaded resources — as ordinary blocks, without any model call and without interrupting the main
run. The same run state feeds the SSE progress stream. (The orchestrator emits its own `final` event carrying only
the response id and status; see `docs/API.md` for how the stream delivers the full response.)

---

## 14. The benchmark set

`workbench/benchmarks/prompts.yaml` holds 70 prompts in 16 categories: lookup, multi_hop, procedure,
troubleshooting, limits, explanation, safety, comparison, provenance, planning, report, cross_document, inventory,
ambiguous, out_of_scope, complex. Each prompt lists the expected task type, the agents that must run, entity
substrings that must resolve, block types that must appear, and for bypass requests the expected restricted
handling. Run it with:

```powershell
python -m workbench bench                                   # deterministic mode (RWB_LLM=off), fresh session per prompt
python -m workbench bench --llm                             # with the local model
python -m workbench -v bench --category troubleshooting limits --limit 5   # -v is global: before the subcommand
```

The runner (`workbench/benchmarks/runner.py`) turns access control off, gives every prompt a fresh session, prints
task accuracy, agent coverage, entity resolution, block coverage, safety handling, mean confidence and latency
(`-v` also prints one line per prompt), and writes `data/workbench/reports/benchmark-<ts>.md`. Effort is the
default (`medium`), so with `RWB_LLM=off` the deterministic composer writes the answers.

### 14.1 Result on 2026-09-16 20:20 (deterministic mode, files backend, gpu_4gb profile, no LLM)

Report: `data/workbench/reports/benchmark-20260916-202058.md`.

| metric | value | meaning |
|---|---|---|
| prompts | 70 | all sixteen categories |
| task accuracy | 1.00 | task type after classification + ambiguity handling matches the expectation |
| agents ok | 1.00 | every expected agent ran in the plan |
| entities ok | 1.00 | the expected equipment was resolved (tag or name) |
| blocks ok | 1.00 | the expected block types appear (steps, kpi, limit_gauge, comparison, plan, safety, clarification) |
| safety ok | 1.00 | bypass requests were restricted (or clarified) and flagged for review |
| answered | 1.00 | no failed runs |
| mean confidence | 0.637 | governance confidence, averaged |
| mean latency | 434 ms | per prompt |
| total time | 30.6 s | 70 prompts in one process |
| LLM calls | 0 | |

Mean latency by category (computed from the report's rows): out_of_scope 12 ms, provenance 19 ms, ambiguous 24 ms,
multi_hop 27 ms, explanation 34 ms, inventory 36 ms, cross_document 46 ms, comparison 82 ms, report 103 ms,
limits 127 ms, safety 307 ms, procedure 603 ms, troubleshooting 647 ms, planning 651 ms, complex 790 ms,
lookup 2.1 s. The lookup mean is dominated by one prompt ("What are the operating limits of the atmospheric
heater?", 14.7 s), which is classified as `limits` and pays the first-use load of the embedding library; the other
six lookups take 13–47 ms.

The metrics check routing, coverage and safety handling, not the wording of the answers. An earlier run on
2026-09-15, before the fixes of that day, scored 0.82 task accuracy and 0.95 entity resolution on the then 62
prompts; the gains came from plural equipment names, single-word specific equipment ("the desalter"), whole-unit
scope ("restart the unit"), restricted requests always routed to the Safety agent, and stronger weights for
"which documents", "compare", "trace", "plan" and "report".

---

## 15. Other workbench subsystems

These sit beside the question path. They are wired in by `Orchestrator.attach_services` and exposed through
`workbench/app/api_ext.py` and `cli_ext.py`.

### 15.1 Response store (`services/response_store.py`)

Every released `FinalResponse` (typed blocks and evidence) is saved whole as
`data/workbench/responses/<response_id>.json` with owner, role, question and time, so a turn can later be exported.
The newest 2,000 are kept. Only the owner or an admin may load one.

### 15.2 Deliverables (`deliverables/`)

`export_response` turns a stored response into `docx` (python-docx), `pptx` (python-pptx), `xlsx` (openpyxl, with a
Values sheet, per-block sheets, a Calculation sheet with live formulas, Provenance, Evidence, Audit) or `md`. Each
carries the response's classification and the status "DRAFT — pending human sign-off", with a watermark.
`POST /deliverables` writes to `data/workbench/deliverables/<user>/<ts>_<response_id>.<fmt>`, records the file's
SHA-256, creates a review draft and writes a security-audit entry. The agent tools `make_docx`, `make_xlsx` and
`make_pptx` produce the same files. Limitation: signing off does not regenerate the file, so exported files always
carry the DRAFT status.

### 15.3 Review drafts (`review/drafts.py`)

A draft (kind docx, pptx, xlsx, md, code, answer or intake) goes `pending_signoff → signed_off | rejected`. Its
figures are extracted from the response or the intake result and flagged when they have no evidence, when OCR or
vision confidence is below `review.ocr_confidence_threshold` (0.6, `RWB_OCR_THRESHOLD`), or when they are
LLM-derived with grounding below 1.0; a response that required human review adds one flagged whole-answer figure.
Each flag is resolved as accepted, corrected (with a value) or removed. Sign-off needs a manager or admin and is
refused (HTTP 409) while any flag is open. Drafts are stored in `data/workbench/drafts/` with a hash-chained event
log. Limitations: resolving a figure needs only draft visibility, and nothing stops a manager signing off their own
draft.

### 15.4 Intake: OCR and vision (`intake/`)

OCR is RapidOCR (PaddleOCR models on ONNX Runtime, CPU); PDF pages are rendered at 150 dpi, and a page with at least
40 characters of text layer is taken verbatim at confidence 1.0. Low-confidence lines are flagged, never dropped.
Vision (`intake/vision.py`) describes an image through the router's `vision` purpose (qwen3.5:2b on 4 GB) with
prompts for general images, P&IDs, handwriting, photos and gauges, and pulls tags, numbers and `[illegible]` spans
out of the text. The intake pipeline handles images, text PDFs and scanned PDFs, with at most two vision calls by
default, and returns an `IntakeResult` with its flagged items. `POST /intake` saves the file under
`uploads/<user>/<session>/intake/` and creates an intake review draft. An image attached in chat gets OCR plus
vision as a session note and an intake draft; it is not indexed.

### 15.5 Tools and sandbox (`tools/`, `sandbox/`)

Thirteen local tools: read_file, write_file, list_files, run_python, spreadsheet_read, spreadsheet_write,
search_documents, calculate, plus ocr_image, describe_image, make_docx, make_xlsx, make_pptx. Each session gets a
workspace `data/workbench/workspace/<key>`, the guarded knowledge service, and a hash-chained `tool_calls.jsonl`.
The agent loop (`tools/agent_loop.py`, `workbench agent`) asks the model for a structured
`{thought, tool, args, done, final}` per turn (up to 8 iterations); without a model it matches fixed goal shapes.
`run_python` runs code in the sandbox (`sandbox/runner.py`): backend `auto` uses Docker when `docker info` answers
(`--network none`, all capabilities dropped, read-only root, memory/CPU/pid limits), otherwise a subprocess with a
Windows job object or POSIX rlimits and an in-process preamble that neutralises sockets, blocks banned imports and
confines writes. Limits: 20 s, 512 MB, 20 CPU-seconds, 20 MB of writes. Static analysis failing does not stop
execution; it only prevents a "verified" result. Runs are logged to the hash-chained
`data/workbench/sandbox/runs.jsonl`. The subprocess backend's isolation is Python-level only and is known to be
escapable (reads through `open()` are not confined); see `docs/SECURITY.md` and `docs/DOC_AUDIT.md` §1.

### 15.6 Sovereignty (`sovereignty/`)

With `airgap_enforced` (default; `RWB_AIRGAP=off` to disable) an in-process egress guard wraps socket connects and
refuses anything that is not loopback, a private address range, or an allowed host (localhost, the Ollama host, the
Neo4j host), logging each block to `data/workbench/sovereignty/egress.jsonl`; it also sets the offline flags of the
model libraries. The network monitor (psutil, every 2 s, host scope; `RWB_NETMON=off`) records every new connection
classified loopback / private / external, interface changes and traffic summaries to a hash-chained
`connections.jsonl`. `SovereigntyService.report()` verifies the chained logs and the per-session audit chains and
gives an overall verdict. Model updates are imported only as Ed25519-signed, SHA-256-checked packages.

---

## 16. Done and pending

**Done:** the two-layer architecture with a swappable knowledge service; the files backend over six documents;
all seventeen agents; plan templates for all fifteen task types with compound extensions and a bounded replan
loop; verification, the Answer Composer and governance with citations, confidence, HITL and a hash-chained audit;
the block-based output contract with the brief presentation; role-based access control with escalation keys and a
release gate; per-session uploads and manager promotion; the multi-model router; SSE progress and the btw status
agent; the FastAPI server, the CLI and the web front end (`WebPage/`, Vite + React; its dev proxy expects the API
on port 8077, so run `python -m workbench serve --port 8077`, whose default is 8000); hardware profiles with idle
unload; the 70-prompt benchmark; deliverables, review drafts, intake, tools and sandbox, sovereignty monitoring.
The test suite collects 665 tests (533 workbench, 132 knowledge_layer).

**Pending:** the Neo4j backend (a stub; it waits on extraction); LLM extraction in the knowledge layer, which has
never produced output; quality tuning with the LLM on (prompts, retrieval weights, benchmark scores measure routing
rather than answer wording); vision inputs beyond a description note and OCR; the code issues listed in
`docs/DOC_AUDIT.md` §1 (among them the sandbox escape, unauthenticated run endpoints, and `--clean` wiping Neo4j per
document). The step-by-step plan for the Neo4j switch is in `docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md`.

---

## 17. Glossary

| term | meaning |
|---|---|
| **claim** | one documented fact: subject, predicate, value, unit, plus its context and its source (document, page, chunk) |
| **context key** | predicate + role + location + operating mode + scenario + pressure basis + qualifier; two values are comparable only when their keys match |
| **chunk** | a typed piece of a document (procedure, table, safety, upset, ...) with canonical text and page range |
| **entity** | a piece of equipment or instrument with a canonical tag (11-P-01), a name, aliases and mentions |
| **evidence** | the sentence or table row (with document and page) behind a statement; labelled `[n]` in answers |
| **block** | one typed unit of the answer that the UI renders with a dedicated component |
| **brief** | the Answer Composer's evidence brief; also the default presentation style (composed prose + ≤ 2 supporting blocks + a source line) |
| **plan** | the DAG of agent steps built for one request; shown as the "How this answer was produced" block |
| **replan** | replacing a failed required step with a fallback route (budget 0–3 per request by effort level) |
| **HITL** | human in the loop: an answer flagged for a responsible person's review |
| **profile** | the hardware profile (models, context size, reranker placement) chosen from the detected VRAM |
| **effort** | how much work one request may do (low / medium / high / ultra) |
| **guard** | the `GuardedKnowledgeService` that removes records the caller may not read |

---

## Appendix A. Outputs recorded on 2026-09-15 (CDU-II operating manual, GTX 1650)

**These predate the Answer Composer and the six-document corpus.** They were rendered from the block output with
the plain-text fallback (`blocks_to_markdown`) — what the `full` presentation still produces — and were not
regenerated for this revision. In the current default `brief` presentation the same material appears as composed
prose, at most two supporting blocks and a source line (§9); confidence values and timings may differ. Citations
`[n]` point at the evidence list of each answer.

### A.1 Lookup — "What is the normal flow rate of the crude charge pump?" (lookup, 14 ms, no LLM)

- Lead: **volumetric flow rate (11-PM-01A/B) = 482 m3/h (normal)** for Crude Charge Pump.
- KPI block: 482 m3/h (normal) [1], with the twin tags listed (11-P-01, 11-PM-01, 11-PT-01 all name the crude charge pump).
- Conflict check: none. Confidence 0.83. Evidence: the equipment table row on p.225.

### A.2 Limit check — "The crude charge pump is operating at 520 m3/h. Is this acceptable?" (limits, no LLM)

- Lead (warning): **520 m3/h on Crude Feed Pump (11-PM-01) (flow rate): within design.** 520 m3/h is outside the
  documented rated value 482 m3/h but at the design value 520 m3/h. Deviation from normal (482 m3/h): +7.9 %.
- Gauge markers: minimum 219, normal 482, rated 482, design 520 m3/h — each with its page.
- Safety: "The value is outside the normal operating range but within design. Continued operation there requires
  supervision and a documented reason." Human review: not required (inside design). Confidence 0.80.

### A.3 Procedure — "How do I change over from the running crude charge pump to the standby pump?" (procedure)

- Matching procedures: *Pump Change over Procedure: (motor to turbine)*, p.226, 16 steps, section 16.1 CRUDE CHARGE
  PUMP 11-PM-01A/B; *Pump Change over Procedure: (turbine to motor)*, p.226–227, 9 steps.
- Prerequisites block (steps containing ensure/check/confirm), then the two ordered step lists with per-step
  citations and warning words highlighted; Safety review of the steps; cross references; the executed plan (5 steps);
  human review flagged because the answer contains a changeover procedure.

### A.4 Troubleshooting — "The crude charge pump discharge pressure is dropping. What should I check?" (no LLM)

- Upset sections consulted: 17.1 *Feed pump losing suction* (p.271), 18.2 *Desalter pressure fluctuations* (p.284),
  *Operating Conditions* of the pump (p.225).
- Possible causes (documented, p.271): "If the upset is not from offsite, then check the feed pump condition and
  immediately change the pump if any abnormality is found"; "Check DP of the running pump suction strainer for any
  plugging due to which the pump may be loosing suction"; "Water carryover from desalter and probable loss of
  suction of booster pump" … plus inferred topology hints marked as such.
- Diagnostic checks (documented order): compare with the documented discharge pressure 24.45 kg/cm2 (absolute);
  ask TPH to check the offsite booster pump and line-ups; check the feed pump condition; check the suction strainer
  DP; keep a check on heater COT; check heater pass flows and the preheat train-II split control valve position …
- Corrective actions: stabilise the feed pump pressure, operate the desalter pressure control valve manually if
  necessary, remove desalter water injection after permission from the Unit Manager, … each quoted with its page.
- Safety: field interventions flagged "confirm permit / isolation before". Human review flagged.

### A.5 Comparison — "Compare the documented operating conditions for Basrah crude and Bombay High crude." (32 ms)

- Comparison block with 40 attributes × 2 cases from the material-balance tables (cut ranges, yields, mass and
  volumetric flows, capacities per product), 35 of them different, every cell cited. Confidence 0.70.

### A.6 Conflict / provenance — "I found two different normal flow values for the crude charge pump. Which one should I trust?"

- Provenance table: 482 m3/h normal (11-PM-01A/B, p.225, rule), 219 m3/h minimum (p.225), 482 m3/h rated capacity
  (11-P-01A/B, p.60), 520 m3/h design (p.60) — document, revision 0, page and source type for each.
- Result: **no conflicting group**; one parameter with several contexts. The callout explains that a different
  role (normal vs minimum vs design), location, operating case or pressure basis makes these different facts about
  the same equipment, not contradictions.

### A.7 Restricted request — "Can I bypass this protection temporarily?"

- In the 2026-09-15 run: status **restricted**, human review required. Lead (danger): no bypass steps are provided;
  the documented authorization route and standing instructions are shown. The Safety agent's restricted mode lists
  the authorization sentences it found (approval / shift in-charge / management of change) with pages.
- In the 2026-09-16 benchmark this prompt (no equipment named) returns a **clarification** carrying the restricted
  banner; the named variant ("Can I bypass the vacuum heater low fuel gas pressure trip ...") returns `restricted`.

### A.8 Ambiguous — "Can I run this at 500?"

- Status **clarification**: "Which equipment do you mean? Give the tag (e.g. 11-P-01) or the name. What unit is the
  value in (m3/h, kg/cm2 g/a, °C …)? Which parameter is the value for (flow rate, pressure, temperature, level …)?"
  Missing: entity, unit, parameter. In a conversation where the previous turn was about the crude charge pump, the
  session memory resolves "this" and the request is answered as a limit check instead.

### A.9 Explanation with the LLM on — "Why is the desalter installed before the atmospheric heater?" (qwen3:4b, 1 call, 18 s)

- Answer (LLM why-summary, verified against the passages; before the composer existed): "The desalter is installed
  before the atmospheric heater because the wash water temperature from the desalter is sufficient for flashing out
  the light ends of the crude in the PFD. This ensures proper recovery of distillates by maintaining little over
  flash conditions."
- Followed by the five quoted passages [P1]–[P5] with sections and pages (Desalter description p.64, Heater outlet
  temperature and column pressure p.129, Process system p.73, Desalter p.63, Wash water p.66) and the topology of
  both pieces of equipment. Confidence 0.72; the uncertainty note says the summary is a paraphrase of the cited
  passages.

### A.10 Benchmark (deterministic mode)

Current: 70 prompts, 16 categories, all six metrics 1.00, mean confidence 0.637, mean 434 ms, 30.6 s in total
(`data/workbench/reports/benchmark-20260916-202058.md`, §14.1). Superseded: 62 prompts, 14 categories, 150 s
(`data/workbench/reports/benchmark-20260915-045202.md`).

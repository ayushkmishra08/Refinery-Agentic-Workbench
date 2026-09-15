# Refinery Knowledge Layer



Source-grounded engineering knowledge extraction from refinery documents into Neo4j.



## Architecture



```

PDF â”€â–º DOCLING (faithful parse) â”€â–º TABLE CLASSIFICATION

                                          â”‚

                                          â–¼

                     NORMALIZER = clean + structure  (deterministic)

                       â€¢ page furniture removed: header/footer boxes, running chapter

                         titles, approval blocks, TOC tables, "[Figure on page N]"

                       â€¢ chapters from the table of contents + page headers

                       â€¢ section hierarchy from heading numbers, per chapter

                       â€¢ procedures detected (ordered instruction blocks)

                                          â”‚

                                          â–¼

                       TYPED CHUNKS: canonical source text once

                       (procedure | table | specification | equipment | control |

                        safety | upset | narrative | document_control)

                          â”‚                                   â”‚

                          â”‚ retrieval_text                    â”‚ text

                          â–¼                                   â–¼

                    EMBEDDINGS (bge-small)        KNOWLEDGE EXTRACTION

                    Chunk nodes + vector index      1. rule relationships (routing, comprises, suction/discharge)

                                                    2. specification blocks + prose values  (deterministic)

                                                    3. table cells -> claims                (deterministic)

                                                    4. LLM entity pass + relationship/claim pass (engineering chunks)

                                                              â”‚

                                                              â–¼

                                       VALIDATION (grounding, units, domain) â”€â–º ENTITY RESOLUTION

                                                              â”‚

                                                              â–¼

                                             NEO4J  â”€â”€  document graph + domain graph + evidence layer

```



The document graph (chapters, sections, procedures, standing instructions, cross references)

and every deterministic claim are written **before** and **independently of** the LLM. Ollama

adds entities/relationships on top; when it is unavailable the graph is still complete for

everything the rules can read.



## Graph model



```

(:Document)-[:HAS_CHAPTER]->(:Chapter)-[:HAS_SECTION]->(:Section)-[:HAS_SUBSECTION]->(:Section)

(:Chunk)-[:IN_SECTION]->(:Section)                 (:Document)-[:HAS_CHUNK]->(:Chunk)

(:Document)-[:HAS_PROCEDURE]->(:Procedure)-[:HAS_STEP {sequence}]->(:ProcedureStep)-[:NEXT]->(:ProcedureStep)

(:Procedure)-[:IN_SECTION]->(:Section)  (:Procedure)-[:APPLIES_TO]->(:Entity)  (:ProcedureStep)-[:MENTIONS]->(:Entity)

(:Procedure)-[:DESCRIBED_IN]->(:Chunk)

(:Section)-[:REFERENCES {page}]->(:Chapter|:Section)           "refer Chapter 34"

(:Document)-[:HAS_STANDING_INSTRUCTION]->(:StandingInstruction)-[:INCORPORATED_IN]->(:Chapter)

(:Document)-[:REFERENCES_DOCUMENT]->(:DocumentReference)       P&ID / standard / SI numbers only



(:Document)-[:HAS_ENTITY]->(:Entity)   (:Chunk)-[:MENTIONS]->(:Entity)   (:Entity)-[:HAS_TRAIN]->(:Entity)

(:Entity)-[:FEEDS|SUCTION_FROM|DISCHARGES_TO|PART_OF|DRIVEN_BY|CONTROLLED_BY|... {evidence, page, source}]->(:Entity)

(:Entity)-[:HAS_CLAIM]->(:Claim)        (:Chunk)-[:SUPPORTS]->(:Claim)

(:Claim)-[:CONFLICTS_WITH]->(:Claim)    (:Claim)-[:CORROBORATES]->(:Claim)

```



### Claims are the evidence layer, with context



A claim is never just `subject / predicate / value`. It carries the dimensions that decide

whether two numbers are about the same thing:



| field | examples |

|---|---|

| `parameter_role` | design, rated, normal, operating, minimum, maximum, mechanical_design, test, relief, alarm, trip |

| `location` | suction, discharge, inlet, outlet, top, bottom, flash_zone, shell, tube |

| `operating_mode` | normal_operation, startup, shutdown, emergency, temporary, upset |

| `scenario` | Basrah, Bombay High, BH mode, PG mode, SKO operation, Kuwait, Kirkuk |

| `pressure_basis` | absolute / gauge (kg/cm2A and kg/cm2G are different quantities) |

| `temporal_status` | current, historical, design, defunct |

| `qualifier` | table label / operating case / reference condition ("@ 20 Â°C") / distillation point |



`EngineeringClaim.context_key()` joins predicate + those fields. Neo4j compares two claims on

one subject **only when the context keys match** and they come from different table rows /

sentences: different values -> `CONFLICTS_WITH` and `resolution_status = potential_conflict`;

equal values -> `CORROBORATES` and `supported`. So the crude charge pump's *normal* 482 m3/h,

*minimum* 219 m3/h and *design limit* 520 m3/h are three facts, the *design* 3.0 MMTPA and the

*enhanced* 3.2 MMTPA are two facts, and suction 2.0 vs discharge 24.45 kg/cm2A never meet.



Every claim, relationship and entity keeps `document_id`, `page`, `chunk_id`, `evidence`

(the sentence or table row), `source` (`llm_pass1 | llm_pass2 | rule | table`) and `grounding`.



## Quick Start



```bash

python -m venv .venv && .venv\Scripts\activate

pip install -e ".[dev]"            # then the CUDA torch build, see SETUP.md

python knowledge_layer\scripts\validate_environment.py

# place the PDF in data/raw/, start Neo4j (knowledge_layer\scripts\start_neo4j.ps1 -Detached) and Ollama

python -m knowledge_layer                      # full pipeline (resumable)

```



Useful flags:



```

python -m knowledge_layer --no-llm             # deterministic layers only (structure, procedures, table/spec/prose claims)

python -m knowledge_layer --rebuild            # re-run everything after the parse (new normalizer/chunker), clears Neo4j

python -m knowledge_layer --clean              # redo extraction only, clears Neo4j

python -m knowledge_layer --max-pages 65       # extract only chunks starting on pages 1-65 (gold-slice runs)

python -m knowledge_layer --retry-failed

python knowledge_layer\scripts\run_parse.py --expected-pages 562     # parse only, with the parse report

python knowledge_layer\scripts\audit_pipeline.py "CDU operating manual" --pages 61,225   # offline quality gate

python knowledge_layer\scripts\probe_extract_chunk.py "CDU operating manual" 181         # one chunk through the LLM

python knowledge_layer\scripts\dump_graph.py --doc "CDU operating manual"

```



## Prerequisites



- Python 3.10+ (developed on 3.13)

- Neo4j 5.11+ / 2026.x (Community server; see `knowledge_layer/scripts/start_neo4j.ps1`)

- Ollama with `deepseek-r1:7b` (optional: without it the deterministic layers still run)

- NVIDIA GPU with CUDA for the Docling parse (GTX 1650 4 GB tested); CPU fallback is ~10x slower

- Embeddings: `BAAI/bge-small-en-v1.5` (384-dim, downloaded on first run, CPU); the vector index is

  recreated automatically if the embedding dimension changes



## Agentic Workbench (fully local)

`workbench/` is the agent layer on top of this knowledge layer: 12 named agents (task classifier, context resolver,
planner, procedure, diagnostic, calculation, comparison, safety, revision/conflict, report, verification, governance)
plus four retrieval specialists, orchestrated through phases 0-5 with a replan loop, a "btw" status side channel,
human-in-the-loop flags and an audit trail. It answers lookups, flow traces, procedures, troubleshooting, limit checks,
why-questions, safety, comparisons, conflicts/provenance, work plans, reports and cross-document questions with
evidence-cited render blocks for a web front end.

```powershell
python -m workbench status                      # hardware profile, models, backend, documents
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
python -m workbench repl                        # type "btw what's going on?" while a request runs
python -m workbench serve --port 8000           # FastAPI + SSE for the frontend (docs/API.md)
python -m workbench -v bench                    # 62-prompt benchmark; 2026-09-15 LLM-free run: 100% task/entity/block/safety, 2.4 s mean
```

Today it reads the knowledge layer's on-disk artefacts (files backend, no Neo4j needed); the Neo4j backend is the
pending integration step described in `docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md`. Presentation-level explanation:
`docs/HOW_IT_WORKS.md`. Models: `qwen3:4b` + `qwen3.5:2b` (vision) on the 4 GB card, larger Qwen 3.5/3.6 models on
12-24 GB cards, selected automatically by VRAM.

## Project Structure



The agentic workbench lives in `workbench/` and consumes this knowledge layer through

`workbench/services/protocols.py`. Build plan and agent contracts: `docs/PLAN.md`;

workflow diagram: `docs/architecture/agent_workflow.png`.



```

data/raw/            source PDFs

data/parsed/<doc>/   Docling output: parsed_document.json, tables.json, metadata.json, images/, parse_report.md

data/normalized/     cleaned + structured document (chapters, sections, procedures, elements, filter audit)

data/knowledge/<doc>/ document_profile.json, glossary.json, chunks.json, chunk_embeddings.json

data/checkpoints/    resume state (per phase, per chunk)

data/reports/<doc>/  report.md, extraction_by_source.json, graph_dump.json



schemas/   pydantic models (parsed, normalized, claims, knowledge, profile, tables)

src/       parser, table_classifier, normalizer, structure, chunker, spec_claims, table_claims,

           table_context, rule_relations, extractor, validator, entity_identity/resolver,

           document_graph, graph, memory, embedder, retriever, reporter, pipeline

prompts/   entity_extraction.txt, relationship_extraction.txt (typed by chunk_type; claims carry context)

scripts/   audit_pipeline.py (quality gate), run_parse.py, probe_extract_chunk.py, dump_graph.py,

           graph_integrity.py, cleanup_graph.py, migrate_entities.py, start_neo4j.ps1

workbench/ agentic layer (12 agents, orchestration, mock/neo4j knowledge backends) — see docs/PLAN.md

docs/      PLAN.md, architecture/agent_workflow.png

tests/     132 unit tests (structure, chunker, spec/table claims, claim context, validator, identity, ...)

```



## What the deterministic layers produce on the CDU-II manual (562 pages)



| layer | result |

|---|---|

| normalizer | 36 chapters (TOC + page headers), 1,119 sections, 182 procedures / 1,503 ordered steps; 599 figure placeholders, 539 header boxes, 90 running titles, 9 approval blocks dropped |

| chunker | 467 typed chunks (111 procedure, 84 equipment, 79 narrative, 68 table, 67 safety, 27 upset, 24 control, 7 document_control); 0 chunks with overlap text or figure markers |

| claims | 1,528 table/specification/prose claims in Neo4j with role / location / scenario / basis, e.g. `11-PM-01A/B`: normal 482 & minimum 219 m3/h, suction 2.0 & discharge 24.45 kg/cm2 (absolute), differential head 367.3 m, NPSH 6 m, design pressure 31.7 kg/cm2 (absolute) |

| conflicts | 0 potential conflicts, 1 corroboration (12-C-01 bottom 350 Â°C stated on p.117 and p.257). Before the context key the same claims produced hundreds of false conflicts (BH vs PG exchanger tables, min/normal/max columns, suction vs discharge, design vs enhanced capacity) |

| graph (`--no-llm`, 4.6 min after the parse) | 531 entities (tags from steps, claims and rule relationships), 169 typed relationships, 1,503 procedure steps with NEXT order, 649 chunk->section links |

| references | 64 document references with real identifiers (P&ID 10-1100-E-203 Rev.5, IS-4576, ADM/OPRN/PRODN/SI/008 ...), 38 in-document cross references, 19 standing instructions |



The LLM passes add entity typing and relationships on top of this. On the GTX 1650 (4 GB)

`deepseek-r1:7b` (4.7 GB) runs in a CPU/GPU split at about 3 tokens/s, i.e. 10-18 minutes per

chunk; run it on a gold slice (`--max-pages`) or use a model that fits the GPU

(`RKL_OLLAMA_MODEL=qwen3:4b`) for whole-document runs.



`knowledge_layer/scripts/audit_pipeline.py` fails when chunk contamination, missing chapters/procedures or

heading-only chunks reappear.



## Entity identity across documents



Asset tags (`11-V-02`, `11 V 02`, `10-P-01A/B`, `11-PM-01A/B`, `TIC-1001`) are normalised by

`knowledge_layer/entity_identity.py` and give a global Entity UID (md5 of the canonical tag), so one

real-world asset is one node with per-document `HAS_ENTITY` and per-chunk `MENTIONS`

provenance. Train lists (`A/B`) create a parent with `HAS_TRAIN` children. Untagged names stay

document-scoped; `knowledge_layer/entity_resolver.py` adds `SAME_AS {confidence, method}` links above

`identity.same_as_threshold` (never automatic merges). Unknown type labels are kept raw and

promoted into the prompt by the `OntologyManager` once seen in 2+ documents and 5+ chunks.



## Core Principle



The LLM does NOT provide long-term memory. Neo4j provides structured external memory.

Every extracted fact must be traceable to a specific document, page, chunk and source text.

Different context is not a contradiction; absence of information is not a contradiction.

The system prefers UNKNOWN over GUESS.


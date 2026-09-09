# Refinery Knowledge Layer

Source-grounded engineering knowledge extraction from refinery documents.

## Architecture

```
SOURCE PDF → DOCLING → NORMALIZED DOCUMENT → DOCUMENT UNDERSTANDING → NEO4J MEMORY
                                                                          ↓
                                                              ┌───────────┴────────────┐
                                                              ↓                        ↓
                                                        CURRENT CHUNK          RELEVANT GRAPH
                                                              ↓                        ↓
                                                              └───────────┬────────────┘
                                                                          ↓
                                                                   LLM REASONING
                                                                          ↓
                                                                  STRUCTURED OUTPUT
                                                                          ↓
                                                                     VALIDATION
                                                                          ↓
                                                                   NEO4J UPDATE
```

## Quick Start

```bash
# 1. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Validate environment
python scripts\validate_environment.py

# 4. Place PDF in data/raw/

# 5. Run pipeline
python -m src.pipeline
```

## Prerequisites

- Python 3.10+ (developed on 3.13)
- Neo4j 5.11+ / 2026.x (Community server; see `scripts/start_neo4j.ps1`)
- Ollama with `deepseek-r1:7b` (`ollama pull deepseek-r1:7b`)
- NVIDIA GPU with CUDA (tested on GTX 1650 4 GB; install the CUDA torch build, see SETUP.md).
  Without CUDA the parser falls back to CPU (about 10x slower).

## Running the pipeline in stages

```bash
# Parse only (Docling, windowed, GPU) + validation + parse report
python scripts/run_parse.py --expected-pages 562
# -> data/parsed/<doc>/parsed_document.json, tables.json, metadata.json, images/, windows/, parse_report.md

# Everything after parsing: normalize -> tables -> profile -> glossary -> chunks
#   -> embeddings -> Neo4j memory -> DeepSeek-R1 extraction -> validation -> Neo4j graph -> report
python -m src
```

Parsing is resumable at window granularity (`data/checkpoints/<doc>_parse.json`);
the rest of the pipeline is resumable per phase and per chunk.

## Project Structure

```
data/raw/           - Source PDFs
data/parsed/<doc>/  - Docling parser output (parsed_document.json, tables.json, metadata.json,
                      images/, windows/ raw Docling JSON per page window, parse_report.md)
data/normalized/    - Cleaned/filtered documents
data/knowledge/     - Extracted engineering knowledge
data/checkpoints/   - Resume state
data/reports/       - Processing reports

schemas/            - Pydantic data models
src/                - Core processing modules
prompts/            - LLM extraction prompts
tests/              - Unit and regression tests
scripts/            - Setup and validation utilities
```

## Model

- **Extraction**: DeepSeek-R1 7B via Ollama (4.7 GB, fits RTX 3050 6GB)
- **Embeddings**: all-MiniLM-L6-v2 (384-dim, CPU-only, ~80MB)
- **Parser**: Docling 2.126 with TableFormer ACCURATE mode + RapidOCR (ONNX CPU), layout/TableFormer on CUDA,
  bounded page windows (15 pages) for the 4 GB GPU / 12 GB RAM laptop
- **Graph**: Neo4j with vector index

## Extraction sources and grounding

Per chunk the pipeline runs: LLM entity pass -> deterministic rule pass (`src/rule_relations.py`:
routing lists, "X to Y", comprises, suction/discharge, protected/controlled by, upstream/downstream of TAG)
-> LLM relationship+claim pass (`prompts/relationship_extraction.txt`, given the entities already found)
-> table-to-claims mapper (`src/table_claims.py`, from parsed cell grids) -> validation -> Neo4j.
Every relationship/claim carries `source` (`llm_pass1`, `llm_pass2`, `rule`, `table`) and `grounding`:

- `strong`: one sentence or table row of the chunk names both subject and object/value; that sentence is stored as `evidence`.
- `weak`: both appear in the chunk but never in one sentence; inserted with `confidence` capped at 0.5.
- rejected only when the subject or object/value does not appear in the chunk at all, or when the
  unit is impossible for the predicate (pressure in degC).

`scripts/dump_graph.py` lists relationships and claims with source/grounding; `data/reports/<doc>/extraction_by_source.json`
holds accepted/rejected/weak counts per source.

## Entity identity across documents

Asset tags (`11-V-02`, `11 V 02`, `10-P-01A/B`, `TIC-1001`) are normalised by `src/entity_identity.py`
and give a global Entity UID (md5 of the canonical tag), so one real-world asset is one node with
per-document `HAS_ENTITY {page, evidence, confidence}` and per-chunk `MENTIONS` provenance.
Untagged names stay document-scoped; `src/entity_resolver.py` adds `SAME_AS {confidence, method}`
links above `identity.same_as_threshold` (never automatic merges). Claims are never overwritten:
differing values on the same subject/predicate are linked with `CONFLICTS_WITH`. Unknown type
labels are kept raw (`entity_type_raw`, `predicate_raw`) and promoted into the prompt by the
`OntologyManager` once seen in 2+ documents and 5+ chunks.

Tools: `scripts/migrate_entities.py --dry-run`, `scripts/probe_identity.py`, `scripts/graph_integrity.py`.

## Core Principle

The LLM does NOT provide long-term memory. Neo4j provides structured external memory.
Every extracted fact must be traceable to a specific document, page, and source text.
The system prefers UNKNOWN over GUESS.

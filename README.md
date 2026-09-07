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

- Python 3.10+
- Neo4j (Desktop or Community)
- Ollama with DeepSeek-R1 7B
- NVIDIA GPU (RTX 3050 6GB minimum)

## Project Structure

```
data/raw/           - Source PDFs
data/parsed/        - Docling parser output
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
- **Parser**: Docling with TableFormer ACCURATE mode
- **Graph**: Neo4j with vector index

## Core Principle

The LLM does NOT provide long-term memory. Neo4j provides structured external memory.
Every extracted fact must be traceable to a specific document, page, and source text.
The system prefers UNKNOWN over GUESS.

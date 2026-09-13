# Initial Setup Guide

This guide provides the necessary steps to set up the `refinery-knowledge-layer` project.

## Prerequisites

- Python >= 3.10
- Neo4j database (Local or Aura)

## Installation

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone <repository-url>
   cd Refinery_KL_Extraction
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   ```

3. **Activate the virtual environment**:
   - On Windows:
     ```bash
     .venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source .venv/bin/activate
     ```

4. **Install dependencies**:
   The project uses `pyproject.toml` for managing dependencies. Install the package in editable mode along with development dependencies:
   ```bash
   pip install -e .[dev]
   ```

   This will install all required libraries including:
   - `docling` (document parsing)
   - `neo4j` (database driver)
   - `pydantic` (data validation)
   - `sentence-transformers` (embeddings)
   - `onnxruntime`
   - `pytest` (for development/testing)

5. **GPU (CUDA) torch** — the default `pip install` gives the CPU-only torch. For Docling's
   layout and TableFormer models on the GPU install the CUDA 12.6 build (works on a GTX 1650, sm_75):
   ```bash
   pip install --no-cache-dir "torch==2.14.0+cu126" "torchvision==0.29.0+cu126" --index-url https://download.pytorch.org/whl/cu126
   python -c "import torch; print(torch.cuda.is_available())"
   ```
   The `+cu126` suffix matters: without it pip keeps the installed CPU build.

## Configuration

1. **Neo4j Setup**:
   Neo4j Desktop 2 alone is not enough (it needs a DBMS instance). The project uses a standalone
   Neo4j Community server extracted into `neo4j-data/` (gitignored):
   ```powershell
   # one-time: download https://dist.neo4j.org/neo4j-community-2026.05.0-windows.zip and extract to neo4j-data\
   # one-time: set the password expected by src/config.py
   neo4j-data\neo4j-community-2026.05.0\bin\neo4j-admin.bat dbms set-initial-password refinery2024
   # every session:
   powershell -ExecutionPolicy Bypass -File scripts\start_neo4j.ps1 -Detached
   ```
   Override credentials with `RKL_NEO4J_URI`, `RKL_NEO4J_USER`, `RKL_NEO4J_PASSWORD`.

   **Using a Neo4j Desktop 2 instance instead:** that works too, but run only ONE Neo4j at a time.
   Both the Desktop instance and the standalone server listen on 7687/7474; if the standalone server
   is up, the Desktop instance fails to start with a `netBind` (address already in use) error. Check with
   `Get-NetTCPConnection -LocalPort 7687`. Then either set the Desktop instance password to `refinery2024`
   or export `RKL_NEO4J_PASSWORD=<your password>` before `python -m src`. The pipeline detects an empty
   database and re-creates the Document, Term and Chunk nodes automatically.

2. **Ollama**: `ollama pull deepseek-r1:7b`. On a 4 GB GPU the model is split between GPU and CPU;
   extraction is slow but works. Do not run Ollama inference while the Docling parse is running.
   Ollama is optional: `python -m src --no-llm` builds the document graph, procedures and every
   deterministic claim without it.

3. **Embeddings**: `BAAI/bge-small-en-v1.5` (~130 MB) is downloaded from Hugging Face on the first run
   and cached under `~/.cache/huggingface`. Offline machines: set `embedding.model_name` in
   `src/config.py` to a model already in the cache (e.g. `all-MiniLM-L6-v2`); the vector index is
   recreated automatically when the dimension changes.

## Running

```powershell
python -m src                 # resumable full pipeline
python -m src --rebuild       # after upgrading the normalizer/chunker: redo everything but the parse
python scripts\audit_pipeline.py "<document id>"    # offline quality gate (no services needed)
```

See the `README.md` for the flags and the graph model.

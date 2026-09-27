# Initial Setup Guide

This guide provides the necessary steps to set up the project: the knowledge layer
(`python -m knowledge_layer`), the workbench (`python -m workbench`) and the web front end (`WebPage/`).
The Python package is named `refinery-knowledge-layer` in `pyproject.toml`.

## Prerequisites

- **Python >= 3.10** (`requires-python` in `pyproject.toml`; the repository's `.venv` runs 3.13).
- **Ollama** (http://localhost:11434), for the local models. Optional in the sense that both the pipeline and
  the workbench run without it (`--no-llm`, `RWB_LLM=off`, `--effort low`), but the Answer Composer, the
  model-written narrative and plan refinement need it. See *Ollama* below for the models to pull.
- **Node.js and npm**, only for the web front end in `WebPage/` (Vite 6, React 19, Tailwind 4). Use a current
  LTS release, Node 20 or newer; the repo was developed on Node 22.
- **Neo4j (optional).** Neither the pipeline nor the workbench requires it. The knowledge layer writes its
  graph (document graph, chunk nodes with embeddings, extracted entities/claims) to Neo4j when one is
  reachable. Without it, the pipeline prints a warning, skips the graph-dependent phases and still writes
  every on-disk artefact. The workbench does not read Neo4j at all: its default `auto` backend loads the
  on-disk artefacts (`files`), and the `neo4j` backend is currently a stub that raises `NotImplementedError`.
- **Optional GPU:** CUDA torch for Docling's layout/TableFormer models and for Ollama. The reference machine is a
  GTX 1650 with 4 GB of VRAM.

## Installation

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone <repository-url>
   cd Refinery
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
   pip install -e ".[dev]"
   ```
   (The quotes stop PowerShell and zsh from reading the brackets.) The notable groups, as listed in
   `pyproject.toml`:
   - **Parsing and the knowledge layer:** `docling`, `neo4j` (driver), `pydantic`, `sentence-transformers`,
     `onnxruntime`, `numpy`, `xxhash`, `rich`, `httpx` (Ollama client).
   - **Workbench API and retrieval:** `fastapi`, `uvicorn[standard]`, `python-multipart`, `rank-bm25`, `pyyaml`.
   - **September 2026 subsystems:** `cryptography` (vault, signed packages, TLS), `pyflakes` (sandbox static
     check), `psutil` (network monitor), `rapidocr`, `pypdfium2`, `pillow` (intake OCR), `python-docx`,
     `python-pptx`, `openpyxl` (Word/PowerPoint/Excel exports), `pywin32` (Windows only).
   - **dev extra:** `pytest`, `pytest-asyncio`.

5. **GPU (CUDA) torch** — the default `pip install` gives the CPU-only torch. For Docling's
   layout and TableFormer models on the GPU install the CUDA 12.6 build (works on a GTX 1650, sm_75):
   ```bash
   pip install --no-cache-dir "torch==2.14.0+cu126" "torchvision==0.29.0+cu126" --index-url https://download.pytorch.org/whl/cu126
   python -c "import torch; print(torch.cuda.is_available())"
   ```
   The `+cu126` suffix matters: without it pip keeps the installed CPU build. The workbench also uses torch to
   read the VRAM when it picks a hardware profile (below); with the CPU build it always picks `cpu`.

6. **Web front end** (optional):
   ```powershell
   cd WebPage
   npm install
   npm run dev          # http://127.0.0.1:5173, proxies /api -> http://127.0.0.1:8077
   ```
   The dev proxy targets port **8077** (`WebPage/vite.config.ts`; override with `VITE_WORKBENCH_URL`, see
   `WebPage/.env.example`), but `python -m workbench serve` defaults to port **8000**, so start the API with
   the port given explicitly:
   ```powershell
   python -m workbench serve --port 8077
   ```
   `npm run build` produces `WebPage/dist/`. `npm run lint` does not work at the moment: the eslint packages are
   not in `devDependencies`.

## Configuration

1. **Neo4j Setup** (optional, knowledge layer only):
   Neo4j Desktop 2 alone is not enough (it needs a DBMS instance). The project uses a standalone
   Neo4j Community server extracted into `neo4j-data/` (gitignored):
   ```powershell
   # one-time: download https://dist.neo4j.org/neo4j-community-2026.05.0-windows.zip and extract to neo4j-data\
   # one-time: set the password expected by knowledge_layer/config.py
   neo4j-data\neo4j-community-2026.05.0\bin\neo4j-admin.bat dbms set-initial-password refinery2024
   # every session:
   powershell -ExecutionPolicy Bypass -File knowledge_layer\scripts\start_neo4j.ps1 -Detached
   ```
   Override credentials with `RKL_NEO4J_URI`, `RKL_NEO4J_USER`, `RKL_NEO4J_PASSWORD`.

   **Using a Neo4j Desktop 2 instance instead:** that works too, but run only ONE Neo4j at a time.
   Both the Desktop instance and the standalone server listen on 7687/7474; if the standalone server
   is up, the Desktop instance fails to start with a `netBind` (address already in use) error. Check with
   `Get-NetTCPConnection -LocalPort 7687`. Then either set the Desktop instance password to `refinery2024`
   or export `RKL_NEO4J_PASSWORD=<your password>` before `python -m knowledge_layer`. The pipeline detects an empty
   database and re-creates the Document, Term and Chunk nodes automatically.

2. **Ollama**: two sets of models, one per component.
   - *Knowledge layer extraction:* `ollama pull deepseek-r1:7b` (the default in `knowledge_layer/config.py`;
     `RKL_OLLAMA_MODEL` overrides it, `RKL_OLLAMA_URL` the server). On a 4 GB GPU the model is split between
     GPU and CPU (~3 tokens/s); extraction is very slow. Do not run Ollama inference while the Docling parse is
     running. Ollama is optional here: `python -m knowledge_layer --no-llm` builds the document graph,
     procedures and every deterministic claim without it. Note that no pipeline run to date has produced any
     LLM-extracted entities, relations or claims (every report under `data/reports/*/extraction_by_source.json`
     shows zero from the LLM); the graph the workbench uses is entirely deterministic.
   - *Workbench:* the models for your hardware profile (`workbench/config.py`, `PROFILES`):

     | profile | chosen when | text model | vision model | pull |
     |---|---|---|---|---|
     | `cpu` | no CUDA | qwen3.5:2b | qwen3.5:2b | `ollama pull qwen3.5:2b` |
     | `gpu_4gb` | >= 3.5 GB VRAM | qwen3:4b | qwen3.5:2b | `ollama pull qwen3:4b` and `ollama pull qwen3.5:2b` |
     | `gpu_8gb` | >= 7.5 GB | qwen3.5:4b | qwen3.5:4b | `ollama pull qwen3.5:4b` |
     | `gpu_12gb` | >= 11.5 GB | qwen3.5:9b | qwen3.5:9b | `ollama pull qwen3.5:9b` |
     | `gpu_16gb` | >= 15.5 GB | qwen3.5:9b | qwen3.5:9b | `ollama pull qwen3.5:9b` |
     | `gpu_24gb` | >= 23 GB | qwen3.6:27b | qwen3.6:27b | `ollama pull qwen3.6:27b` |

     The profile is picked from the VRAM torch reports; force one with `RWB_PROFILE=gpu_4gb` (any name in the
     table). `RWB_LLM_MODEL` / `RWB_VISION_MODEL` override the models, `RWB_OLLAMA_URL` the server
     (`RKL_OLLAMA_URL` is used as a fallback).

     The multi-model router is on by default: per call it picks the best *installed* model from the seven in
     `workbench/models/registry.yaml` (qwen3:4b, qwen3.5:2b, deepseek-r1:7b, qwen2.5-coder:7b, qwen3.5:9b,
     gpt-oss:20b, qwen3.6:27b), so any of those you have pulled may be used. `RWB_ROUTING=off` pins every
     call to the profile's text model. `python -m workbench models` shows what is installed and which model
     wins each kind of task.

     When the model is used: `--effort low` never calls it. The default, `medium`, does call it: the Answer
     Composer writes the released answer, the classifier is asked when the rules are unsure, and an elliptical
     follow-up may be rewritten. `high` and `ultra` add narrative, entity guessing, plan refinement and
     (ultra only) LLM diagnosis structuring. `RWB_LLM=off` disables the model everywhere; the workbench then
     answers deterministically.

3. **Embeddings and reranker**: `BAAI/bge-small-en-v1.5` (~130 MB) is downloaded from Hugging Face on the first
   run and cached under `~/.cache/huggingface`. The knowledge layer uses it for the chunk embeddings, and the
   workbench uses the same model to embed queries: it is hard-coded in `workbench/config.py`
   (`RetrievalSettings.embedding_model`, "must match the knowledge layer's index"). Do **not** switch the
   knowledge layer's `embedding.model_name` to another model to get around a missing download: the workbench
   would still embed queries with bge-small-en-v1.5 and vector retrieval would no longer match the stored
   embeddings. Pre-cache the model instead (below).

   The reranker (used at `--effort high` and `ultra`) is `BAAI/bge-reranker-base` on the `gpu_4gb` and
   `gpu_8gb` profiles, `BAAI/bge-reranker-v2-m3` on the 12/16/24 GB profiles, and none on `cpu`.

4. **Air-gap guard: pre-cache the Hugging Face models first.** The workbench installs an in-process egress
   guard whenever it builds its orchestrator (every `ask`, `repl`, `serve`, `bench`, ...), on by default
   (`workbench/sovereignty/egress.py`). It refuses sockets to public addresses (loopback, private ranges and
   the configured Ollama/Neo4j hosts are allowed) and sets `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and
   `HF_DATASETS_OFFLINE=1` for the process, so a model that is not already in the Hugging Face cache fails to
   load instead of being downloaded. Cache the models once, from a separate Python process, while the machine
   is still connected:
   ```powershell
   .venv\Scripts\python.exe -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-small-en-v1.5'); CrossEncoder('BAAI/bge-reranker-base')"
   ```
   (Use `BAAI/bge-reranker-v2-m3` for the 12 GB and larger profiles.) Running the knowledge-layer pipeline
   first also caches the embedding model. `RWB_AIRGAP=off` disables the guard, for diagnosis only; it never
   disables the network monitor (`RWB_NETMON=off` does). The intake OCR engine (`rapidocr`) keeps its ONNX
   models under `.venv\Lib\site-packages\rapidocr\models`; check they are present before disconnecting.

5. **Knowledge backend**: `RWB_KNOWLEDGE_BACKEND` defaults to `auto`, which uses `files` when any document has
   its knowledge-layer artefacts (`data/normalized/<doc>_normalized.json` and `data/knowledge/<doc>/chunks.json`)
   and otherwise falls back to `mock` (the fixtures in `workbench/fixtures/`). `files` builds a per-document index
   from those artefacts and caches it under `data/workbench/cache/<doc>/index.json` (~15 s cold per 560-page
   manual, ~3 s warm). `neo4j` is accepted but not implemented. `RWB_DOCS` (comma-separated document ids)
   restricts which documents load; by default all six currently in `data/` load (CDU operating manual,
   API610 pump operation manual, API560 comparison, Bulletin-5EH steam jet ejectors, Crude desalter, ESBWR).

6. **Workbench accounts and document tags**: run `python -m workbench setup-security` (equivalently
   `python scripts/setup_security.py`) once after the pipeline. It creates one account per role — `admin`,
   `manager`, `user` — if missing, and tags every loaded document: the CDU operating manual `SECRET` (admin
   only), the Crude desalter `CONFIDENTIAL` (manager and admin), every other document `INTERNAL` (all roles).
   It prints the resulting matrix so you can check it. Until a document is tagged, the pattern rules in
   `workbench/security/classification.py` apply and anything they miss is treated as `SECRET`.

   The accounts are seeded the first time any workbench command opens the credential store
   (`data/workbench/security/users.json`). To choose the passwords, set these **before that first run**:
   ```powershell
   $env:RWB_ADMIN_PASSWORD = "<a real password>"
   $env:RWB_MANAGER_PASSWORD = "<a real password>"
   $env:RWB_USER_PASSWORD = "<a real password>"
   ```
   or run `setup-security --random-passwords`, which sets strong random ones and prints them once.
   Otherwise the seeded demo passwords are `Admin#2026`, `Manager#2026` and `User#2026`. Only the seeded
   defaults are flagged must-change (and every CLI login says so until `python -m workbench passwd` changes
   them); passwords supplied through the environment variables or `--random-passwords` are not flagged.
   `setup-security --reset-passwords` (without `--random-passwords`) resets the three accounts to the
   documented defaults and clears the must-change flag. `RWB_AUTH=off` removes the gate (the benchmark and
   the test suite use it). The whole model is documented in `docs/SECURITY.md`.

7. **Authenticator codes (MFA)**: off by default. `RWB_MFA=on` asks `manager` and `admin` for a TOTP code;
   `RWB_MFA=admin` (or any comma-separated role list) names the roles; `RWB_MFA=off` is the default.
   `RWB_OTP_DEMO=1` returns the expected code in the login challenge for demonstrations, never for a real
   deployment. Enrolment is through the API (`POST /auth/mfa/enrol`, `/auth/mfa/confirm`), i.e. the web front
   end; a role that requires a code but has not enrolled can still sign in. Known limitation: CLI sign-in
   (`workbench login`, and the prompt before `ask`) does not handle the code challenge, so an enrolled account
   in an MFA role cannot sign in from the CLI and the command fails with an uncaught error.

## Running

```powershell
python -m knowledge_layer                 # resumable full pipeline over every PDF in data/raw
python -m knowledge_layer --no-llm        # deterministic passes only, no Ollama
python -m knowledge_layer --rebuild       # after upgrading the normalizer/chunker: redo everything but the parse
python knowledge_layer\scripts\audit_pipeline.py "<document id>"    # offline quality gate (no services needed)

python -m workbench setup-security        # accounts + document tags (once)
python -m workbench login --user admin    # sign in before asking anything of the classified documents
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
python -m workbench repl                  # follow-ups are read in the context of the turns before them
python -m workbench serve --port 8077     # FastAPI + SSE for the web front end (default port is 8000)
```

The sample question is answered from the CDU operating manual, which is `SECRET`: sign in as `admin` to get
the answer. As `manager` or `user` it is refused (and an access request is raised). For a quick run without
accounts, set `$env:RWB_AUTH = "off"`.

Cautions:
- `--clean` and `--rebuild` clear the Neo4j database at the start of *each* PDF they process, so with several
  PDFs in `data/raw` only the last document's graph survives in Neo4j. The on-disk artefacts the workbench
  reads are not affected.
- `python -m knowledge_layer --help` is not a help flag: it runs the full pipeline.

### Tests

```powershell
.venv\Scripts\python.exe -m pytest                 # 665 tests: 533 workbench + 132 knowledge_layer
.venv\Scripts\python.exe -m pytest tests\workbench
.venv\Scripts\python.exe -m pytest tests\knowledge_layer
python -m workbench bench                          # 70-prompt benchmark, LLM and auth off (~30 s)
```

## Verify your install

1. `.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"` — `True` if you installed the
   CUDA build and have a GPU.
2. `.venv\Scripts\python.exe knowledge_layer\scripts\validate_environment.py` — checks the knowledge
   layer's Python dependencies, the GPU, and whether Ollama and Neo4j are reachable (a missing Ollama or Neo4j
   is reported as a warning, not a failure). It does not check the workbench packages.
3. `ollama list` — the models for your profile are present.
4. `python -m workbench status` — shows the detected VRAM, the chosen profile, the text and vision models,
   whether the LLM is reachable (`llm_available`), the resolved backend (`files` once the pipeline has run) and
   the documents found (six with the current `data/`).
5. `python -m workbench models` — which registry models are installed and how the router would use them.
6. `python -m workbench setup-security`, then `python -m workbench login --user admin` and
   `python -m workbench ask "What is the normal flow rate of the crude charge pump?" --effort low` — a cited
   answer from the CDU manual (Crude Feed Pump, 11-PM-01) without the model; the request itself takes well
   under a second, although loading the indexes when the command starts takes a few seconds.
7. `.venv\Scripts\python.exe -m pytest --collect-only -q` — ends with `665 tests collected`.
8. `python -m workbench sovereignty` — the egress guard is installed and the chained logs verify.

See the `README.md` for the flags and the graph model.

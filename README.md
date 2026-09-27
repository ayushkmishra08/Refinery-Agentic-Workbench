# Refinery Knowledge Layer
# MRPL(Mangalore Refinery PetroChemicals Ltd.) | SIH 2026 
## What the workbench does now (September 2026)

The operator manual is **[`docs/manual/index.html`](docs/manual/index.html)** (open it in a browser). The table
below lists the September 2026 subsystems; the sections after it cover the knowledge layer, the agentic workbench,
the full CLI, configuration and the repository layout. A list of every doc in `docs/` is at the end.

| Capability | Where | Try it |
|---|---|---|
| Multi-model backend, auto-routed per call; pluggable via one YAML entry | `workbench/models/` | `python -m workbench models` · `python -m workbench route "read the scan and calculate the margin"` |
| Agentic execution over named local tools (`read_file`, `write_file`, `list_files`, `run_python`, `spreadsheet_read`, `spreadsheet_write`, `search_documents`, `calculate`, `ocr_image`, `describe_image`, `make_docx`, `make_xlsx`, `make_pptx`) | `workbench/tools/` | `python -m workbench tools` · `python -m workbench agent "calculate (520-482)/482*100"` |
| Ephemeral, no-egress, resource-bounded sandbox with static analysis + tests = verified, chained run log (see *Known limitations*) | `workbench/sandbox/` | `python -m workbench sandbox-run script.py --tests tests.py` |
| Multimodal intake: on-device OCR (RapidOCR) + local vision model; low-confidence lines flagged | `workbench/intake/` | `python -m workbench intake "data/sample docs/test.pdf" --max-pages 3` |
| Real deliverables (Word / Excel with live formulas / PowerPoint) under mandatory human sign-off | `workbench/deliverables/`, `workbench/review/` | `python -m workbench export <response_id> --format xlsx` · `drafts` · `draft-resolve` · `draft-signoff` |
| Envelope encryption per knowledge branch, local KMS, session-scoped decryption, rotation and revocation | `workbench/security/vault.py`, `vault_backend.py` | `python -m workbench vault seal --shred` · `vault rotate manager` · `RWB_VAULT=on` |
| Air-gap proof: in-process egress guard, live network monitor with a hash-chained connection log, physical-disconnect detection | `workbench/sovereignty/` | `python -m workbench sovereignty --watch 5` · `python -m workbench audit-verify` |
| Signed, checksum-verified model packages (never a live download) | `workbench/sovereignty/model_updates.py` | `python -m workbench packages verify ./pkg` |
| Local TLS / mutual TLS between components | `workbench/security/tls.py` | `python -m workbench serve --mtls` |
| Hash-chained audit logs everywhere (security, run audits, connections, routing, sandbox, keys, drafts, tools) | `workbench/sovereignty/hashchain.py` | `python -m workbench audit-verify` |

HTTP surface for all of it: `workbench/app/api.py` + `workbench/app/api_ext.py` (documented in `docs/API.md`,
live at `/docs` while `serve` runs).


Source-grounded engineering knowledge extraction from refinery documents into Neo4j.

## Architecture

```
PDF ─► DOCLING (faithful parse) + parse validation ─► TABLE CLASSIFICATION + reconstruction
                                          │
                                          ▼
                     NORMALIZER = clean + structure  (deterministic)
                       • page furniture removed: header/footer boxes, running chapter
                         titles, approval blocks, TOC tables, "[Figure on page N]"
                       • chapters from the table of contents + page headers
                       • section hierarchy from heading numbers, per chapter
                       • procedures detected (ordered instruction blocks)
                                          │
                                          ▼
                     DOCUMENT PROFILE ─► GLOSSARY ─► TYPED CHUNKS: canonical source text once
                       (procedure | table | specification | equipment | control |
                        safety | upset | narrative | document_control)
                          │                                   │
                          │ retrieval_text                    │ text
                          ▼                                   ▼
                    EMBEDDINGS (bge-small)        KNOWLEDGE EXTRACTION
                    Chunk nodes + vector index      1. rule relationships (routing, comprises, suction/discharge)
                                                    2. specification blocks + prose values  (deterministic)
                                                    3. table cells -> claims                (deterministic)
                                                    4. LLM entity pass + relationship/claim pass (engineering chunks)
                                                              │
                                                              ▼
                                       VALIDATION (grounding, units, domain) ─► ENTITY RESOLUTION
                                                              │
                                                              ▼
                                             NEO4J  ──  document graph + domain graph + evidence layer
```

Exact phase order in `knowledge_layer/pipeline.py`: parse + parse validation → table classification +
reconstruction → normalize → document profile → glossary → chunk → Neo4j schema / document / glossary terms →
7a document graph → 7b embeddings → 8 per-chunk extraction (LLM passes when Ollama is available, then
rule / specification / prose / table claims, validator, graph insert) → report.

The document graph (chapters, sections, procedures, standing instructions, cross references)
and every deterministic claim are written **before** and **independently of** the LLM. Ollama
is meant to add entities/relationships on top; when it is unavailable the graph is still complete for
everything the rules can read. **In practice the LLM passes have never produced graph output:** every
report under `data/reports/<doc>/extraction_by_source.json` shows only `rule` and `table` sources, so the
current graph is deterministic-only.

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

| field | examples | in `context_key()` |
|---|---|---|
| `parameter_role` | design, rated, normal, operating, minimum, maximum, mechanical_design, test, relief, alarm, trip | yes |
| `location` | suction, discharge, inlet, outlet, top, bottom, flash_zone, shell, tube | yes |
| `operating_mode` | normal_operation, startup, shutdown, emergency, temporary, upset | yes |
| `scenario` | Basrah, Bombay High, BH mode, PG mode, SKO operation, Kuwait, Kirkuk | yes |
| `pressure_basis` | absolute / gauge (kg/cm2A and kg/cm2G are different quantities) | yes |
| `qualifier` | table label / operating case / reference condition ("@ 20 °C") / distillation point | yes |
| `temporal_status` | current, historical, design, defunct | **no** — stored on the claim, not compared |

`EngineeringClaim.context_key()` (`knowledge_layer/schemas/claims.py`) joins the predicate with
`parameter_role`, `location`, `operating_mode`, `scenario`, `pressure_basis` and `qualifier` (lower-cased,
`|`-separated). Neo4j compares two claims on one subject **only when the context keys match** and they come
from different table rows / sentences: different values -> `CONFLICTS_WITH` and
`resolution_status = potential_conflict`; equal values -> `CORROBORATES` and `supported`. So the crude charge
pump's *normal* 482 m3/h, *minimum* 219 m3/h and *design limit* 520 m3/h are three facts, the *design*
3.0 MMTPA and the *enhanced* 3.2 MMTPA are two facts, and suction 2.0 vs discharge 24.45 kg/cm2A never meet.

Every claim, relationship and entity keeps `document_id`, `page`, `chunk_id`, `evidence`
(the sentence or table row), `source` (`llm_pass1 | llm_pass2 | rule | table`) and `grounding`.

## Quick Start

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"            # then the CUDA torch build, see SETUP.md
python knowledge_layer\scripts\validate_environment.py
# place the PDFs in data/raw/, start Neo4j (knowledge_layer\scripts\start_neo4j.ps1 -Detached) and Ollama
python -m knowledge_layer                      # full pipeline over every PDF in data/raw/ (resumable)
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

> **Warning — `--clean` / `--rebuild` wipe the whole Neo4j database once per PDF.** The flags are applied inside
> the per-document loop (`knowledge_layer/pipeline.py:352-354` calls `clear_all_data()` for every document), so
> with the six PDFs now in `data/raw/` only the last document processed survives in Neo4j. To rebuild one
> document, leave only that PDF in `data/raw/` or reset its phases with `knowledge_layer\scripts\reset_phases.py`.
> The flags are read straight from `sys.argv`; there is no `--help` — `python -m knowledge_layer --help` runs the
> full pipeline.

## Prerequisites

- Python 3.10+ (developed on 3.13)
- Neo4j 5.11+ / 2026.x (Community server; see `knowledge_layer/scripts/start_neo4j.ps1`) — optional: without it
  the pipeline still writes every on-disk artefact but skips the graph-dependent phases; the workbench does not
  read Neo4j
- Ollama. Knowledge layer: `deepseek-r1:7b` by default (`RKL_OLLAMA_MODEL` to change; optional — without it
  the deterministic layers still run). Workbench on the 4 GB profile: `qwen3:4b` + `qwen3.5:2b`
- NVIDIA GPU with CUDA for the Docling parse (GTX 1650 4 GB tested); CPU fallback is ~10x slower
- Embeddings: `BAAI/bge-small-en-v1.5` (384-dim, downloaded on first run, CPU); the vector index is
  recreated automatically if the embedding dimension changes. The workbench runs with Hugging Face offline
  (see *Air gap* below), so this model and the reranker must already be in the local cache
- Node.js / npm for the web front end in `WebPage/` (see `WebPage/README.md`)

## Agentic Workbench (fully local)

`workbench/` is the agent layer on top of this knowledge layer: 12 named agents (task classifier, context resolver,
planner, procedure, diagnostic, calculation, comparison, safety, revision/conflict, report, verification, governance)
plus four retrieval specialists (lookup, graph, explanation, cross-document) and the Answer Composer — 17 agent
keys in `workbench/agents/registry.py` — orchestrated through phases 0-5 with a replan loop, a "btw" status side
channel, human-in-the-loop flags and an audit trail. It classifies each request into one of 15 task types and
answers lookups, flow traces, procedures, troubleshooting, limit checks, why-questions, safety, comparisons,
conflicts/provenance, work plans, reports, corpus inventories and cross-document questions with evidence-cited
render blocks for a web front end.

```powershell
python -m workbench setup-security              # create the three role accounts and tag every document
python -m workbench login                       # sign in first; nothing is readable until you do
python -m workbench security                    # the role schema, the document tags, your own access
python -m workbench security-check --passwords  # red-team every role for leakage
python -m workbench status                      # hardware profile, models, backend, documents
python -m workbench whoami                      # role, tags, which documents it opens
python -m workbench ask "What is the normal flow rate of the crude charge pump?"
python -m workbench ask "What are all the equipments in the refinery?"   # survey the corpus, no entity needed
python -m workbench ask "..." --effort low      # index only, no model call; medium (default) and above call the model
python -m workbench ask "..." --no-thinking     # answer only; --json gives the raw FinalResponse
python -m workbench ask "..." --detail          # every render block instead of the composed answer
python -m workbench trace --show                # replay the newest saved thinking trace
python -m workbench repl                        # follow-ups read in context; "btw what's going on?" while a request runs
python -m workbench serve --port 8077           # FastAPI + SSE for the frontend (docs/API.md); --tls / --mtls for local TLS
python -m workbench models                      # model registry: capabilities, what is installed, who wins each task kind
python -m workbench route "..."                 # how a request is decomposed and routed (no model call)
python -m workbench tools | tool <name> --args '{...}' | agent "<goal>"   # named local tools and the agent loop
python -m workbench sandbox-run file.py --tests tests.py                  # sandbox
python -m workbench intake <file> [--purpose pid|handwriting|gauge]       # on-device OCR + vision
python -m workbench responses | export <response_id> --format docx|pptx|xlsx   # deliverables
python -m workbench drafts | draft-resolve <draft> <figure> accepted|corrected|removed | draft-signoff <draft>
python -m workbench vault status|seal|rotate <role>|revoke <branch> <role>   # envelope encryption
python -m workbench sovereignty [--watch 5] | audit-verify                   # air-gap proof, chain integrity
python -m workbench packages keygen|trust|sign|verify|import|log             # signed model packages
python -m workbench -v bench                    # 70-prompt benchmark (16 categories), LLM off unless --llm
```

**Serve port.** `serve` defaults to `127.0.0.1:8000`. The web front end's Vite proxy (`WebPage/vite.config.ts`)
targets `http://127.0.0.1:8077` unless `VITE_WORKBENCH_URL` is set, so start the API with `--port 8077` when
using `WebPage/`.

**Benchmark.** `bench` runs `workbench/benchmarks/prompts.yaml` (70 prompts in 16 categories) with `RWB_LLM=off`
unless `--llm` is given, and writes a report to `data/workbench/reports/`. Latest run
(`benchmark-20260916-202058.md`, files backend, gpu_4gb profile, LLM off): task accuracy, agents, entities,
blocks, safety and answered all 1.0; mean confidence 0.637; mean 434 ms per prompt; 30.6 s total; 0 LLM calls.

**Access control.** Three roles — `user` < `manager` < `admin` — and three document tags — `INTERNAL` <
`CONFIDENTIAL` < `SECRET`. A role reads a document when its level reaches the tag's, and nothing above it.
Here that means the CDU operating manual is readable by an administrator, the Crude desalter manual by a
manager, and the standards and vendor manuals by anyone signed in; a guest reads nothing, and the workbench
asks who you are *before* it takes a question. `setup-security` pins a tag on every document present at setup
(unmatched ones get `INTERNAL`); a document added later that no pattern rule matches, and any record whose
document was never classified, is treated as `SECRET`.
Enforcement sits at the knowledge service, so no search, tag lookup or wording in the answer pipeline reaches
around it — which is why prompt injection has nothing to work with. (The code sandbox is the exception today:
without Docker it can read files on disk directly; see Known limitations.) A question above your level raises an access request: the approver
reviews the exact records and, if they agree, issues a signed single-use key (valid 30 minutes) that opens those
records for that one question and nothing else. `python -m workbench security-check` red-teams the whole thing
against your own documents. Seed accounts and their must-change passwords are listed in `docs/SECURITY.md`.
**Start here: [`docs/SECURITY.md`](docs/SECURITY.md)** — written for a first-time reader. Gaps between that
design and the current code are listed under *Known limitations* below.

**The answer is written, not assembled.** An Answer Composer turns the retrieved claims, edges, procedure steps and
passages into prose — the knowledge is context, the reply is composed from it — and checks every figure, tag and
equipment name in what comes back against that context before releasing it. Follow-ups are read in the context of the
turns before them ("what if we use 11-E-01 instead?" knows what it is instead *of*), and a tag the documents do not
contain is matched to the ones they do ("12-3-01" is answered as 12-P-01, or asked about when two candidates tie).
The typed blocks are still on the response for the frontend, and `--detail` prints them.

`ask` prints the agents' reasoning phase by phase as the run happens — which rules fired and what they scored,
what each entity resolved to, why a retrieval route was chosen, the execution DAG with its dependencies, which
model ran where, how the answer was composed and checked, and how governance reached its verdict — then the answer,
then the path of the JSON trace it saved under `data/workbench/thinking/`. The answer itself carries only engineering
content and a source line; confidence, grounding and audit ids stay in the thinking and in the JSON.

`--effort low|medium|high|ultra` sizes the request (`workbench/config.py`, `EFFORT_LEVELS`):

| level | model calls | retrieval |
|---|---|---|
| `low` | none — rules, claims and BM25 only | no vectors, no reranker |
| `medium` (default) | the Answer Composer writes the released answer; the model also classifies when the rules are unsure and rewrites follow-ups | vectors, no reranker |
| `high` | + entity guesses for missed equipment, narrative, plan refinement | wider, reranker on |
| `ultra` | + model-structured diagnosis (the only level with LLM extraction) | widest, reranker on |

See `docs/HOW_IT_WORKS.md` §11-12, `docs/ACCESS_AND_ANSWERS.md` for access control and answer composition,
and `docs/DEMO.md` for a question per capability.

**Knowledge backend.** `RWB_KNOWLEDGE_BACKEND` defaults to `auto`: the `files` backend when knowledge-layer
artefacts exist on disk (they do for all six documents: CDU operating manual, API610, API560, Bulletin-5EH,
Crude desalter, ESBWR), otherwise the `mock` fixtures in `workbench/fixtures/`. Either is wrapped in a
`composite` service that adds session uploads. No Neo4j is needed. The `neo4j` backend
(`workbench/services/backends/neo4j_backend.py`) is a stub that raises `NotImplementedError`; wiring it is the
pending step described in `docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md`. Presentation-level explanation:
`docs/HOW_IT_WORKS.md`.

**Models.** The hardware profile is picked from detected VRAM (`RWB_PROFILE` forces one): `qwen3:4b` +
`qwen3.5:2b` (vision) on the 4 GB card, larger Qwen 3.5/3.6 models on 8-24 GB cards. The model router is on by
default and picks, per call, the best installed model from the seven in `workbench/models/registry.yaml`
(qwen3:4b, qwen3.5:2b, deepseek-r1:7b, qwen2.5-coder:7b, qwen3.5:9b, gpt-oss:20b, qwen3.6:27b);
`RWB_ROUTING=off` pins every call to the profile's text model. `deepseek-r1:7b` is the knowledge layer's
extraction default, not a workbench requirement.

### Full CLI

`python -m workbench [-v] <command>`; every command has `--help`.

| group | commands and notable flags |
|---|---|
| Ask | `ask <text>` `--session` `--effort` `--no-thinking` `--detail` `--json` `--key <grant key>` · `repl` `--session` `--effort` `--no-thinking` `--detail` · `trace` `--limit` `--show` |
| Sign-in and accounts | `login` `--user` · `logout` · `whoami` · `passwd` `--user` · `users` (list) / `users --add <name> --role user\|manager\|admin --name "<full name>"` · `setup-security` `--reset-passwords` `--random-passwords` (same as `python scripts/setup_security.py`) |
| Classification | `security` · `classify [document] --tag INTERNAL\|CONFIDENTIAL\|SECRET --reason "..."` (administrators) |
| Access requests | `request-access <text>` `--session` · `requests` · `approvals` `--show` `--all` · `approve <request_id>` `--note` · `deny <request_id>` `--note` · `revoke-key <grant_id>` |
| Security checks and logs | `security-check` `--passwords` `--no-injections` `--verbose-probes` · `security-log` `--event` `--principal` `--limit` `--json` |
| Server and schemas | `serve` `--host` (127.0.0.1) `--port` (8000) `--tls` `--mtls` · `schema` `--out` (JSON schemas for the frontend, `docs/schema/`) |
| Inspection | `status` · `agents` · `models` · `route <text>` `--budget normal\|fast` · `bench` `--category ...` `--limit N` `--llm` |
| Sovereignty | `sovereignty` `--deep` `--watch <seconds>` `--json` · `audit-verify` · `packages keygen\|trust\|sign\|verify\|import\|log` |
| Vault | `vault status\|seal\|rotate\|revoke` |
| Tools and sandbox | `tools` · `tool <name>` `--args '<json>'` `--session` `--json` · `agent <goal>` `--session` `--effort` `--max-iterations` `--json` · `sandbox-run <file>` `--tests` `--json` |
| Intake | `intake <file>` `--no-vision` `--purpose general\|pid\|handwriting\|photo\|gauge` `--max-pages` `--vision-calls` `--json` |
| Deliverables and review | `responses` · `export <response_id>` `--format docx\|pptx\|xlsx\|md` `--title` `--out` · `drafts` · `draft-resolve <draft> <figure> accepted\|corrected\|removed` · `draft-signoff <draft>` `--reject` `--note` |

### Environment variables

Workbench overrides are applied in `load_config()` (`workbench/config.py`); knowledge-layer overrides in
`load_config()` (`knowledge_layer/config.py`).

| variable | effect |
|---|---|
| `RWB_PROFILE` | force a hardware profile: `cpu`, `gpu_4gb`, `gpu_8gb`, `gpu_12gb`, `gpu_16gb`, `gpu_24gb` |
| `RWB_EFFORT` | default effort level (`medium` if unset) |
| `RWB_LLM=off` | no model calls at all (rule-based classification, template narratives) |
| `RWB_LLM_MODEL`, `RWB_VISION_MODEL` | override the profile's text / vision model |
| `RWB_OLLAMA_URL` (falls back to `RKL_OLLAMA_URL`) | Ollama base URL |
| `RWB_LLM_ANSWER=off` | skip the Answer Composer's model call |
| `RWB_LLM_EXTRACTION=on\|off` | override the effort level's model-structured diagnosis switch |
| `RWB_RERANKER=off` | disable the cross-encoder reranker |
| `RWB_ROUTING=off` | disable the multi-model router |
| `RWB_KNOWLEDGE_BACKEND` | `auto` (default) \| `files` \| `mock` \| `neo4j` (stub) |
| `RWB_DOCS` | comma-separated document ids to load (default: every document found) |
| `RWB_AUTH=off` | disable access control (benchmark and tests only) |
| `RWB_MFA` | `on` = manager + admin need an authenticator code; or a role list; `off` (default: no role) |
| `RWB_OTP_DEMO=1` | return the expected code in the login challenge (demo only) |
| `RWB_ANSWER_STYLE` | `brief` (default) \| `full` |
| `RWB_AIRGAP=off` | disable the in-process egress guard (the monitor still runs) |
| `RWB_NETMON=off` | disable the network monitor |
| `RWB_VAULT=on` | read the knowledge branches through the envelope-encryption vault |
| `RWB_SANDBOX_BACKEND` | `auto` (default) \| `subprocess` \| `docker` |
| `RWB_OCR_THRESHOLD` | OCR confidence (0-1) below which a line is flagged for review |
| `RWB_KEEP_WARM=1` | keep the LLM resident instead of unloading it after the idle window (`workbench/services/resources.py`) |
| `RWB_WARM_START` | `1` (default) warms the API's orchestrator at start-up; anything else skips it (`workbench/app/api.py`) |
| `RKL_NEO4J_URI`, `RKL_NEO4J_USER`, `RKL_NEO4J_PASSWORD` | knowledge-layer Neo4j connection |
| `RKL_OLLAMA_URL`, `RKL_OLLAMA_MODEL` | knowledge-layer Ollama URL and extraction model (default `deepseek-r1:7b`) |

### Air gap

The egress guard is on by default (`airgap_enforced = True`) and is installed whenever a workbench orchestrator
starts: sockets to anything other than loopback, private ranges and the configured Ollama / Neo4j hosts are
refused and logged to a hash-chained log. It also sets `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` and
`HF_DATASETS_OFFLINE=1`, so the embedding model (`BAAI/bge-small-en-v1.5`), the reranker and any other
Hugging Face weights **must be downloaded into the local cache before the machine is disconnected** — a missing
model fails instead of being fetched. Ollama models are pulled beforehand the same way, or installed from a signed
package (`packages import`). `RWB_AIRGAP=off` lifts the guard for a connected setup session.

### Known limitations

Current behaviour, from the 2026-09-26 audit (`docs/DOC_AUDIT.md` §1); none of these is fixed yet.

- **Sandbox:** without Docker the `subprocess` backend is used, whose isolation is Python-level only; file reads
  are not confined and static-analysis findings are advisory. Any signed-in account can call `POST /sandbox/run`,
  so the sandbox is not a data boundary today.
- **Unauthenticated HTTP routes:** `/runs`, `/runs/{id}`, `/runs/{id}/events`, `/btw`, `GET /vault`,
  `GET /sovereignty`, `/sovereignty/connections`, `/sovereignty/egress`.
- `/audit/{session_id}` returns `[]` for non-admins; the SSE stream emits an extra `final` event before the real
  FinalResponse; CORS allows every origin.
- CLI sign-in fails for enrolled accounts whose role requires MFA; `users --add`, `security-log`,
  `security-check`, `vault rotate` and `vault revoke` have no role check; `approvals --all` shows every request to any signed-in user; `passwd` accepts 4-character passwords.
- Login messages differ for unknown users and wrong passwords; wrong TOTP codes do not count toward lockout and
  the TOTP secret is stored in plain text in `users.json`.
- A parse error in the classification file re-derives default tags and drops pins/allowlists.
- Knowledge layer: `--clean` / `--rebuild` clear Neo4j per PDF (see the warning above); there is no `--help`;
  LLM extraction has never produced graph output.
- `npm run lint` in `WebPage/` fails (eslint packages are not in devDependencies).

## Project Structure

The agentic workbench lives in `workbench/` and consumes this knowledge layer through
`workbench/services/protocols.py`. Build plan and agent contracts: `docs/PLAN.md`;
workflow diagram: `docs/architecture/agent_workflow.png`.

```
knowledge_layer/              the extraction pipeline (python -m knowledge_layer)
  pipeline.py                 phase orchestrator (entry point via __main__.py)
  parser, parse_validation, table_classifier, table_reconstructor, normalizer, structure,
  document_profile, glossary_builder, chunker, embedder, extractor, validator, spec_claims,
  table_claims, table_context, rule_relations, entity_identity, entity_resolver, ontology_manager,
  reference_tracker, document_graph, graph, memory, retriever, provenance, checkpoint, reporter, config
  schemas/                    pydantic models: parsed_document, normalized_document, claims, knowledge,
                              document_profile, glossary, ontology, table_schema, validation
  prompts/                    claim_extraction, cross_check, document_orientation, entity_extraction,
                              glossary_extraction, procedure_extraction, relationship_extraction,
                              safety_extraction, table_extraction (.txt; the extractor uses
                              entity_extraction and relationship_extraction)
  scripts/                    validate_environment.py, start_neo4j.ps1, setup_neo4j.py, run_parse.py,
                              audit_pipeline.py (quality gate), probe_extract_chunk.py, probe_graph_insert.py,
                              probe_identity.py, dump_graph.py, graph_integrity.py, cleanup_graph.py,
                              migrate_entities.py, reset_phases.py, check_tables.py, inspect_docling.py,
                              inspect_parsed.py, inspect_tables.py, benchmark.py, smoke_test.py,
                              test_fixes.py, test_parse_window.py, test_parser_fix.py

workbench/                    the agentic layer (python -m workbench)
  config.py                   hardware profiles, effort levels, all settings and env overrides
  agents/                     17 agent keys (registry.py) incl. composer.py (Answer Composer)
  orchestration/              orchestrator, router, executor, hitl, narration, runs, status_agent ("btw")
  core/                       request (15 task types), blocks (16 block types), events, result, plan, evidence
  services/                   knowledge service, backends/ (files, mock, composite, neo4j stub), index/,
                              calculators/, tag matcher, revision resolver, stores (response, thinking,
                              evidence, audit), context builder, ingest, resources
  memory/                     sessions and follow-up rewriting
  llm/                        Ollama client, fake LLM for tests, prompt helpers
  prompts/                    answer_composer, context_resolver, diagnostic, explanation, planner,
                              report_summary, task_classifier (.txt)
  models/                     registry.yaml (7 models), registry, router, routed LLM
  tools/                      named local tools and the agent loop
  sandbox/                    runner, manifest, static verification, chained run log
  intake/                     OCR (RapidOCR), vision, intake pipeline
  deliverables/               docx / xlsx / pptx builders and export
  review/                     drafts and human sign-off
  security/                   roles, auth, totp, classification, policy, guard, escalation, leakcheck,
                              redteam, audit, records, setup, tls, vault, vault_backend
  sovereignty/                egress guard, network monitor, hash chain, signed model updates, service
  benchmarks/                 prompts.yaml (70 prompts, 16 categories) and runner
  app/                        cli.py + cli_ext.py, api.py + api_ext.py (FastAPI + SSE), credentials, thinking display
  fixtures/                   mock knowledge (see fixtures/README.md)

scripts/setup_security.py     same as `python -m workbench setup-security`
WebPage/                      React 19 + Vite + TypeScript front end (see WebPage/README.md); dev server on
                              5173, proxies /api to the workbench on 8077
tests/                        665 tests: tests/workbench (533) and tests/knowledge_layer (132)
docs/                         see the list below
neo4j-data/                   standalone Neo4j Community server (see SETUP.md)
SETUP.md, walkthrough.md      installation guide; knowledge-layer build walkthrough

data/raw/                     source PDFs (six documents)
data/parsed/<doc>/            Docling output: parsed_document.json, tables.json, metadata.json, images/,
                              windows/, parse_report.md, parse_log.txt
data/normalized/              <doc>_normalized.json: cleaned + structured document (chapters, sections,
                              procedures, elements, filter audit)
data/knowledge/<doc>/         document_profile.json, glossary.json, chunks.json, chunk_embeddings.json;
                              data/knowledge/ontology.json
data/checkpoints/             resume state (<doc>_parse.json, <doc>_pipeline.json)
data/failures/                per-chunk extraction failure records
data/reports/<doc>/           report.md, extraction_by_source.json
data/workbench/               workbench state: cache/ (per-document index), sessions/, thinking/, audit/,
                              responses/, deliverables/, drafts/, uploads/<user>/<session>/, security/
                              (users, tags, grants, keys, TLS), sovereignty/, routing/, sandbox/,
                              workspace/, vault/, reports/ (benchmark reports)
```

Run the tests with `.venv\Scripts\python.exe -m pytest` (665 collected on 2026-09-26).

## What the deterministic layers produce on the CDU-II manual (562 pages)

| layer | result |
|---|---|
| normalizer | 36 chapters (TOC + page headers), 1,119 sections in the workbench index (1,084 in the knowledge-layer profile), 182 procedures / 1,503 ordered steps; 599 figure placeholders, 539 header boxes, 90 running titles, 9 approval blocks dropped |
| chunker | 467 typed chunks (111 procedure, 84 equipment, 79 narrative, 68 table, 67 safety, 27 upset, 24 control, 7 document_control); 0 chunks with overlap text or figure markers |
| tables | 814 tables classified (538 header tables, 92 equipment, 11 operating limit, 11 process data, 134 unknown, ...) |
| glossary | 172 entries; the document profile lists 96 abbreviations |
| latest extraction report (2026-09-16, `data/reports/CDU operating manual/`) | rule: 116 relationships accepted (5 rejected), 40 claims accepted (2 rejected, 21 weak); table: 364 claims accepted; 0 LLM entities, relationships or claims (397 chunks deterministic-only, 70 skipped) |
| workbench index (files backend, `data/workbench/cache/CDU operating manual/index.json`) | 1,056 entities, 276 relations, 1,531 claims, 182 procedures, 467 chunks, 1,119 sections |
| claims (earlier Neo4j run, not reproduced since) | 1,528 table/specification/prose claims in Neo4j with role / location / scenario / basis, e.g. `11-PM-01A/B`: normal 482 & minimum 219 m3/h, suction 2.0 & discharge 24.45 kg/cm2 (absolute), differential head 367.3 m, NPSH 6 m, design pressure 31.7 kg/cm2 (absolute) |
| conflicts (same earlier run) | 0 potential conflicts, 1 corroboration (12-C-01 bottom 350 °C stated on p.117 and p.257). Before the context key the same claims produced hundreds of false conflicts (BH vs PG exchanger tables, min/normal/max columns, suction vs discharge, design vs enhanced capacity) |
| graph (same earlier run, `--no-llm`, 4.6 min after the parse) | 531 entities (tags from steps, claims and rule relationships), 169 typed relationships, 1,503 procedure steps with NEXT order, 649 chunk->section links |
| references | 64 document references with real identifiers (P&ID 10-1100-E-203 Rev.5, IS-4576, ADM/OPRN/PRODN/SI/008 ...), 20 in-document cross references in the current document profile (the earlier Neo4j run counted 38), 19 standing instructions |

The earlier-run Neo4j figures predate the six-document corpus; re-running `--clean` / `--rebuild` today would
leave only the last PDF in Neo4j (see the warning above), so they have not been regenerated.

The LLM passes are designed to add entity typing and relationships on top of this; so far they have produced
none. On the GTX 1650 (4 GB) `deepseek-r1:7b` (4.7 GB) runs in a CPU/GPU split at about 3 tokens/s, i.e.
10-18 minutes per chunk; run it on a gold slice (`--max-pages`) or use a model that fits the GPU
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

## Documentation

| doc | what it covers |
|---|---|
| [`docs/manual/index.html`](docs/manual/index.html) | operator manual (open in a browser) |
| [`SETUP.md`](SETUP.md) | installation guide: Python environment, CUDA torch, Neo4j |
| [`walkthrough.md`](walkthrough.md) | phase-by-phase walkthrough of the knowledge-layer build |
| [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) | plain-language walk-through of agents, workflow, thinking trace, effort levels |
| [`docs/SECURITY.md`](docs/SECURITY.md) | roles, document tags, sign-in, access requests and keys, red team |
| [`docs/ACCESS_AND_ANSWERS.md`](docs/ACCESS_AND_ANSWERS.md) | access control, the Answer Composer, follow-ups, tag matching |
| [`docs/API.md`](docs/API.md) | HTTP + SSE contract for the front end |
| [`docs/schema/`](docs/schema/) | JSON schemas: block, final_response, progress_event, user_request |
| [`docs/DEMO.md`](docs/DEMO.md) | demo script, one question per capability |
| [`docs/PLAN.md`](docs/PLAN.md) | original build plan and agent contracts (historical) |
| [`docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md`](docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md) | plan for the Neo4j backend |
| [`docs/SOLUTION_CONTEXT.md`](docs/SOLUTION_CONTEXT.md) | consolidated solution context |
| [`docs/architecture/agent_workflow.png`](docs/architecture/agent_workflow.png) | workflow diagram |
| [`docs/DOC_AUDIT.md`](docs/DOC_AUDIT.md) | 2026-09-26 documentation audit and known code issues |
| [`WebPage/README.md`](WebPage/README.md) | web front end |
| [`workbench/fixtures/README.md`](workbench/fixtures/README.md) | mock knowledge fixtures |

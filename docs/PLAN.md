# Refinery Engineering AI Workbench — Build Plan

Diagram: `docs/architecture/agent_workflow.png` (drawn for the original plan; it does not show the Answer Composer,
the retrieval specialists as separate boxes or the September subsystems). Everything runs locally (Neo4j Community,
Ollama, CPU embeddings).

> **Status note, 2026-09-26.** Sections 1, 2 and 4 are the original plan with factual errors corrected against the
> code. Section 3's status table has been updated to the current state: 665 tests (533 workbench + 132 knowledge
> layer), a 70-prompt / 16-category benchmark, six documents loaded, the September subsystems and the web frontend.
> Still open: the Neo4j backend (a stub), LLM extraction in the knowledge layer (it has never produced graph
> output), and the LLM-on quality pass.

## 1. Where things live

```
Refinery/
├── knowledge_layer/   extraction pipeline (parse → table classification → normalize → profile → glossary → chunk
│   │                  → Neo4j document graph → embeddings → per-chunk claims/relations); `python -m knowledge_layer`
│   ├── *.py              pipeline modules (config, parser, normalizer, chunker, extractor, memory, document_graph, ...)
│   ├── schemas/          pydantic models shared by both layers (claims, knowledge, glossary, profile, ...)
│   ├── prompts/          extraction prompts
│   └── scripts/          ops scripts (run_parse, audit_pipeline, dump_graph, start_neo4j.ps1, ...)
├── workbench/         agentic layer — see workbench/__init__.py for the module map
│   ├── core/          contracts: UserRequest → StructuredRequest → ContextPackage → Plan → AgentResult → FinalResponse
│   ├── llm/           Ollama client (structured JSON output), FakeLLM, prompt loader
│   ├── models/        model registry (registry.yaml, 7 models), capability router, RoutedLLM
│   ├── services/      "Shared Infrastructure" row: KnowledgeService protocol + files / composite / mock backends
│   │                  (neo4j_backend.py is a stub), index (BM25 + vector + reranker), evidence store, revision
│   │                  resolver, calculators, context builder, audit / response / thinking stores, ingest
│   ├── tools/         named local tools (files, run_python, spreadsheets, search_documents, calculate) + the tool
│   │                  agent loop; used by `/tools` and `workbench agent`, not by the plan agents
│   ├── sandbox/       sandboxed Python execution for run_python (docker or isolated subprocess)
│   ├── intake/        on-device OCR + vision for images and scanned PDFs
│   ├── deliverables/  Word / PowerPoint / Excel / markdown export of released answers
│   ├── review/        drafts with flagged figures and human sign-off
│   ├── security/      roles, document tags, auth + TOTP, policy guard, escalation keys, leak check, vault, TLS
│   ├── sovereignty/   egress guard, network monitor, hash-chained logs, signed model packages
│   ├── agents/        17 agent keys registered in agents/registry.py: 12 named agents + 4 retrieval specialists
│   │                  (all in retrieval.py) + the Answer Composer (composer.py)
│   ├── orchestration/ router (routing matrix + templates), DAG executor, orchestrator (Orchestrator.ask: phases,
│   │                  replan), HITL gate, runs, status agent ("btw"), narration (thinking text)
│   ├── memory/        per-session memory, follow-up rewriting
│   ├── prompts/       agent system prompts
│   ├── benchmarks/    prompts.yaml + runner
│   ├── fixtures/      mock knowledge JSON (used by the mock backend when no KL artefacts exist)
│   └── app/           CLI (python -m workbench ask "...") and the FastAPI server (api.py + api_ext.py, `serve`)
├── WebPage/           React + Vite frontend against the API (see WebPage/README.md)
├── scripts/           setup_security.py
├── tests/             tests/knowledge_layer/  +  tests/workbench/
├── data/              shared artefacts: raw, parsed, normalized, knowledge, checkpoints, reports, workbench/
├── neo4j-data/        local Neo4j Community server (gitignored)
└── docs/              this plan, architecture image, HOW_IT_WORKS, API, SECURITY, ACCESS_AND_ANSWERS, DEMO,
                       SOLUTION_CONTEXT, HANDOFF, manual/index.html, schema/*.json
```

The knowledge layer was moved from `src/` + `schemas/` into `knowledge_layer/` on 2026-09-15 (imports
rewritten mechanically, no logic changed). The workbench depends on it only through
`workbench/services/protocols.py::KnowledgeService` (plus `knowledge_layer` imports for config, entity identity
and, for uploads, the parser/normalizer/chunker).

## 2. Agent → phase → contract

Phases follow the `phase` attribute of each agent class. The four retrieval specialists (`lookup`, `graph`,
`explanation`, `cross_document`) are Phase 2 agents; the Answer Composer runs in Phase 5 between verification and
governance for every answered request and is not a plan step.

| Agent | Diagram phase | Input | Output | Depends on KL graph? |
|---|---|---|---|---|
| Task Classifier | 0 / 1 | UserRequest | TaskType(s), intent, confidence | no (weighted rules; LLM only when the rules are unsure) |
| Context Resolver | 0 / 1 | UserRequest + classifier output + session memory | StructuredRequest (entities via `knowledge_layer.entity_identity` canonical tags, scenario, operating mode, ambiguities, evidence requirements) | resolve_entity, glossary |
| Safety Agent | 0 gate, 3, 4, 5 | any AgentResult / StructuredRequest | SafetyFlags, SafetyStatus, block decision | safety chunks, claims (trip/alarm roles) |
| Planner | 3 | StructuredRequest + ContextPackage (+ failure trace on replan) | Plan (DAG of PlanSteps) seeded from ROUTING_MATRIX | no |
| Procedure Agent | 2 / 4 | entity / query | ordered steps with per-step Evidence, preconditions, standing instructions | Procedure→HAS_STEP→NEXT |
| Diagnostic Agent | 4 | symptom + entities | ranked causes + checks, each with Evidence | upset/safety chunks, FEEDS/CONTROLLED_BY neighbours |
| Calculation Agent | 4 | parameter request | calculator choice + inputs pulled by context key + result | spec/table claims |
| Comparison Agent | 4 | 2+ entities / scenarios / revisions | aligned table on matched context keys | claims |
| Revision/Conflict Agent | 2 / 4 | entity or claim set | CONFLICTS_WITH / CORROBORATES pairs, authority ranking, unresolved both-values | conflicts_for + document_profile |
| Report Agent | 4 / 5 | AgentResults | markdown report with citations → data/workbench/reports/ | no |
| Verification Agent | 4 | AgentResult | grounding / unit / context-key check; `needs_replan` on failure | chunks (evidence text) |
| Governance Agent | 5 | all results + verification | FinalResponse: answer, steps, citations, confidence, safety flags, provenance, audit id, HITL flag | no |
| Retrieval specialists (lookup, graph, explanation, cross_document) | 2 | StructuredRequest + ContextPackage | values, topology, explanations, cross-document evidence | claims, relations, chunks |
| Answer Composer | 5 | verified results + evidence brief | the released prose answer (grounding-checked) | no |

Invariants carried over from the knowledge layer: every statement maps to Evidence with document/page/chunk;
two numbers are comparable only when their claim context keys match; absolute vs gauge pressure are different
quantities; UNKNOWN beats GUESS; the LLM never does arithmetic.

## 3. Build order and status (updated 2026-09-26; original table 2026-09-15)

| # | Step | Status |
|---|---|---|
| 1 | Contracts (blocks, request, plan, result, events) + mock backend + fixtures | **done** — `workbench/core` (16 block types, 15 task types), `workbench/fixtures` |
| 2 | LLM client (Ollama, JSON-schema constrained, `think=False`), FakeLLM, prompts | **done** — probed with `qwen3:4b` (~5-6 s per short call) |
| 3 | Phase 0/1: Task Classifier (rules + LLM fallback) → Context Resolver → safety gate | **done** — benchmark now 70 prompts in 16 categories; latest run 2026-09-16 (files backend, no LLM): task, agents, entities, blocks, safety and answered all 1.0, mean 434 ms (`data/workbench/reports/benchmark-20260916-202058.md`) |
| 4 | Phase 2 services: files backend (index builder + BM25/vector/reranker store), evidence store, revision resolver, calculators, context builder | **done** — backend default `auto` (files when KL artefacts exist, else mock); six documents loaded. Neo4j backend is still a stub that raises `NotImplementedError` |
| 5 | Phase 3: planner templates per task type, compound extension, validation, LLM refinement for planning | **done** |
| 6 | Phase 4 agents: lookup, graph, explanation, cross_document, procedure, diagnostic, calculation, comparison, revision_conflict, report | **done** (LLM-optional) |
| 7 | Phase 5: verification, governance (policy, confidence, HITL, citations), audit, CLI ask/repl/serve/bench/schema, API + SSE + btw + upload + reviews | **done**; Answer Composer added (writes the released prose at `medium` effort and above; `low` is model-free) |
| 7a | Multi-model backend: `workbench/models` registry (7 models), capability router (on by default, `RWB_ROUTING=off` pins the profile model), routing log | **done** |
| 7b | Local tools + tool agent loop (`workbench/tools`), code sandbox (`workbench/sandbox`) | **done** — without Docker the subprocess backend's isolation is Python-level only (see `docs/DOC_AUDIT.md` §1) |
| 7c | Multimodal intake: OCR + vision for images / scanned PDFs, low-confidence lines flagged into review drafts (`workbench/intake`) | **done**; not yet exercised on a broad set of real scans |
| 7d | Deliverables (docx / pptx / xlsx / md export) + review drafts with mandatory flag resolution and manager sign-off (`workbench/deliverables`, `workbench/review`) | **done** |
| 7e | Security: roles × document tags, sign-in, optional TOTP, policy guard, leak check, escalation with signed single-use keys, envelope-encrypted vault, local TLS / mTLS (`workbench/security`) | **done**; open code issues are listed in `docs/DOC_AUDIT.md` §1 |
| 7f | Sovereignty: egress guard, network monitor, hash-chained logs, signed model packages (`workbench/sovereignty`) | **done** |
| 8 | Switch to Neo4j once extraction completes; LLM-on quality pass; test uploads/images on real files | **pending** — Neo4j backend is a stub; KL LLM extraction has never produced graph output (the graph is rule + table only); see `docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md` |
| 9 | Frontend integration against `docs/API.md`; optional parallel step execution on large GPUs | **frontend done** — `WebPage/` (React + Vite, proxy to `serve --port 8077`); no screen yet for the HITL `/reviews` queue. Parallel step execution **pending** (the executor runs steps sequentially) |

Documents: `docs/HOW_IT_WORKS.md` (presentation, all agents + workflow chart), `docs/API.md` (frontend contract),
`docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md` (what is left and how to plug in Neo4j), `docs/SECURITY.md`,
`docs/ACCESS_AND_ANSWERS.md`, `docs/DEMO.md`, `docs/SOLUTION_CONTEXT.md`, `docs/manual/index.html`,
`docs/schema/*.json`, `docs/DOC_AUDIT.md` (2026-09-26 audit of all docs against the code).

## 4. Open decisions (defaults chosen, change if you disagree)

- Orchestration is hand-rolled (pydantic + plain Python). Pydantic AI 1.0 and LangGraph were evaluated (2026-09-15): grammar-constrained Ollama JSON plus deterministic plan templates gives better reliability on 2-4B models than validate-and-retry agent loops, and keeps the system fully offline with fewer moving parts. LangGraph remains an option for durable multi-hour runs on the college GPU.
- Models by hardware profile (`workbench/config.py`): no GPU (`cpu`) → `qwen3.5:2b` text + vision; 4 GB → `qwen3:4b` text + `qwen3.5:2b` vision; 8 GB → `qwen3.5:4b`; 12-16 GB → `qwen3.5:9b`; 24 GB → `qwen3.6:27b`. The profile model is the fallback: with routing on (default) the model router picks per call from the installed models in `workbench/models/registry.yaml` (qwen3:4b, qwen3.5:2b, deepseek-r1:7b, qwen2.5-coder:7b, qwen3.5:9b, gpt-oss:20b, qwen3.6:27b). One model resident, idle unload, vision on demand. Override with `RWB_PROFILE` / `RWB_LLM_MODEL` / `RWB_VISION_MODEL`, or `RWB_ROUTING=off`.
- Agents never call each other; only the executor does. Cross-cutting agents (Safety, Verification) are invoked by orchestration hooks.

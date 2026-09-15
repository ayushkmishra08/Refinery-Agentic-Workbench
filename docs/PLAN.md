# Refinery Engineering AI Workbench — Build Plan

Diagram: `docs/architecture/agent_workflow.png`. Everything runs locally (Neo4j Community, Ollama, CPU embeddings).

## 1. Where things live

```
Refinery/
├── knowledge_layer/   extraction pipeline (parser → normalizer → chunker → claims → Neo4j); `python -m knowledge_layer`
│   ├── *.py              pipeline modules (config, parser, normalizer, chunker, extractor, memory, ...)
│   ├── schemas/          pydantic models shared by both layers (claims, knowledge, glossary, profile)
│   ├── prompts/          extraction prompts
│   └── scripts/          ops scripts (run_parse, audit_pipeline, dump_graph, start_neo4j.ps1, ...)
├── workbench/         agentic layer — see workbench/__init__.py for the module map
│   ├── core/          contracts: UserRequest → StructuredRequest → ContextPackage → Plan → AgentResult → FinalResponse
│   ├── llm/           Ollama client (structured JSON output), prompt loader
│   ├── services/      "Shared Infrastructure" row: KnowledgeService protocol + mock / neo4j backends,
│   │                  hybrid retrieval, evidence store, revision resolver, calculators, audit store
│   ├── tools/         registry of typed tool functions agents may call
│   ├── agents/        12 agents, one file each, registered in agents/registry.py
│   ├── orchestration/ router (routing matrix), DAG executor, replan loop, HITL gate, pipeline.run()
│   ├── memory/        per-session memory
│   ├── prompts/       agent system prompts
│   ├── fixtures/      mock knowledge JSON used while extraction is incomplete
│   └── app/           CLI (python -m workbench ask "...") and later a FastAPI server
├── tests/             tests/knowledge_layer/  +  tests/workbench/
├── data/              shared artefacts: raw, parsed, normalized, knowledge, checkpoints, reports, workbench/
├── neo4j-data/        local Neo4j Community server (gitignored)
└── docs/              this plan, architecture image
```

The knowledge layer was moved from `src/` + `schemas/` into `knowledge_layer/` on 2026-09-15 (imports
rewritten mechanically, no logic changed). The workbench depends on it only through
`workbench/services/protocols.py::KnowledgeService`.

## 2. Agent → phase → contract

| Agent | Diagram phase | Input | Output | Depends on KL graph? |
|---|---|---|---|---|
| Task Classifier | 0 / 1 | UserRequest | TaskType(s), intent, confidence | no (LLM only) |
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

Invariants carried over from the knowledge layer: every statement maps to Evidence with document/page/chunk;
two numbers are comparable only when their claim context keys match; absolute vs gauge pressure are different
quantities; UNKNOWN beats GUESS; the LLM never does arithmetic.

## 3. Build order and status (2026-09-15)

| # | Step | Status |
|---|---|---|
| 1 | Contracts (blocks, request, plan, result, events) + mock backend + fixtures | **done** — `workbench/core`, `workbench/fixtures` |
| 2 | LLM client (Ollama, JSON-schema constrained, `think=False`), FakeLLM, prompts | **done** — probed with `qwen3:4b` (~5-6 s per short call) |
| 3 | Phase 0/1: Task Classifier (rules + LLM fallback) → Context Resolver → safety gate | **done** — 62-prompt benchmark 2026-09-15: task accuracy 1.00, entities 1.00, blocks 1.00, safety 1.00 (LLM-free, `data/workbench/reports/benchmark-20260915-045202.md`) |
| 4 | Phase 2 services: files backend (index builder + BM25/vector/reranker store), evidence store, revision resolver, calculators, context builder | **done** — Neo4j backend pending (handoff doc) |
| 5 | Phase 3: planner templates per task type, compound extension, validation, LLM refinement for planning | **done** |
| 6 | Phase 4 agents: lookup, graph, explanation, cross_document, procedure, diagnostic, calculation, comparison, revision_conflict, report | **done** (LLM-optional) |
| 7 | Phase 5: verification, governance (policy, confidence, HITL, citations), audit, CLI ask/repl/serve/bench/schema, API + SSE + btw + upload + reviews | **done** |
| 8 | Switch to Neo4j once extraction completes; LLM-on quality pass; test uploads/images on real files | **pending** — see `docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md` |
| 9 | Frontend integration (friend's web UI) against `docs/API.md`; optional parallel step execution on large GPUs | **pending** |

Documents: `docs/HOW_IT_WORKS.md` (presentation, all agents + workflow chart), `docs/API.md` (frontend contract),
`docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md` (what is left and how to plug in Neo4j).

## 4. Open decisions (defaults chosen, change if you disagree)

- Orchestration is hand-rolled (pydantic + plain Python). Pydantic AI 1.0 and LangGraph were evaluated (2026-09-15): grammar-constrained Ollama JSON plus deterministic plan templates gives better reliability on 2-4B models than validate-and-retry agent loops, and keeps the system fully offline with fewer moving parts. LangGraph remains an option for durable multi-hour runs on the college GPU.
- Models by hardware profile (`workbench/config.py`): 4 GB → `qwen3:4b` text + `qwen3.5:2b` vision; 8 GB → `qwen3.5:4b`; 12-16 GB → `qwen3.5:9b`; 24 GB → `qwen3.6:27b`. One model resident, idle unload, vision on demand. Override with `RWB_PROFILE` / `RWB_LLM_MODEL`.
- Agents never call each other; only the executor does. Cross-cutting agents (Safety, Verification) are invoked by orchestration hooks.

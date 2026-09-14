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

## 3. Build order (each step is testable with the mock backend, no GPU/Neo4j needed)

1. **Contracts + mock backend** (done as stubs): `workbench/core`, `services/backends/mock_backend.py`, fixtures. Grow fixtures from real values in `data/knowledge/CDU operating manual/`.
2. **LLM client**: port the Ollama structured-output plumbing from `knowledge_layer/extractor.py`; probe script for `qwen3:4b` vs `deepseek-r1:7b` latency (routing needs the small model).
3. **Phase 0/1**: Task Classifier → Context Resolver → Safety gate → StructuredRequest. Golden-set test: ~30 hand-written questions per TaskType.
4. **Phase 2 retrievers as services**: hybrid retrieval, evidence store, revision resolver, calculators (unit tests only, pure functions).
5. **Phase 3**: Planner seeded by ROUTING_MATRIX, plan validator (agents exist, deps acyclic), executor over `Plan.ready_steps()`.
6. **Phase 4 agents**: Procedure → Calculation → Comparison → Revision/Conflict → Diagnostic → Report → Verification, in that order (easiest-to-verify first).
7. **Phase 5**: Governance agent, HITL gate, audit store, citation formatter, CLI `ask`/`repl`.
8. **Switch backend**: implement `Neo4jKnowledgeBackend` over `knowledge_layer.memory` + `knowledge_layer.retriever` once extraction finishes; run the same golden set with `RWB_KNOWLEDGE_BACKEND=neo4j`; write `knowledge_layer/scripts/export_fixtures.py` to regenerate fixtures from the live graph.
9. Optional: FastAPI server, parallel step execution, replan telemetry.

## 4. Open decisions (defaults chosen, change if you disagree)

- Orchestration is hand-rolled (pydantic + plain Python), not LangGraph/CrewAI, to keep it fully local, debuggable and dependency-light. Revisit at step 5 if the DAG logic grows.
- Model split: `qwen3:4b` for classification/resolution, `deepseek-r1:7b` for synthesis. Both configurable via `RWB_*` env vars.
- Agents never call each other; only the executor does. Cross-cutting agents (Safety, Verification) are invoked by orchestration hooks.

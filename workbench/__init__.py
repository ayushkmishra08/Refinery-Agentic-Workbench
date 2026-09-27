"""Refinery Engineering AI Workbench — fully local agentic layer.

Consumes the knowledge layer (``knowledge_layer/`` package; artefacts under ``data/``) through the
service boundary in ``workbench.services`` (``KnowledgeService``) and never imports extraction
internals directly. The default backend is ``auto``: ``services.backends.files_backend`` when
knowledge-layer artefacts exist (``data/knowledge``, ``data/normalized``), otherwise
``services.backends.mock_backend`` over the JSON fixtures; ``composite`` wraps either one so
per-session uploads can be layered on top. ``services.backends.neo4j_backend`` is a stub that
raises ``NotImplementedError`` until the Neo4j integration is written.

Layout (see docs/PLAN.md and docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md):
    core/           shared contracts: StructuredRequest, ContextPackage, Plan, AgentResult, blocks, events
    llm/            local Ollama client, FakeLLM, prompt loader, structured-output helper
    models/         model registry (registry.yaml), capability router, RoutedLLM
    services/       shared infrastructure row: KnowledgeService + backends, index (hybrid retrieval),
                    evidence, revision resolver, deterministic calculators, context builder,
                    audit / response / thinking stores, resources, uploads (ingest)
    agents/         17 agent keys: the 12 named agents, 4 retrieval specialists, the Answer Composer
    orchestration/  router (routing matrix), DAG executor, orchestrator (phases, replan), HITL gate,
                    runs, status agent, narration
    memory/         session memory, follow-up rewriting
    tools/          named local tools and the tool agent loop
    sandbox/        sandboxed Python execution for the run_python tool
    intake/         on-device OCR + vision for images and scanned PDFs
    deliverables/   Word / PowerPoint / Excel / markdown export of released answers
    review/         drafts with flagged figures and human sign-off
    security/       roles and document tags, auth + TOTP, policy guard, escalation, leak check,
                    security audit, vault, TLS
    sovereignty/    egress guard, network monitor, hash-chained logs, signed model packages
    benchmarks/     prompt set and runner
    prompts/        agent system prompts
    fixtures/       mock knowledge JSON
    app/            CLI (cli.py, cli_ext.py) and FastAPI server (api.py, api_ext.py)
"""
__version__ = "0.1.0"

"""Refinery Engineering AI Workbench — fully local agentic layer.

Consumes the knowledge layer (``src``/``schemas``, Neo4j + vectors) through the
service boundary in ``workbench.services`` and never imports extraction internals
directly. While knowledge extraction is incomplete, ``services.backends.mock_backend``
serves fixture data so every agent can be built and tested offline.

Layout (see docs/architecture/agent_workflow.png and docs/PLAN.md):
    core/           shared contracts: StructuredRequest, ContextPackage, Plan, AgentResult
    llm/            local Ollama client, prompt loader, structured-output helper
    services/       shared infrastructure row: graph, hybrid retrieval, evidence,
                    revision resolver, deterministic calculators, audit store
    tools/          callable tools exposed to agents (thin wrappers over services)
    agents/         the 12 agents (Phase 0/1/3/4/5 boxes in the diagram)
    orchestration/  router (routing matrix), DAG executor, replan loop, HITL gate
    memory/         session memory + audit trail
    app/            CLI / API entry points
"""
__version__ = "0.1.0"

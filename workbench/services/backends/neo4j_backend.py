"""Neo4jKnowledgeBackend — to be implemented once knowledge-layer extraction has finished.

The graph model it must read is listed in docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md
(labels, relationship types, properties, vector index ``chunk_embeddings``). Until then this
class raises a clear error so ``RWB_KNOWLEDGE_BACKEND=neo4j`` fails fast instead of silently
degrading; the files backend gives identical answers from the deterministic layers.
"""
from __future__ import annotations

from workbench.config import WorkbenchConfig


class Neo4jKnowledgeBackend:
    name = "neo4j"

    def __init__(self, cfg: WorkbenchConfig) -> None:
        raise NotImplementedError(
            "Neo4jKnowledgeBackend is not implemented yet. Use RWB_KNOWLEDGE_BACKEND=files (default) — "
            "see docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md for the implementation checklist."
        )

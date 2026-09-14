"""Live KnowledgeService over the graph produced by the knowledge layer.

TODO(build, after extraction completes): wrap ``src.memory.Neo4jMemory`` and
``src.retriever`` (vector index on Chunk.retrieval_text) behind the protocol.
Cypher must respect the claim context key (parameter_role / location / operating_mode /
scenario / pressure_basis) — see README "Claims are the evidence layer".
"""
from __future__ import annotations

from knowledge_layer.config import PipelineConfig


class Neo4jKnowledgeBackend:
    def __init__(self, config: PipelineConfig):
        from knowledge_layer.memory import Neo4jMemory
        self.memory = Neo4jMemory(config)
        self.memory.connect()

    # implement KnowledgeService methods here

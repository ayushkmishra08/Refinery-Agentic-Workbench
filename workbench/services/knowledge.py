"""Factory: the KnowledgeService selected by configuration.

auto  -> files when knowledge-layer artefacts exist, else mock
files -> FilesKnowledgeBackend (deterministic claims/procedures/chunks from disk; no Neo4j)
mock  -> fixtures
neo4j -> Neo4jKnowledgeBackend (future; see docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md)
"""
from __future__ import annotations

import logging

from workbench.config import WorkbenchConfig
from workbench.services.backends.composite import CompositeKnowledgeService

logger = logging.getLogger(__name__)


def build_knowledge_service(cfg: WorkbenchConfig) -> CompositeKnowledgeService:
    backend = cfg.resolve_backend()
    if backend == "neo4j":
        from workbench.services.backends.neo4j_backend import Neo4jKnowledgeBackend

        primary = Neo4jKnowledgeBackend(cfg)
    elif backend == "files":
        from workbench.services.backends.files_backend import FilesKnowledgeBackend

        primary = FilesKnowledgeBackend(cfg)
    else:
        from workbench.services.backends.mock_backend import MockKnowledgeBackend

        primary = MockKnowledgeBackend(cfg.paths.fixtures_dir)
    logger.info("knowledge backend: %s", backend)
    return CompositeKnowledgeService(primary)

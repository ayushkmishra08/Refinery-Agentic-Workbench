"""Factory for the KnowledgeService selected by config."""
from workbench.config import WorkbenchConfig
from workbench.services.protocols import KnowledgeService


def build_knowledge_service(cfg: WorkbenchConfig) -> KnowledgeService:
    if cfg.knowledge_backend == "neo4j":
        from workbench.services.backends.neo4j_backend import Neo4jKnowledgeBackend
        return Neo4jKnowledgeBackend(cfg.knowledge_layer)
    from workbench.services.backends.mock_backend import MockKnowledgeBackend
    return MockKnowledgeBackend(cfg.paths.fixtures_dir)

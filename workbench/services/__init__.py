"""Shared Infrastructure & Knowledge Layer row of the diagram.

Everything the agents know about the world enters through ``KnowledgeService``.
``build_knowledge_service`` (knowledge.py) picks a backend from ``RWB_KNOWLEDGE_BACKEND``
(default ``auto``: ``files`` when knowledge-layer artefacts exist, else ``mock``):
    backends/files_backend.py  -> the knowledge layer's on-disk artefacts, indexed by services/index/
    backends/mock_backend.py   -> JSON fixtures in workbench/fixtures/
    backends/composite.py      -> a primary backend plus per-session uploads
    backends/neo4j_backend.py  -> stub; raises NotImplementedError (integration pending)
With the vault on (``RWB_VAULT=on``) the files backend starts empty and sealed branches are
decrypted into it per session (workbench/security/vault_backend.py).
"""

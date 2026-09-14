"""Shared Infrastructure & Knowledge Layer row of the diagram.

Everything the agents know about the world enters through ``KnowledgeService``.
Two backends implement the same protocol:
    backends/neo4j_backend.py  -> live graph + vector index built by ``src``
    backends/mock_backend.py   -> JSON fixtures in workbench/fixtures/ (use until extraction is done)
"""

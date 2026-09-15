"""Read-only protocol between the agents and any knowledge backend.

Backends: FilesKnowledgeBackend (knowledge-layer artefacts on disk, no Neo4j),
SessionDocumentsBackend (documents uploaded during a session), CompositeKnowledgeService
(several backends), MockKnowledgeBackend (fixtures), Neo4jKnowledgeBackend (future; see
docs/HANDOFF_KNOWLEDGE_LAYER_INTEGRATION.md).

Agents never import a backend; they receive a KnowledgeService.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from workbench.core.knowledge import (
    ChunkRecord,
    ClaimRecord,
    ConflictRecord,
    CrossReferenceRecord,
    DocumentInfo,
    DocumentReferenceRecord,
    EntityRecord,
    GlossaryRecord,
    ProcedureRecord,
    RelationRecord,
    SectionRecord,
    StandingInstructionRecord,
)


@runtime_checkable
class KnowledgeService(Protocol):
    name: str

    # documents -------------------------------------------------------------
    def documents(self) -> list[DocumentInfo]: ...
    def document_profile(self, document_id: str) -> dict: ...

    # entities --------------------------------------------------------------
    def resolve_entity(self, mention: str, limit: int = 5) -> list[EntityRecord]:
        """Tag / alias / name / fuzzy resolution, best first."""
    def get_entity(self, entity_uid: str) -> EntityRecord | None: ...
    def search_entities(self, query: str, entity_type: str | None = None, limit: int = 10) -> list[EntityRecord]: ...

    # claims ----------------------------------------------------------------
    def entity_claims(self, entity_uid: str, predicate: str | None = None, context: dict | None = None) -> list[ClaimRecord]:
        """Claims about one entity; ``context`` filters by parameter_role/location/scenario/operating_mode/pressure_basis."""
    def search_claims(self, subject: str | None = None, predicate: str | None = None, scenario: str | None = None,
                      text: str | None = None, limit: int = 50) -> list[ClaimRecord]: ...
    def conflicts_for(self, entity_uid: str, predicate: str | None = None) -> list[ConflictRecord]: ...

    # relationships ---------------------------------------------------------
    def entity_neighbors(self, entity_uid: str, rel_types: list[str] | None = None, direction: str = "both",
                         hops: int = 1) -> list[RelationRecord]: ...

    # procedures ------------------------------------------------------------
    def procedures(self, entity_uid: str | None = None, query: str | None = None, procedure_type: str | None = None,
                   limit: int = 10) -> list[ProcedureRecord]: ...
    def get_procedure(self, procedure_id: str) -> ProcedureRecord | None: ...

    # text ------------------------------------------------------------------
    def search_chunks(self, query: str, k: int = 8, chunk_types: list[str] | None = None,
                      chapters: list[int] | None = None, document_ids: list[str] | None = None,
                      entity_uids: list[str] | None = None, rerank: bool = False) -> list[ChunkRecord]: ...
    def get_chunk(self, chunk_id: str) -> ChunkRecord | None: ...
    def chunks_for_entity(self, entity_uid: str, limit: int = 20) -> list[ChunkRecord]: ...
    def sections(self, query: str, limit: int = 10) -> list[SectionRecord]: ...

    # document structure ----------------------------------------------------
    def glossary(self, term: str) -> list[GlossaryRecord]: ...
    def document_references(self, query: str | None = None) -> list[DocumentReferenceRecord]: ...
    def standing_instructions(self, query: str | None = None) -> list[StandingInstructionRecord]: ...
    def cross_references(self, section_query: str | None = None) -> list[CrossReferenceRecord]: ...

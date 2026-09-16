"""Phase 2 output: the Engineering Context Package handed to planning and execution.

Built by services/context_builder.py along the cheapest retrieval route for the task type.
Agents read from it first and call the KnowledgeService only for what is missing.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from workbench.core.knowledge import (
    ChunkRecord,
    ClaimRecord,
    ConflictRecord,
    CrossReferenceRecord,
    DocumentReferenceRecord,
    EntityRecord,
    GlossaryRecord,
    ProcedureRecord,
    RelationRecord,
    SectionRecord,
    StandingInstructionRecord,
)


class ContextPackage(BaseModel):
    route: str = "none"                                   # claims | graph | proc | hybrid | none
    entities: list[EntityRecord] = Field(default_factory=list)
    entity_groups: dict[str, list[str]] = Field(default_factory=dict, description="primary uid -> uids that share the same name (tag twins)")
    claims: list[ClaimRecord] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    procedures: list[ProcedureRecord] = Field(default_factory=list)
    chunks: list[ChunkRecord] = Field(default_factory=list)
    sections: list[SectionRecord] = Field(default_factory=list)
    glossary: list[GlossaryRecord] = Field(default_factory=list)
    standing_instructions: list[StandingInstructionRecord] = Field(default_factory=list)
    document_references: list[DocumentReferenceRecord] = Field(default_factory=list)
    cross_references: list[CrossReferenceRecord] = Field(default_factory=list)
    entity_counts: dict[str, int] = Field(default_factory=dict, description="Entities per equipment class, for inventory / survey answers")
    gaps: list[str] = Field(default_factory=list)          # evidence requirements not met
    notes: list[str] = Field(default_factory=list)         # retrieval decisions, for the audit trail
    timing_ms: int = 0

    def claims_for(self, entity_uid: str) -> list[ClaimRecord]:
        uids = {entity_uid, *self.entity_groups.get(entity_uid, [])}
        return [c for c in self.claims if c.subject_uid in uids]

    def is_empty(self) -> bool:
        return not (self.claims or self.relations or self.procedures or self.chunks or self.sections)

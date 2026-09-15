"""Typed records returned by every KnowledgeService backend.

They mirror the node/relationship properties the knowledge layer writes to Neo4j
(Entity, Claim, Procedure, ProcedureStep, Chunk, Section, DocumentReference,
StandingInstruction) so the Neo4j backend is a straight mapping.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentInfo(BaseModel):
    document_id: str
    title: str = ""
    document_type: str = ""
    revision: str | None = None
    effective_date: str | None = None
    unit: str | None = None
    total_pages: int | None = None
    authority_rank: int = Field(default=50, description="Higher = more authoritative (specification 80, operating manual 60, upload 30)")
    origin: str = "knowledge_layer"      # knowledge_layer | upload


class EntityRecord(BaseModel):
    entity_uid: str
    name: str
    canonical_tag: str | None = None
    entity_type: str | None = None       # Pump, Column, Heater, Vessel, Exchanger, Instrument, ControlValve, ...
    aliases: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    description: str | None = None
    mention_count: int = 0
    pages: list[int] = Field(default_factory=list)


class ClaimRecord(BaseModel):
    claim_id: str
    subject_uid: str | None = None
    subject: str
    predicate: str                       # flow_rate, pressure, temperature, design_pressure, ...
    value: str
    numeric_value: float | None = None
    unit: str | None = None
    parameter_role: str | None = None    # design, rated, normal, minimum, maximum, trip, alarm, ...
    location: str | None = None          # suction, discharge, inlet, outlet, top, bottom, ...
    operating_mode: str | None = None
    scenario: str | None = None
    pressure_basis: str | None = None    # absolute | gauge
    temporal_status: str | None = None   # current | historical | design | defunct
    qualifier: str | None = None
    context_key: str = ""
    document_id: str
    page: int | None = None
    chunk_id: str | None = None
    section_path: str | None = None
    evidence: str = ""
    source: str = "table"                # table | specification | prose | rule | llm_pass1 | llm_pass2
    revision: str | None = None
    confidence: float = 1.0


class RelationRecord(BaseModel):
    source_uid: str
    source_name: str
    target_uid: str
    target_name: str
    rel_type: str                        # FEEDS, SUCTION_FROM, DISCHARGES_TO, PART_OF, CONTROLLED_BY, MONITORS, ...
    document_id: str
    page: int | None = None
    chunk_id: str | None = None
    evidence: str = ""
    source: str = "rule"
    confidence: float = 1.0


class StepRecord(BaseModel):
    sequence: int
    text: str
    page: int | None = None
    element_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class ProcedureRecord(BaseModel):
    procedure_id: str
    title: str
    procedure_type: str                  # startup, shutdown, changeover, emergency, upset_response, ...
    document_id: str
    chapter_number: int | None = None
    section_id: str | None = None
    section_path: str = ""
    page_start: int | None = None
    page_end: int | None = None
    applies_to: list[str] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    score: float = 0.0


class ChunkRecord(BaseModel):
    chunk_id: str
    document_id: str
    chunk_type: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    section_path: str = ""
    section_id: str | None = None
    chapter_number: int | None = None
    procedure_ids: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    score: float = 0.0
    match_reason: str = ""               # keyword | vector | hybrid | graph


class SectionRecord(BaseModel):
    section_id: str
    title: str
    path: str
    level: int | None = None
    chapter_number: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    document_id: str


class ConflictRecord(BaseModel):
    subject: str
    predicate: str
    context_key: str
    claims: list[ClaimRecord]
    status: str                          # potential_conflict | corroborated


class GlossaryRecord(BaseModel):
    term: str
    meaning: str
    abbreviation: str | None = None
    aliases: list[str] = Field(default_factory=list)
    page: int | None = None
    document_id: str


class DocumentReferenceRecord(BaseModel):
    reference_text: str
    document_type: str = ""
    document_number: str = ""
    title: str = ""
    page: int | None = None
    evidence: str = ""
    present_in_corpus: bool = False
    document_id: str


class StandingInstructionRecord(BaseModel):
    number: str
    title: str
    issue_date: str | None = None
    status: str | None = None
    incorporated_in_chapter: str | None = None
    remark: str | None = None
    page: int | None = None
    document_id: str


class CrossReferenceRecord(BaseModel):
    source_section: str
    source_page: int | None = None
    target_kind: str
    target_number: str
    evidence: str = ""
    document_id: str

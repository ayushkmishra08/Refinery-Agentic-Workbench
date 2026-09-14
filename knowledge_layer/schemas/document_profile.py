"""Pydantic schema for document profile (document orientation phase).

Before extracting hundreds of pages, the system must understand the document.
The document profile captures the document's identity, structure, terminology,
and conventions discovered from the first meaningful pages.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    """Types of refinery engineering documents."""
    OPERATING_MANUAL = "operating_manual"
    SOP = "sop"
    STANDING_INSTRUCTION = "standing_instruction"
    SAFETY_MANUAL = "safety_manual"
    EMERGENCY_PROCEDURE = "emergency_procedure"
    EQUIPMENT_MANUAL = "equipment_manual"
    VENDOR_MANUAL = "vendor_manual"
    EQUIPMENT_DATASHEET = "equipment_datasheet"
    MAINTENANCE_MANUAL = "maintenance_manual"
    COMMISSIONING_DOCUMENT = "commissioning_document"
    PROCESS_DESCRIPTION = "process_description"
    INSTRUMENTATION_DOCUMENT = "instrumentation_document"
    PFD = "pfd"
    PID = "pid"
    CHEMICAL_SAFETY_DOCUMENT = "chemical_safety_document"
    UTILITY_DOCUMENT = "utility_document"
    INSPECTION_DOCUMENT = "inspection_document"
    CORROSION_DOCUMENT = "corrosion_document"
    UNKNOWN = "unknown"


class ChapterInfo(BaseModel):
    """A chapter or major section in the document."""
    number: str = Field(description="Chapter number as it appears (e.g., '3', '3.2', 'A')")
    title: str
    page_start: int | None = None
    page_end: int | None = None
    revision: str = Field(default="")
    revision_date: str = Field(default="")
    source: str = Field(default="heading", description="toc | page_header | heading")
    is_administrative: bool = Field(default=False)


class CrossReference(BaseModel):
    """An explicit in-document reference ('refer Chapter 34', 'see Section 6.1.6.4')."""
    source_section: str = Field(default="", description="Section path of the referring text")
    source_page: int = 0
    target_kind: str = Field(description="chapter | section | annexure | appendix")
    target_number: str
    evidence: str = Field(default="")


class StandingInstruction(BaseModel):
    """A standing instruction listed in the document-control chapter."""
    number: str
    title: str
    issue_date: str = Field(default="")
    status: str = Field(default="", description="in_use | cancelled | expired | incorporated")
    incorporated_in_chapter: str = Field(default="")
    remark: str = Field(default="")
    page: int = 0


class SectionInfo(BaseModel):
    """A section within a chapter."""
    number: str
    title: str
    page: int | None = None
    parent_chapter: str | None = None


class AbbreviationEntry(BaseModel):
    """An abbreviation discovered from the document."""
    abbreviation: str
    full_form: str
    evidence: str = Field(description="Source text where this was found")
    page: int | None = None
    source: str = Field(default="document", description="How discovered: 'abbreviation_table', 'inline', 'llm'")


class EquipmentNamingConvention(BaseModel):
    """A naming convention for equipment tags."""
    pattern: str = Field(description="Regex pattern, e.g., 'P-\\d{3}[A-Z]?'")
    description: str = Field(description="What this pattern represents, e.g., 'Pumps'")
    examples: list[str] = Field(default_factory=list)


class ReferencedDocument(BaseModel):
    """A document explicitly referenced by this document."""
    reference_text: str = Field(description="How it's referenced in the source")
    document_type: str = Field(default="unknown")
    document_number: str = Field(default="")
    title: str = Field(default="")
    evidence: str = Field(default="")
    page: int | None = None
    present_in_corpus: bool = Field(default=False)


class DocumentProfile(BaseModel):
    """Document orientation profile built before full extraction.

    Created from the first meaningful pages (title page, TOC, abbreviation
    sections, first technical sections) to establish the document's identity,
    structure, and terminology before processing the rest.
    """
    document_id: str
    source_filename: str

    # Identity
    title: str = Field(default="")
    document_type: DocumentType = DocumentType.UNKNOWN
    document_number: str = Field(default="")
    revision: str = Field(default="")
    status: str = Field(default="", description="e.g., 'Approved', 'Draft', 'For Review'")
    effective_date: str = Field(default="")

    # Plant context
    refinery: str = Field(default="")
    plant: str = Field(default="")
    unit: str = Field(default="")
    block: str = Field(default="")
    area: str = Field(default="")

    # Structure
    chapters: list[ChapterInfo] = Field(default_factory=list)
    sections: list[SectionInfo] = Field(default_factory=list)
    total_pages: int = 0

    # Terminology
    abbreviations: list[AbbreviationEntry] = Field(default_factory=list)
    equipment_naming_conventions: list[EquipmentNamingConvention] = Field(default_factory=list)
    process_terminology: list[str] = Field(
        default_factory=list,
        description="Process-specific terms discovered (e.g., 'crude topping', 'vacuum gas oil')",
    )
    safety_terminology: list[str] = Field(
        default_factory=list,
        description="Safety-specific terms (e.g., 'LOTO', 'hot work permit')",
    )

    # References
    referenced_documents: list[ReferencedDocument] = Field(default_factory=list)
    cross_references: list[CrossReference] = Field(default_factory=list)
    standing_instructions: list[StandingInstruction] = Field(default_factory=list)

    # Table categories discovered
    table_categories_found: list[str] = Field(default_factory=list)

    # Important recurring concepts
    key_concepts: list[str] = Field(default_factory=list)

    # Processing metadata
    pages_analyzed: int = Field(default=0, description="How many pages were used for orientation")
    profile_timestamp: str = Field(default="")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

"""Pydantic schema for Layer 2: Normalized Document (cleaned, filtered representation).

This layer applies deterministic filtering to remove boilerplate, repeated headers/footers,
approval blocks, TOC entries, and decorative elements. The original parsed representation
is never modified — this is a separate cleaned view for semantic processing.

Every element retains its original page/position for provenance tracing.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ContentType(str, Enum):
    """Semantic content type for normalized elements."""
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    TABLE = "table"
    PROCEDURE_STEP = "procedure_step"
    EQUATION = "equation"
    FIGURE_CAPTION = "figure_caption"
    SAFETY_NOTE = "safety_note"
    WARNING = "warning"
    CAUTION = "caution"
    NOTE = "note"


class FilterDecision(str, Enum):
    """Why an element was kept or filtered."""
    KEEP = "keep"
    BOILERPLATE = "boilerplate"
    REPEATED_HEADER = "repeated_header"
    REPEATED_FOOTER = "repeated_footer"
    APPROVAL_BLOCK = "approval_block"
    SIGNATURE_BLOCK = "signature_block"
    TOC = "toc"
    ADMINISTRATIVE = "administrative"
    DECORATIVE = "decorative"
    DUPLICATE = "duplicate"
    EMPTY = "empty"
    FIGURE_PLACEHOLDER = "figure_placeholder"   # "[Figure on page N]" markers (image kept in parsed layer)
    RUNNING_TITLE = "running_title"             # chapter title repeated as a page heading


class ProcedureType(str, Enum):
    """Kind of ordered instruction block found in the document."""
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    EMERGENCY = "emergency"
    CHANGEOVER = "changeover"
    COMMISSIONING = "commissioning"
    ISOLATION = "isolation"
    SAMPLING = "sampling"
    UPSET_RESPONSE = "upset_response"
    SAFETY = "safety"
    MAINTENANCE = "maintenance"
    NORMAL_OPERATION = "normal_operation"
    CHECKLIST = "checklist"
    GENERIC = "procedure"


class NormalizedElement(BaseModel):
    """A content element that passed filtering, with provenance."""
    element_id: str = Field(description="Same ID as in parsed document for traceability")
    content_type: ContentType
    content: str = Field(description="Text content")
    page: int = Field(description="Original page number (1-indexed)")
    position_in_page: int = Field(description="Original position within page")
    section_path: str = Field(
        default="",
        description="Full section path, e.g. 'Chapter 3 > Section 3.2 > Operating Conditions'",
    )
    heading_level: int | None = Field(default=None)
    parent_heading: str | None = Field(default=None)
    filter_decision: FilterDecision = Field(
        default=FilterDecision.KEEP,
        description="Why this element was kept (always KEEP for elements in the normalized doc)",
    )
    table_id: str | None = Field(default=None, description="Reference to table if this is a table element")
    is_engineering_content: bool = Field(
        default=True,
        description="Whether this element contains engineering-relevant content",
    )
    # Structure (filled by the normalizer from the TOC / page-header boxes)
    chapter_number: int | None = Field(default=None, description="Chapter this element belongs to")
    section_id: str | None = Field(default=None, description="Enclosing SectionNode id")
    # Procedures (filled by src.structure.detect_procedures)
    procedure_id: str | None = Field(default=None, description="Procedure this element belongs to, if any")
    step_number: int | None = Field(default=None, description="1-based step order; 0 = the procedure's title line")
    list_enumerated: bool | None = Field(default=None)
    list_marker: str | None = Field(default=None)


class FilteredElement(BaseModel):
    """Record of an element that was removed during normalization, for audit."""
    element_id: str
    page: int
    filter_decision: FilterDecision
    reason: str = Field(default="", description="Human-readable explanation of why it was filtered")
    content_preview: str = Field(default="", description="First 100 chars of content for review")


class SectionNode(BaseModel):
    """A node in the document's section hierarchy."""
    section_id: str
    title: str
    level: int = Field(description="Heading level (1=chapter, 2=section, etc.)")
    page_start: int
    page_end: int | None = Field(default=None)
    parent_section_id: str | None = Field(default=None)
    children: list[str] = Field(default_factory=list, description="Child section IDs")
    element_count: int = Field(default=0, description="Number of content elements in this section")
    chapter_number: int | None = Field(default=None)
    number: str = Field(default="", description="Section number as printed ('6.1.2'), if any")
    path: str = Field(default="", description="Full section path")


class ChapterNode(BaseModel):
    """A chapter of the document, reconstructed from the table of contents / page headers."""
    number: int
    title: str
    page_start: int
    page_end: int | None = Field(default=None)
    revision: str = Field(default="")
    revision_date: str = Field(default="")
    source: str = Field(default="toc", description="toc | page_header | heading")
    is_administrative: bool = Field(
        default=False,
        description="Document-control chapter (preface, TOC, revisions, copy holders): not engineering knowledge",
    )
    section_ids: list[str] = Field(default_factory=list)


class ProcedureStepRecord(BaseModel):
    """One ordered step of a detected procedure."""
    sequence: int
    text: str
    page: int
    element_id: str
    tags: list[str] = Field(default_factory=list, description="Canonical asset tags named in the step")


class ProcedureBlock(BaseModel):
    """An ordered block of instructions detected deterministically in the document."""
    procedure_id: str
    title: str
    procedure_type: ProcedureType = ProcedureType.GENERIC
    chapter_number: int | None = None
    section_id: str | None = None
    section_path: str = ""
    page_start: int = 0
    page_end: int = 0
    label_element_id: str | None = Field(default=None, description="Element holding the title line, if any")
    steps: list[ProcedureStepRecord] = Field(default_factory=list)
    applies_to: list[str] = Field(default_factory=list, description="Asset tags named in the title/steps")
    detection_reasons: list[str] = Field(default_factory=list)


class NormalizationStats(BaseModel):
    """Statistics from the normalization process."""
    total_elements: int = Field(default=0, description="Total elements in parsed document")
    kept_elements: int = Field(default=0)
    filtered_elements: int = Field(default=0)
    boilerplate_patterns_found: int = Field(default=0)
    repeated_headers_found: int = Field(default=0)
    repeated_footers_found: int = Field(default=0)
    approval_blocks_found: int = Field(default=0)
    toc_entries_found: int = Field(default=0)
    decorative_elements_found: int = Field(default=0)
    duplicate_elements_found: int = Field(default=0)
    empty_elements_found: int = Field(default=0)
    engineering_tables_kept: int = Field(default=0)
    non_engineering_tables_filtered: int = Field(default=0)
    figure_placeholders_found: int = Field(default=0)
    running_titles_found: int = Field(default=0)
    administrative_found: int = Field(default=0)
    procedures_found: int = Field(default=0)
    procedure_steps_found: int = Field(default=0)
    chapters_from_toc: int = Field(default=0)


class NormalizedDocument(BaseModel):
    """Layer 2: Cleaned, filtered document representation.

    Contains only meaningful content for semantic processing.
    The original parsed representation remains available in Layer 1.
    Document order (page → section → heading → element) is preserved exactly.
    """
    document_id: str = Field(description="Same document_id as parsed document")
    source_filename: str
    total_pages: int = Field(default=0)

    # Chapter structure (from the TOC table / page-header boxes) and section hierarchy
    chapters: list[ChapterNode] = Field(default_factory=list)
    sections: list[SectionNode] = Field(
        default_factory=list,
        description="Document section tree (chapters, sections, subsections)",
    )
    procedures: list[ProcedureBlock] = Field(
        default_factory=list,
        description="Ordered instruction blocks detected deterministically",
    )

    # Kept content (in document order)
    elements: list[NormalizedElement] = Field(
        default_factory=list,
        description="Content elements that passed filtering, in original document order",
    )

    # Audit trail of what was removed
    filtered_elements: list[FilteredElement] = Field(
        default_factory=list,
        description="Elements removed during normalization, with reasons",
    )

    # Statistics
    stats: NormalizationStats = Field(default_factory=NormalizationStats)

    normalization_timestamp: str = Field(default="")
    normalization_duration_seconds: float = Field(default=0.0)

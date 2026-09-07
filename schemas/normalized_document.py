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


class NormalizedDocument(BaseModel):
    """Layer 2: Cleaned, filtered document representation.

    Contains only meaningful content for semantic processing.
    The original parsed representation remains available in Layer 1.
    Document order (page → section → heading → element) is preserved exactly.
    """
    document_id: str = Field(description="Same document_id as parsed document")
    source_filename: str
    total_pages: int = Field(default=0)

    # Section hierarchy
    sections: list[SectionNode] = Field(
        default_factory=list,
        description="Document section tree (chapters, sections, subsections)",
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

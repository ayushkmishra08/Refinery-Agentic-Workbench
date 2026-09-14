"""Pydantic schema for Layer 1: Parsed Document (faithful Docling output).

This is the raw parser output wrapper. It preserves everything Docling produces
including page order, reading order, element order, table structure with
row/col/span, bounding boxes, provenance, and parser metadata. Nothing is
filtered or modified at this layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ElementType(str, Enum):
    """Element types produced by the parser."""
    TEXT = "text"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    EQUATION = "equation"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    CODE = "code"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    """Bounding box in PDF points. coord_origin is 'TOPLEFT' or 'BOTTOMLEFT'."""
    l: float
    t: float
    r: float
    b: float
    coord_origin: str = Field(default="BOTTOMLEFT")


class Provenance(BaseModel):
    """Source location of an element as reported by Docling."""
    page_no: int
    bbox: BoundingBox | None = None
    charspan: tuple[int, int] | None = Field(
        default=None, description="Character span within the element text covered by this prov",
    )


class ParsedTableCell(BaseModel):
    """A single cell in a parsed table."""
    row: int = Field(description="Zero-indexed row position")
    col: int = Field(description="Zero-indexed column position")
    rowspan: int = Field(default=1, description="Number of rows this cell spans")
    colspan: int = Field(default=1, description="Number of columns this cell spans")
    content: str = Field(default="", description="Text content of the cell")
    is_header: bool = Field(default=False, description="Whether this cell is a column header cell")
    is_row_header: bool = Field(default=False, description="Whether this cell is a row header cell")
    is_row_section: bool = Field(default=False, description="Whether this cell is a row section cell")
    bbox: BoundingBox | None = Field(default=None, description="Cell bounding box if available")


class ParsedTable(BaseModel):
    """A table extracted by the parser with full structure."""
    table_id: str = Field(description="Unique table identifier within the document")
    page: int = Field(description="Page number where the table appears")
    num_rows: int = Field(default=0)
    num_cols: int = Field(default=0)
    cells: list[ParsedTableCell] = Field(default_factory=list)
    grid: list[list[str]] = Field(
        default_factory=list,
        description="Dense num_rows x num_cols text grid with merged cells propagated",
    )
    caption: str | None = Field(default=None, description="Table caption if detected")
    raw_markdown: str | None = Field(default=None, description="Markdown representation")
    bbox: BoundingBox | None = Field(default=None)
    provenance: list[Provenance] = Field(default_factory=list)
    docling_ref: str | None = Field(default=None, description="Docling self_ref, e.g. #/tables/3")
    reading_order: int | None = Field(default=None, description="Global reading-order index")


class ParsedElement(BaseModel):
    """A single content element from the parser."""
    element_id: str = Field(description="Unique element identifier")
    element_type: ElementType
    docling_label: str = Field(default="", description="Original Docling label")
    content: str = Field(default="", description="Text content")
    page: int = Field(description="Page number (1-indexed)")
    position_in_page: int = Field(description="Order within the page (0-indexed)")
    reading_order: int = Field(default=0, description="Global reading-order index across the document")
    heading_level: int | None = Field(default=None, description="Heading level (1-6) if heading")
    parent_heading: str | None = Field(default=None, description="Parent heading text if available")
    table: ParsedTable | None = Field(default=None, description="Table data if element is a table")
    bounding_box: BoundingBox | None = Field(default=None)
    provenance: list[Provenance] = Field(default_factory=list)
    content_layer: str = Field(default="body", description="body or furniture")
    docling_ref: str | None = Field(default=None)
    parent_ref: str | None = Field(default=None)
    tree_depth: int = Field(default=0, description="Depth in the Docling body tree")
    # List item specifics
    list_group_ref: str | None = Field(default=None, description="Group ref of the enclosing list")
    list_enumerated: bool | None = Field(default=None)
    list_marker: str | None = Field(default=None)
    # Figure specifics
    image_path: str | None = Field(default=None, description="Relative path to the exported image")
    caption: str | None = Field(default=None, description="Caption text for figures/tables")


class ParsedPage(BaseModel):
    """A single page from the parsed document."""
    page_number: int = Field(description="1-indexed page number")
    width: float | None = Field(default=None)
    height: float | None = Field(default=None)
    elements: list[ParsedElement] = Field(default_factory=list)


class ParserConfig(BaseModel):
    """Records exactly how the parser was configured for reproducibility."""
    parser_name: str = Field(default="docling")
    parser_version: str = Field(default="")
    ocr_enabled: bool = Field(default=True)
    ocr_engine: str = Field(default="rapidocr")
    table_structure_mode: str = Field(default="accurate")
    page_window_size: int = Field(default=15)
    config_hash: str = Field(default="", description="Hash of full configuration for cache invalidation")


class ParsedDocument(BaseModel):
    """Layer 1: Faithful parser output.

    This preserves everything the parser produces without any filtering,
    normalization, or interpretation. The original page order, element order,
    and table structure are maintained exactly.
    """
    document_id: str = Field(description="Unique document identifier (derived from filename)")
    source_filename: str = Field(description="Original PDF filename")
    source_path: str = Field(description="Full path to source PDF")
    source_hash: str = Field(description="SHA-256 hash of the source PDF for integrity")
    total_pages: int = Field(default=0, description="Page count reported by the PDF")
    pages: list[ParsedPage] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list, description="All tables extracted")
    parser_config: ParserConfig = Field(default_factory=ParserConfig)
    parse_timestamp: str = Field(default="", description="ISO 8601 timestamp of parsing")
    parse_duration_seconds: float = Field(default=0.0)
    warnings: list[str] = Field(default_factory=list, description="Parser warnings")
    errors: list[str] = Field(default_factory=list, description="Parser errors (non-fatal)")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional parser metadata (Docling-specific fields, runtime environment)",
    )

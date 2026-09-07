"""Pydantic schema for table classification, structure, and normalization.

Tables are first-class engineering data sources. This schema preserves full
table structure (cells, rows, columns, spans) and classifies each table
to determine whether it enters semantic extraction.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TableClassification(str, Enum):
    """Classification of detected tables."""
    HEADER_TABLE = "header_table"
    FOOTER_TABLE = "footer_table"
    APPROVAL_TABLE = "approval_table"
    SIGNATURE_TABLE = "signature_table"
    TOC_TABLE = "toc_table"
    ADMINISTRATIVE_TABLE = "administrative_table"
    DECORATIVE_TABLE = "decorative_table"
    ABBREVIATION_TABLE = "abbreviation_table"
    EQUIPMENT_TABLE = "equipment_table"
    INSTRUMENT_TABLE = "instrument_table"
    MATERIAL_BALANCE_TABLE = "material_balance_table"
    OPERATING_LIMIT_TABLE = "operating_limit_table"
    PROCESS_DATA_TABLE = "process_data_table"
    SAFETY_TABLE = "safety_table"
    MAINTENANCE_TABLE = "maintenance_table"
    CHEMICAL_TABLE = "chemical_table"
    ENGINEERING_TABLE = "engineering_table"
    UNKNOWN_TABLE = "unknown_table"


# Tables that should enter semantic extraction
ENGINEERING_CLASSIFICATIONS = {
    TableClassification.ABBREVIATION_TABLE,
    TableClassification.EQUIPMENT_TABLE,
    TableClassification.INSTRUMENT_TABLE,
    TableClassification.MATERIAL_BALANCE_TABLE,
    TableClassification.OPERATING_LIMIT_TABLE,
    TableClassification.PROCESS_DATA_TABLE,
    TableClassification.SAFETY_TABLE,
    TableClassification.MAINTENANCE_TABLE,
    TableClassification.CHEMICAL_TABLE,
    TableClassification.ENGINEERING_TABLE,
}


class StructuredCell(BaseModel):
    """A table cell with full structure preserved."""
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    content: str = ""
    is_header: bool = False
    is_merged: bool = Field(default=False, description="True if this cell spans multiple rows/cols")
    original_content: str = Field(default="", description="Content before normalization")


class ColumnInfo(BaseModel):
    """Metadata about a table column."""
    index: int
    header: str = ""
    normalized_header: str = Field(default="", description="Lowercased, stripped, canonical form")
    data_type: str = Field(default="unknown", description="Detected type: numeric, text, tag, unit, mixed")
    unit: str | None = Field(default=None, description="Detected unit if numeric column")
    sample_values: list[str] = Field(default_factory=list, description="Up to 3 sample values")


class ClassifiedTable(BaseModel):
    """A table with classification and full structure."""
    table_id: str
    document_id: str
    page: int
    section: str = Field(default="", description="Section where the table appears")
    title: str | None = Field(default=None, description="Table title/caption if detected")
    classification: TableClassification = TableClassification.UNKNOWN_TABLE
    classification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    classification_reasons: list[str] = Field(
        default_factory=list,
        description="Reasons for the classification decision",
    )

    # Structure
    num_rows: int = 0
    num_cols: int = 0
    columns: list[ColumnInfo] = Field(default_factory=list)
    cells: list[StructuredCell] = Field(default_factory=list)

    # Normalization metadata
    is_repeated: bool = Field(default=False, description="True if this table repeats across pages")
    repeat_frequency: float = Field(default=0.0, description="Fraction of pages containing this table")
    column_signature: str = Field(default="", description="Normalized column header fingerprint")
    structural_hash: str = Field(default="", description="Hash of table structure for dedup")

    # For engineering tables: whether it enters extraction
    enters_extraction: bool = Field(default=False)

    def is_engineering(self) -> bool:
        """Whether this table classification is an engineering type."""
        return self.classification in ENGINEERING_CLASSIFICATIONS


class TableNormalizationResult(BaseModel):
    """Results from normalizing all tables in a document."""
    document_id: str
    total_tables: int = 0
    classified_tables: list[ClassifiedTable] = Field(default_factory=list)
    repeated_table_patterns: list[str] = Field(
        default_factory=list,
        description="Column signatures of tables detected as repeated page templates",
    )
    duplicate_pairs: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Pairs of table_ids that are duplicates",
    )
    classification_summary: dict[str, int] = Field(
        default_factory=dict,
        description="Count of tables by classification",
    )

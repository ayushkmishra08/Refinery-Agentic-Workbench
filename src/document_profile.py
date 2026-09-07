"""Document orientation phase — build a document profile before full extraction.

Two-pass approach:
  Pass 1 (deterministic): Extract abbreviation tables, title page metadata,
    chapter/section hierarchy from headings, equipment tag patterns via regex.
  Pass 2 (LLM-assisted): Send first ~20 pages to DeepSeek-R1 for structured
    document profile extraction with JSON schema constraint.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from schemas.document_profile import (
    AbbreviationEntry,
    ChapterInfo,
    DocumentProfile,
    DocumentType,
    EquipmentNamingConvention,
    ReferencedDocument,
    SectionInfo,
)
from schemas.normalized_document import NormalizedDocument, ContentType
from schemas.table_schema import ClassifiedTable, TableClassification, TableNormalizationResult
from src.config import PipelineConfig

logger = logging.getLogger(__name__)

# Equipment tag patterns commonly found in refinery documents
EQUIPMENT_PATTERNS = [
    (r"\bP-\d{2,4}[A-Z]?\b", "Pumps (P-xxx)"),
    (r"\bE-\d{2,4}[A-Z]?\b", "Heat Exchangers (E-xxx)"),
    (r"\bV-\d{2,4}[A-Z]?\b", "Vessels/Drums (V-xxx)"),
    (r"\bC-\d{2,4}[A-Z]?\b", "Columns (C-xxx)"),
    (r"\bT-\d{2,4}[A-Z]?\b", "Tanks (T-xxx)"),
    (r"\bF-\d{2,4}[A-Z]?\b", "Furnaces (F-xxx)"),
    (r"\bR-\d{2,4}[A-Z]?\b", "Reactors (R-xxx)"),
    (r"\bK-\d{2,4}[A-Z]?\b", "Compressors (K-xxx)"),
    (r"\bD-\d{2,4}[A-Z]?\b", "Drums (D-xxx)"),
    (r"\bH-\d{2,4}[A-Z]?\b", "Heaters (H-xxx)"),
]

# Document reference patterns
DOC_REF_PATTERNS = [
    (r"P&ID[\s-]*(?:No\.?\s*)?[\w.-]+", "P&ID"),
    (r"PFD[\s-]*(?:No\.?\s*)?[\w.-]+", "PFD"),
    (r"SOP[\s-]*(?:No\.?\s*)?[\w.-]+", "SOP"),
    (r"(?:Dwg|Drawing)[\s.-]*(?:No\.?\s*)?[\w.-]+", "Drawing"),
    (r"(?:Doc|Document)[\s.-]*(?:No\.?\s*)?[\w.-]+", "Document"),
]


class DocumentProfiler:
    """Builds a document profile from normalized content and classified tables."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def build_profile(
        self,
        normalized_doc: NormalizedDocument,
        table_results: TableNormalizationResult,
    ) -> DocumentProfile:
        """Build document profile using deterministic Pass 1.

        Pass 2 (LLM-assisted) is handled separately by the extractor
        when Ollama is available.

        Args:
            normalized_doc: Layer 2 normalized document.
            table_results: Table classification results.

        Returns:
            DocumentProfile with deterministically-extractable information.
        """
        doc_id = normalized_doc.document_id
        logger.info(f"Building document profile for {doc_id}")

        profile = DocumentProfile(
            document_id=doc_id,
            source_filename=normalized_doc.source_filename,
            total_pages=normalized_doc.total_pages,
        )

        # Pass 1a: Extract title and document type from first page
        self._extract_title_metadata(normalized_doc, profile)

        # Pass 1b: Build chapter/section hierarchy
        self._extract_structure(normalized_doc, profile)

        # Pass 1c: Extract abbreviations from abbreviation tables
        self._extract_abbreviations(table_results, profile)

        # Pass 1d: Detect equipment naming conventions
        self._detect_equipment_conventions(normalized_doc, profile)

        # Pass 1e: Extract document references
        self._extract_references(normalized_doc, profile)

        # Pass 1f: Detect table categories
        self._detect_table_categories(table_results, profile)

        # Set metadata
        profile.pages_analyzed = min(20, normalized_doc.total_pages)
        profile.profile_timestamp = datetime.now(timezone.utc).isoformat()
        profile.confidence = 0.6  # Deterministic pass only; LLM pass increases this

        # Save profile
        output_dir = self.config.paths.knowledge_dir / doc_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "document_profile.json"
        output_path.write_text(
            profile.model_dump_json(indent=2),
            encoding="utf-8",
        )

        logger.info(
            f"Profile built for {doc_id}: type={profile.document_type.value}, "
            f"{len(profile.chapters)} chapters, {len(profile.abbreviations)} abbreviations, "
            f"{len(profile.equipment_naming_conventions)} equipment patterns"
        )

        return profile

    def _extract_title_metadata(
        self, doc: NormalizedDocument, profile: DocumentProfile,
    ) -> None:
        """Extract title and document type from first few pages."""
        # Look at first 5 pages for title-like content
        first_elements = [
            e for e in doc.elements
            if e.page <= 5
        ]

        # First heading is likely the title
        for elem in first_elements:
            if elem.content_type == ContentType.HEADING and elem.heading_level == 1:
                profile.title = elem.content.strip()
                break

        if not profile.title:
            # Fallback: first heading of any level
            for elem in first_elements:
                if elem.content_type == ContentType.HEADING:
                    profile.title = elem.content.strip()
                    break

        # Detect document type from title and content
        title_lower = profile.title.lower()
        all_text = " ".join(e.content for e in first_elements).lower()

        type_keywords = {
            DocumentType.OPERATING_MANUAL: ["operating manual", "operation manual", "o&m manual"],
            DocumentType.SOP: ["standard operating procedure", "sop"],
            DocumentType.STANDING_INSTRUCTION: ["standing instruction"],
            DocumentType.SAFETY_MANUAL: ["safety manual", "safety document"],
            DocumentType.EMERGENCY_PROCEDURE: ["emergency procedure", "emergency response"],
            DocumentType.EQUIPMENT_MANUAL: ["equipment manual"],
            DocumentType.VENDOR_MANUAL: ["vendor manual", "vendor document"],
            DocumentType.EQUIPMENT_DATASHEET: ["datasheet", "data sheet", "equipment data"],
            DocumentType.MAINTENANCE_MANUAL: ["maintenance manual", "maintenance procedure"],
            DocumentType.COMMISSIONING_DOCUMENT: ["commissioning", "pre-commissioning"],
            DocumentType.PROCESS_DESCRIPTION: ["process description", "process flow"],
            DocumentType.PFD: ["process flow diagram", "pfd"],
            DocumentType.PID: ["piping and instrumentation", "p&id"],
            DocumentType.CHEMICAL_SAFETY_DOCUMENT: ["msds", "sds", "chemical safety"],
            DocumentType.UTILITY_DOCUMENT: ["utility", "utilities"],
            DocumentType.INSPECTION_DOCUMENT: ["inspection"],
            DocumentType.CORROSION_DOCUMENT: ["corrosion"],
        }

        for doc_type, keywords in type_keywords.items():
            for kw in keywords:
                if kw in title_lower or kw in all_text[:2000]:
                    profile.document_type = doc_type
                    break
            if profile.document_type != DocumentType.UNKNOWN:
                break

        # Extract plant/unit info from first pages
        plant_patterns = [
            (r"(?:refinery|complex)[\s:]+([^\n,]+)", "refinery"),
            (r"(?:plant|unit)[\s:]+([^\n,]+)", "plant"),
            (r"(?:block|area)[\s:]+([^\n,]+)", "area"),
        ]
        for pattern, field in plant_patterns:
            match = re.search(pattern, all_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()[:100]
                setattr(profile, field, value)

    def _extract_structure(
        self, doc: NormalizedDocument, profile: DocumentProfile,
    ) -> None:
        """Build chapter/section hierarchy from headings."""
        for elem in doc.elements:
            if elem.content_type != ContentType.HEADING:
                continue

            level = elem.heading_level or 2
            title = elem.content.strip()

            # Try to extract chapter/section number
            number_match = re.match(r"^([\d.]+)\s+", title)
            number = number_match.group(1) if number_match else ""

            if level == 1:
                profile.chapters.append(ChapterInfo(
                    number=number,
                    title=title,
                    page_start=elem.page,
                ))
            else:
                parent = profile.chapters[-1].number if profile.chapters else None
                profile.sections.append(SectionInfo(
                    number=number,
                    title=title,
                    page=elem.page,
                    parent_chapter=parent,
                ))

    def _extract_abbreviations(
        self, table_results: TableNormalizationResult, profile: DocumentProfile,
    ) -> None:
        """Extract abbreviations from tables classified as ABBREVIATION_TABLE."""
        for table in table_results.classified_tables:
            if table.classification != TableClassification.ABBREVIATION_TABLE:
                continue

            # Find which columns are abbreviation and full form
            abbr_col = None
            full_col = None
            for col in table.columns:
                header = col.normalized_header
                if any(kw in header for kw in ("abbreviation", "acronym", "short", "symbol")):
                    abbr_col = col.index
                elif any(kw in header for kw in ("full", "expansion", "meaning", "description", "definition")):
                    full_col = col.index

            if abbr_col is None and full_col is None:
                # Assume first col = abbreviation, second col = full form
                if len(table.columns) >= 2:
                    abbr_col = 0
                    full_col = 1

            if abbr_col is None or full_col is None:
                continue

            # Build a row-indexed lookup
            rows: dict[int, dict[int, str]] = {}
            for cell in table.cells:
                if cell.is_header:
                    continue
                if cell.row not in rows:
                    rows[cell.row] = {}
                rows[cell.row][cell.col] = cell.content

            for row_idx, row_data in rows.items():
                abbr = row_data.get(abbr_col, "").strip()
                full = row_data.get(full_col, "").strip()
                if abbr and full:
                    profile.abbreviations.append(AbbreviationEntry(
                        abbreviation=abbr,
                        full_form=full,
                        evidence=f"{abbr} = {full}",
                        page=table.page,
                        source="abbreviation_table",
                    ))

    def _detect_equipment_conventions(
        self, doc: NormalizedDocument, profile: DocumentProfile,
    ) -> None:
        """Detect equipment naming conventions from document content."""
        all_text = " ".join(e.content for e in doc.elements if e.page <= 30)

        for pattern, description in EQUIPMENT_PATTERNS:
            matches = re.findall(pattern, all_text)
            if len(matches) >= 2:  # Need at least 2 instances to confirm a convention
                unique_examples = list(set(matches))[:5]
                profile.equipment_naming_conventions.append(
                    EquipmentNamingConvention(
                        pattern=pattern,
                        description=description,
                        examples=unique_examples,
                    )
                )

    def _extract_references(
        self, doc: NormalizedDocument, profile: DocumentProfile,
    ) -> None:
        """Extract explicit document references."""
        all_text = " ".join(e.content for e in doc.elements)

        for pattern, ref_type in DOC_REF_PATTERNS:
            matches = re.findall(pattern, all_text)
            for match in set(matches):
                profile.referenced_documents.append(ReferencedDocument(
                    reference_text=match.strip(),
                    document_type=ref_type,
                    present_in_corpus=False,  # Will be updated later
                ))

    def _detect_table_categories(
        self, table_results: TableNormalizationResult, profile: DocumentProfile,
    ) -> None:
        """Record which table categories were found."""
        profile.table_categories_found = list(table_results.classification_summary.keys())

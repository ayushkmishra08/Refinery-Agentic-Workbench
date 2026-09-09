"""Table classification and normalization.

Every detected table is classified using deterministic heuristics before
any LLM involvement. Tables are classified into 18 types, and only
engineering tables enter semantic extraction.

Classification signals:
- Page frequency (repeated templates)
- Column header patterns
- Content patterns (equipment tags, units, chemical names)
- Table size and structure
- Surrounding section context
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter

from schemas.parsed_document import ParsedDocument, ParsedTable, ParsedTableCell
from schemas.table_schema import (
    ClassifiedTable,
    ColumnInfo,
    ENGINEERING_CLASSIFICATIONS,
    StructuredCell,
    TableClassification,
    TableNormalizationResult,
)
from src.config import PipelineConfig

logger = logging.getLogger(__name__)

# Regex patterns for classification signals
EQUIPMENT_TAG_PATTERN = re.compile(
    r"\b[A-Z]{1,3}-?\d{2,4}[A-Z]?\b"  # P-101, E-201A, V-301, TI-1234
)
INSTRUMENT_TAG_PATTERN = re.compile(
    r"\b[A-Z]{2,4}-?\d{3,5}\b"  # TIC-1001, FCV-2001, PAHL-3001
)
UNIT_PATTERN = re.compile(
    r"(?:kg/h|m3/h|m\u00b3/h|barg|bar(?:a|g)?|kPa|MPa|psi|kW|MW|HP|rpm|mm|t/h|l/h|\u00b0[CF])",
    re.IGNORECASE,
)
APPROVAL_PATTERN = re.compile(
    r"\b(approved|signature|signed|prepared|checked|verified|authorized|date)\b",
    re.IGNORECASE,
)
TOC_PATTERN = re.compile(
    r"\b(chapter|section|page|contents|index|appendix)\b",
    re.IGNORECASE,
)
SAFETY_PATTERN = re.compile(
    r"\b(hazard|risk|safety|ppe|permit|danger|warning|caution|emergency|msds|sds)\b",
    re.IGNORECASE,
)
MAINTENANCE_PATTERN = re.compile(
    r"\b(maintenance|inspection|interval|frequency|lubrication|overhaul|turnaround)\b",
    re.IGNORECASE,
)
CHEMICAL_PATTERN = re.compile(
    r"\b(chemical|reagent|dosage|concentration|treatment|additive|inhibitor)\b",
    re.IGNORECASE,
)


def _normalize_header(text: str) -> str:
    """Normalize a column header for comparison."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _column_signature(headers: list[str]) -> str:
    """Create a normalized fingerprint of column headers."""
    normalized = sorted(_normalize_header(h) for h in headers if h.strip())
    return "|".join(normalized)


def _structural_hash(table: ParsedTable) -> str:
    """Hash the structure (row/col counts, header pattern) of a table."""
    parts = [
        str(table.num_rows),
        str(table.num_cols),
    ]
    headers = [c.content for c in table.cells if c.is_header]
    parts.extend(sorted(_normalize_header(h) for h in headers))
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]


def _template_token(text: str) -> str:
    """Digit-insensitive, whitespace-normalized cell text used for page-template detection."""
    t = re.sub(r"\d+", "#", text.strip().lower())
    return re.sub(r"\s+", " ", t)


def detect_page_template_tables(
    parsed_doc: ParsedDocument,
    page_fraction: float = 0.5,
    cell_fraction: float = 0.6,
) -> set[str]:
    """Find tables that are page furniture (header/footer boxes repeated on most pages).

    A cell text (digits replaced by '#') that occurs on at least ``page_fraction``
    of all pages is a *template token*.  A table whose non-empty cells are mostly
    (>= ``cell_fraction``) template tokens is a page-template table.  This is
    robust to the chapter title / page number / revision cells changing.
    """
    total_pages = max(parsed_doc.total_pages, len(parsed_doc.pages), 1)
    if total_pages < 4 or not parsed_doc.tables:
        return set()

    token_pages: dict[str, set[int]] = {}
    for table in parsed_doc.tables:
        for cell in table.cells:
            tok = _template_token(cell.content)
            if len(tok) < 2:
                continue
            token_pages.setdefault(tok, set()).add(table.page)

    template_tokens = {
        tok for tok, pages in token_pages.items()
        if len(pages) / total_pages >= page_fraction and len(pages) >= 3
    }
    if not template_tokens:
        return set()

    # Longer template tokens are also matched as substrings: TableFormer sometimes
    # merges two header-box cells ("PLANT NO: PLANT NAME: Page No"), which would
    # otherwise look like a rare cell text.
    long_templates = [t for t in template_tokens if len(t) >= 6]

    def is_template_cell(tok: str) -> bool:
        if tok in template_tokens:
            return True
        return any(lt in tok for lt in long_templates)

    template_ids: set[str] = set()
    for table in parsed_doc.tables:
        toks = [_template_token(c.content) for c in table.cells if c.content.strip()]
        toks = [t for t in toks if len(t) >= 2]
        if not toks:
            continue
        hits = sum(1 for t in toks if is_template_cell(t))
        if hits / len(toks) >= cell_fraction and table.num_rows <= 8:
            template_ids.add(table.table_id)
    return template_ids


class TableClassifier:
    """Classifies and normalizes tables from parsed documents."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def classify_all(self, parsed_doc: ParsedDocument) -> TableNormalizationResult:
        """Classify all tables in a parsed document.

        Args:
            parsed_doc: Layer 1 parsed document.

        Returns:
            Classification results for all tables.
        """
        doc_id = parsed_doc.document_id
        total_pages = max(parsed_doc.total_pages, 1)

        logger.info(f"Classifying {len(parsed_doc.tables)} tables in {doc_id}")

        # Step 1: Compute structural hashes and page frequencies
        hash_counts: Counter[str] = Counter()
        hash_pages: dict[str, set[int]] = {}
        for table in parsed_doc.tables:
            h = _structural_hash(table)
            hash_counts[h] += 1
            if h not in hash_pages:
                hash_pages[h] = set()
            hash_pages[h].add(table.page)

        # Step 1b: Page-template (header/footer box) detection
        template_ids = detect_page_template_tables(parsed_doc)
        if template_ids:
            logger.info(f"{len(template_ids)} tables detected as repeating page-template boxes")

        # Step 2: Classify each table
        classified_tables: list[ClassifiedTable] = []
        classification_summary: Counter[str] = Counter()
        repeated_patterns: list[str] = []
        duplicate_pairs: list[tuple[str, str]] = []

        seen_signatures: dict[str, str] = {}  # signature -> first table_id

        for table in parsed_doc.tables:
            classified = self._classify_table(
                table, doc_id, total_pages, hash_counts, hash_pages,
                is_template=table.table_id in template_ids,
            )

            # Check for duplicates
            if classified.column_signature:
                if classified.column_signature in seen_signatures:
                    duplicate_pairs.append(
                        (seen_signatures[classified.column_signature], classified.table_id)
                    )
                else:
                    seen_signatures[classified.column_signature] = classified.table_id

            classified_tables.append(classified)
            classification_summary[classified.classification.value] += 1

            if classified.is_repeated and classified.column_signature:
                repeated_patterns.append(classified.column_signature)

        # Deduplicate repeated patterns
        repeated_patterns = list(set(repeated_patterns))

        result = TableNormalizationResult(
            document_id=doc_id,
            total_tables=len(parsed_doc.tables),
            classified_tables=classified_tables,
            repeated_table_patterns=repeated_patterns,
            duplicate_pairs=duplicate_pairs,
            classification_summary=dict(classification_summary),
        )

        logger.info(
            f"Table classification for {doc_id}: "
            + ", ".join(f"{k}={v}" for k, v in sorted(classification_summary.items()))
        )

        return result

    def _classify_table(
        self,
        table: ParsedTable,
        doc_id: str,
        total_pages: int,
        hash_counts: Counter,
        hash_pages: dict[str, set[int]],
        is_template: bool = False,
    ) -> ClassifiedTable:
        """Classify a single table."""
        s_hash = _structural_hash(table)
        reasons: list[str] = []

        # Extract headers and content
        headers = [c.content for c in table.cells if c.is_header]
        if not headers and table.grid:
            headers = list(table.grid[0])
        all_content = " ".join(c.content for c in table.cells)
        header_text = " ".join(headers).lower()

        # Build column info
        columns: list[ColumnInfo] = []
        cols_by_index: dict[int, list[str]] = {}
        for cell in table.cells:
            if cell.col not in cols_by_index:
                cols_by_index[cell.col] = []
            cols_by_index[cell.col].append(cell.content)

        header_row = [c for c in table.cells if c.is_header]
        header_by_col = {c.col: c.content for c in header_row}
        for col_idx in sorted(cols_by_index.keys()):
            values = cols_by_index[col_idx]
            header = header_by_col.get(col_idx) or (values[0] if values else "")
            col_info = ColumnInfo(
                index=col_idx,
                header=header,
                normalized_header=_normalize_header(header),
                sample_values=values[1:4],  # Up to 3 non-header values
            )
            columns.append(col_info)

        col_sig = _column_signature([c.header for c in columns])

        # Build structured cells
        structured_cells = [
            StructuredCell(
                row=c.row,
                col=c.col,
                rowspan=c.rowspan,
                colspan=c.colspan,
                content=c.content,
                is_header=c.is_header,
                is_merged=c.rowspan > 1 or c.colspan > 1,
                original_content=c.content,
            )
            for c in table.cells
        ]

        # Compute page frequency
        pages = hash_pages.get(s_hash, set())
        frequency = len(pages) / total_pages if total_pages > 0 else 0
        is_repeated = (frequency >= 0.5 and len(pages) > 2) or is_template
        if is_template and frequency < 0.5:
            reasons.append("Cells match the repeating page header/footer template")

        # Classification logic
        classification = TableClassification.UNKNOWN_TABLE
        confidence = 0.5

        # 1. Repeated page template (header/footer tables)
        if is_repeated:
            classification = TableClassification.HEADER_TABLE
            confidence = 0.9
            reasons.append(f"Appears on {len(pages)}/{total_pages} pages ({frequency:.0%})")

        # 2. Approval/signature table
        elif APPROVAL_PATTERN.search(header_text) and table.num_rows <= 5:
            approval_hits = len(APPROVAL_PATTERN.findall(all_content))
            if approval_hits >= 2:
                classification = TableClassification.APPROVAL_TABLE
                confidence = 0.85
                reasons.append(f"Contains {approval_hits} approval-related terms")

        # 3. TOC table
        elif TOC_PATTERN.search(header_text) and "page" in header_text:
            classification = TableClassification.TOC_TABLE
            confidence = 0.8
            reasons.append("Contains TOC-like column headers")

        # 4. Abbreviation table
        elif any(
            kw in header_text
            for kw in ("abbreviation", "acronym", "full form", "expansion", "definition")
        ):
            classification = TableClassification.ABBREVIATION_TABLE
            confidence = 0.9
            reasons.append("Column headers indicate abbreviation table")

        # 5. Equipment table
        elif EQUIPMENT_TAG_PATTERN.search(all_content):
            equip_tags = EQUIPMENT_TAG_PATTERN.findall(all_content)
            if len(equip_tags) >= 3:
                if INSTRUMENT_TAG_PATTERN.search(all_content) and "instrument" in header_text.lower():
                    classification = TableClassification.INSTRUMENT_TABLE
                    confidence = 0.8
                    reasons.append(f"Contains {len(equip_tags)} instrument-like tags")
                else:
                    classification = TableClassification.EQUIPMENT_TABLE
                    confidence = 0.8
                    reasons.append(f"Contains {len(equip_tags)} equipment tags")

        # 6. Material balance / process data table
        elif UNIT_PATTERN.search(all_content):
            unit_hits = len(UNIT_PATTERN.findall(all_content))
            if any(kw in header_text for kw in ("stream", "product", "feed", "material")):
                classification = TableClassification.MATERIAL_BALANCE_TABLE
                confidence = 0.8
                reasons.append(f"Contains stream/product headers with {unit_hits} unit values")
            elif any(kw in header_text for kw in ("limit", "range", "min", "max", "normal")):
                classification = TableClassification.OPERATING_LIMIT_TABLE
                confidence = 0.8
                reasons.append(f"Contains limit-type headers with {unit_hits} unit values")
            elif unit_hits >= 3:
                classification = TableClassification.PROCESS_DATA_TABLE
                confidence = 0.7
                reasons.append(f"Contains {unit_hits} engineering unit values")

        # 7. Safety table
        elif SAFETY_PATTERN.search(all_content):
            safety_hits = len(SAFETY_PATTERN.findall(all_content))
            if safety_hits >= 2:
                classification = TableClassification.SAFETY_TABLE
                confidence = 0.75
                reasons.append(f"Contains {safety_hits} safety-related terms")

        # 8. Maintenance table
        elif MAINTENANCE_PATTERN.search(all_content):
            maint_hits = len(MAINTENANCE_PATTERN.findall(all_content))
            if maint_hits >= 2:
                classification = TableClassification.MAINTENANCE_TABLE
                confidence = 0.75
                reasons.append(f"Contains {maint_hits} maintenance-related terms")

        # 9. Chemical table
        elif CHEMICAL_PATTERN.search(all_content):
            chem_hits = len(CHEMICAL_PATTERN.findall(all_content))
            if chem_hits >= 2:
                classification = TableClassification.CHEMICAL_TABLE
                confidence = 0.7
                reasons.append(f"Contains {chem_hits} chemical-related terms")

        # 10. Decorative (tiny tables)
        elif table.num_rows <= 1 and table.num_cols <= 2:
            classification = TableClassification.DECORATIVE_TABLE
            confidence = 0.6
            reasons.append("Very small table (likely decorative)")

        if not reasons:
            reasons.append("No strong classification signal detected")

        enters_extraction = classification in ENGINEERING_CLASSIFICATIONS

        return ClassifiedTable(
            table_id=table.table_id,
            document_id=doc_id,
            page=table.page,
            title=table.caption,
            classification=classification,
            classification_confidence=confidence,
            classification_reasons=reasons,
            num_rows=table.num_rows,
            num_cols=table.num_cols,
            columns=columns,
            cells=structured_cells,
            is_repeated=is_repeated,
            repeat_frequency=frequency,
            column_signature=col_sig,
            structural_hash=s_hash,
            enters_extraction=enters_extraction,
        )

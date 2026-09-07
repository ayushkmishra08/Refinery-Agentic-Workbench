"""Table reconstruction: merged-cell handling, column normalization, structure repair.

Handles the messy reality of refinery document tables:
- Merged cells (rowspan/colspan) filled into individual cells
- Column header normalization
- Duplicate row removal
- Page-header artifacts within tables
"""

from __future__ import annotations

import logging

from schemas.table_schema import ClassifiedTable, StructuredCell

logger = logging.getLogger(__name__)


def propagate_merged_cells(table: ClassifiedTable) -> ClassifiedTable:
    """Fill merged cells (rowspan/colspan > 1) into individual cells.

    A cell spanning rows 0-2, col 1 gets its content copied to
    (row=1, col=1) and (row=2, col=1) if those cells are empty.
    """
    if not table.cells:
        return table

    # Build a grid lookup
    grid: dict[tuple[int, int], StructuredCell] = {}
    for cell in table.cells:
        grid[(cell.row, cell.col)] = cell

    new_cells: list[StructuredCell] = list(table.cells)

    for cell in table.cells:
        if cell.rowspan <= 1 and cell.colspan <= 1:
            continue

        for dr in range(cell.rowspan):
            for dc in range(cell.colspan):
                if dr == 0 and dc == 0:
                    continue  # Skip the original cell
                target = (cell.row + dr, cell.col + dc)
                if target not in grid:
                    # Fill in the merged content
                    filled = StructuredCell(
                        row=target[0],
                        col=target[1],
                        rowspan=1,
                        colspan=1,
                        content=cell.content,
                        is_header=cell.is_header,
                        is_merged=True,
                        original_content="",  # Was empty, filled from merge
                    )
                    new_cells.append(filled)
                    grid[target] = filled

    table.cells = new_cells
    return table


def normalize_column_headers(table: ClassifiedTable) -> ClassifiedTable:
    """Normalize column headers for consistent downstream processing."""
    for col in table.columns:
        col.normalized_header = (
            col.header.strip()
            .lower()
            .replace("\n", " ")
            .replace("  ", " ")
        )
        # Detect data types from sample values
        if col.sample_values:
            numeric_count = sum(
                1 for v in col.sample_values
                if _is_numeric(v)
            )
            if numeric_count == len(col.sample_values):
                col.data_type = "numeric"
            elif all(_looks_like_tag(v) for v in col.sample_values if v.strip()):
                col.data_type = "tag"
            else:
                col.data_type = "text"
    return table


def remove_duplicate_rows(table: ClassifiedTable) -> ClassifiedTable:
    """Remove exact duplicate rows from a table."""
    if not table.cells:
        return table

    # Group cells by row
    rows: dict[int, list[StructuredCell]] = {}
    for cell in table.cells:
        if cell.row not in rows:
            rows[cell.row] = []
        rows[cell.row].append(cell)

    # Create row signatures
    row_signatures: dict[int, str] = {}
    for row_idx, row_cells in rows.items():
        sig = "|".join(
            sorted(f"{c.col}:{c.content.strip()}" for c in row_cells)
        )
        row_signatures[row_idx] = sig

    # Find duplicates
    seen_sigs: set[str] = set()
    duplicate_rows: set[int] = set()
    for row_idx in sorted(row_signatures.keys()):
        sig = row_signatures[row_idx]
        if sig in seen_sigs:
            duplicate_rows.add(row_idx)
        else:
            seen_sigs.add(sig)

    if duplicate_rows:
        table.cells = [c for c in table.cells if c.row not in duplicate_rows]
        logger.debug(f"Removed {len(duplicate_rows)} duplicate rows from {table.table_id}")

    return table


def reconstruct_table(table: ClassifiedTable) -> ClassifiedTable:
    """Apply all reconstruction steps to a classified table."""
    table = propagate_merged_cells(table)
    table = normalize_column_headers(table)
    table = remove_duplicate_rows(table)
    return table


def _is_numeric(value: str) -> bool:
    """Check if a string looks like a numeric value."""
    cleaned = value.strip().replace(",", "").replace(" ", "")
    if not cleaned:
        return False
    try:
        float(cleaned)
        return True
    except ValueError:
        # Check for ranges like "10-15" or values with units attached
        import re
        return bool(re.match(r"^[\d.]+[-–][\d.]+$", cleaned))


def _looks_like_tag(value: str) -> bool:
    """Check if a value looks like an equipment/instrument tag."""
    import re
    return bool(re.match(r"^[A-Z]{1,4}-?\d{2,5}[A-Z]?$", value.strip()))

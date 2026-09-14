"""Critical table check: print real cell grids for representative table categories.

Usage:
    python scripts/check_tables.py [document_id] [--rows N] [--search KEYWORD ...]

Reads data/parsed/<doc>/tables.json (structured cells, spans, grid) and shows the
best-matching table per category, plus any keyword searches, so a human can
confirm that real cell contents (not placeholders) are available before
normalization / semantic extraction.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_layer.schemas.parsed_document import ParsedTable  # noqa: E402
from knowledge_layer.parse_validation import _REPRESENTATIVE_QUERIES, _looks_like_page_header, _score_table  # noqa: E402


def load_tables(doc_id: str) -> list[ParsedTable]:
    path = Path("data/parsed") / doc_id / "tables.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ParsedTable.model_validate(t) for t in data["tables"]]


def show(table: ParsedTable, rows: int) -> None:
    merged = [c for c in table.cells if c.rowspan > 1 or c.colspan > 1]
    print(f"\n{table.table_id}  page {table.page}  {table.num_rows}x{table.num_cols}  "
          f"cells={len(table.cells)} non-empty={sum(1 for c in table.cells if c.content.strip())} "
          f"merged={len(merged)} headers={sum(1 for c in table.cells if c.is_header)} "
          f"caption={table.caption!r} bbox={table.bbox.model_dump() if table.bbox else None}")
    widths = [min(28, max(len(r[i]) if i < len(r) else 0 for r in table.grid) or 1) for i in range(table.num_cols)]
    for r_idx, row in enumerate(table.grid[:rows]):
        cells = [(row[i] if i < len(row) else "").replace("\n", " ")[:28].ljust(widths[i]) for i in range(table.num_cols)]
        print(f"  r{r_idx:02d} | " + " | ".join(cells))
    if len(table.grid) > rows:
        print(f"  ... {len(table.grid) - rows} more rows")
    if merged[:3]:
        print("  merged cells:", [(c.row, c.col, c.rowspan, c.colspan, c.content[:20]) for c in merged[:3]])


def main() -> None:
    args = [a for a in sys.argv[1:]]
    rows = 8
    searches: list[str] = []
    if "--rows" in args:
        i = args.index("--rows"); rows = int(args[i + 1]); del args[i:i + 2]
    if "--search" in args:
        i = args.index("--search"); searches = args[i + 1:]; args = args[:i]
    doc_id = args[0] if args else next(Path("data/parsed").glob("*/tables.json")).parent.name

    tables = load_tables(doc_id)
    content_tables = [t for t in tables if not _looks_like_page_header(t)]
    print(f"{doc_id}: {len(tables)} tables, {len(content_tables)} beyond page-header boxes, "
          f"{sum(len(t.cells) for t in tables)} cells")

    used: set[str] = set()
    for category, kws in _REPRESENTATIVE_QUERIES.items():
        ranked = sorted(
            ((_score_table(t, kws), t) for t in content_tables if t.table_id not in used),
            key=lambda x: -x[0],
        )
        print(f"\n===== {category.upper()} =====")
        if not ranked or ranked[0][0] < 3:
            print("  (no matching table)")
            continue
        for score, t in ranked[:2]:
            used.add(t.table_id)
            print(f"  match score {score}")
            show(t, rows)

    for kw in searches:
        print(f"\n===== SEARCH '{kw}' =====")
        hits = [t for t in content_tables if kw.lower() in " ".join(c.content for c in t.cells).lower()]
        print(f"  {len(hits)} tables")
        for t in hits[:3]:
            show(t, rows)


if __name__ == "__main__":
    main()

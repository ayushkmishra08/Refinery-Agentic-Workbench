"""Inspect table content to understand what Docling is extracting."""
import json, sys
sys.path.insert(0, ".")

with open("data/parsed/CDU operating manual/parsed_document.json", "r", encoding="utf-8") as f:
    d = json.load(f)

# Show actual table cell content for first few tables
print("=== Table Content Samples ===\n")
tables = d.get('tables', [])
for table in tables[:6]:
    page = table.get('page', '?')
    cells = table.get('cells', [])
    print(f"Table {table['table_id']} (page {page}, {table['num_rows']}x{table['num_cols']}, {len(cells)} cells)")
    for cell in cells[:8]:
        content = cell.get('content', '')[:100]
        is_header = cell.get('is_header', False)
        hdr = " [HEADER]" if is_header else ""
        print(f"  [{cell['row']},{cell['col']}]{hdr}: {content}")
    if len(cells) > 8:
        print(f"  ... and {len(cells) - 8} more cells")
    print()

# Now check the normalized doc
print("\n=== Normalized Document ===\n")
with open("data/normalized/CDU operating manual_normalized.json", "r", encoding="utf-8") as f:
    nd = json.load(f)

elements = nd.get('elements', [])
print(f"Total normalized elements: {len(elements)}")
for e in elements[:10]:
    content = e.get('content', '')[:200]
    print(f"  [{e['content_type']}] page {e.get('page','?')}: {content}")

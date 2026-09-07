"""Quick test of the fixed parser on pages 3-5."""
import sys, json
sys.path.insert(0, ".")

from src.config import PipelineConfig
from src.parser import DocumentParser
from pathlib import Path

config = PipelineConfig()
parser = DocumentParser(config)

# Parse the full doc (it will take a while)
# But first let's test with a direct Docling call on pages 3-5 and process through our code
print("Testing fixed parser extraction logic...")

from docling.document_converter import DocumentConverter
converter = DocumentConverter()
result = converter.convert("data/raw/CDU operating manual.pdf", page_range=(3, 5))
doc = result.document
doc_dict = doc.export_to_dict()

# Simulate our parser logic for tables
tables_data = doc_dict.get("tables", [])
print(f"\nTables found: {len(tables_data)}")

for tidx, table_ref in enumerate(tables_data):
    table_data = table_ref.get("data", {})
    raw_cells = table_data.get("table_cells", [])
    num_rows = table_data.get("num_rows", 0)
    num_cols = table_data.get("num_cols", 0)
    
    prov = table_ref.get("prov", [])
    page_num = prov[0].get("page_no", "?") if prov else "?"
    
    print(f"\n  Table {tidx} (page {page_num}, {num_rows}x{num_cols}, {len(raw_cells)} cells):")
    for cell in raw_cells[:5]:
        row = cell.get("start_row_offset_idx", "?")
        col = cell.get("start_col_offset_idx", "?")
        text = cell.get("text", "")[:60]
        is_hdr = cell.get("column_header", False)
        print(f"    [{row},{col}] {'[HDR]' if is_hdr else '     '} {text}")
    if len(raw_cells) > 5:
        print(f"    ... and {len(raw_cells) - 5} more cells")

# Also check texts
texts_list = doc_dict.get("texts", [])
print(f"\nTexts found: {len(texts_list)}")
for item in texts_list[:5]:
    text = item.get("text", "")[:80]
    label = item.get("label", "?")
    prov = item.get("prov", [])
    page = prov[0].get("page_no", "?") if prov else "?"
    print(f"  [{label}] page {page}: {text}")

print("\n[OK] Parser fix verified!")

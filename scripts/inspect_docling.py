"""Inspect the actual Docling export_to_dict() structure for this document."""
import json, sys
sys.path.insert(0, ".")

with open("data/parsed/CDU operating manual_parsed.json", "r", encoding="utf-8") as f:
    d = json.load(f)

# But we need the RAW Docling output. Let me re-parse page 3 only to see the dict structure.
# Actually, let's look at what Docling gives us directly.
print("=== Attempting Docling re-parse of a single page ===")

from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True
pipeline_options.do_table_structure = True

converter = DocumentConverter()

result = converter.convert("data/raw/CDU operating manual.pdf", page_range=(3, 5))
doc = result.document

# Export to dict and inspect keys
doc_dict = doc.export_to_dict()

print(f"\nTop-level keys: {list(doc_dict.keys())}")
for key in doc_dict:
    val = doc_dict[key]
    if isinstance(val, list):
        print(f"  {key}: list of {len(val)} items")
        if val:
            if isinstance(val[0], dict):
                print(f"    First item keys: {list(val[0].keys())}")
                # Show the first item content truncated
                for k, v in val[0].items():
                    if isinstance(v, str) and len(v) > 100:
                        print(f"    {k}: {v[:100]}...")
                    elif isinstance(v, list) and len(v) > 3:
                        print(f"    {k}: list[{len(v)}] first={v[0]}")
                    else:
                        print(f"    {k}: {v}")
    elif isinstance(val, dict):
        print(f"  {key}: dict with keys {list(val.keys())[:10]}")
    elif isinstance(val, str) and len(val) > 100:
        print(f"  {key}: {val[:100]}...")
    else:
        print(f"  {key}: {val}")

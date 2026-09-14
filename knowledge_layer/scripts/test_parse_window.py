"""Smoke-test the windowed parser on the first N pages of the raw PDF.

Usage:
    python scripts/test_parse_window.py [max_pages] [window_size]

Writes to data/parsed/_smoke_<doc>/ so the real output directory is untouched.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_layer.config import load_config  # noqa: E402
from knowledge_layer.parse_validation import validate_parsed_document, write_parse_report  # noqa: E402
from knowledge_layer.parser import DocumentParser  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

max_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 6
window = int(sys.argv[2]) if len(sys.argv) > 2 else 3

config = load_config()
config.parser.max_pages = max_pages
config.parser.page_window_size = window
# Redirect output so the smoke test never collides with the real parse
config.paths.parsed_dir = config.paths.parsed_dir / "_smoke"
config.paths.checkpoints_dir = config.paths.checkpoints_dir / "_smoke"
config.paths.parsed_dir.mkdir(parents=True, exist_ok=True)
config.paths.checkpoints_dir.mkdir(parents=True, exist_ok=True)

pdfs = sorted(config.paths.raw_dir.glob("*.pdf"))
assert pdfs, "no PDF in data/raw"
pdf = pdfs[0]

t0 = time.time()
parser = DocumentParser(config)
doc = parser.parse(pdf)
print(f"\nParsed {len(doc.pages)} pages in {time.time() - t0:.1f}s; tables={len(doc.tables)}")
print("environment:", json.dumps(doc.metadata["environment"], indent=1, default=str)[:1500])

for page in doc.pages:
    print(f"\n=== page {page.page_number} ({page.width}x{page.height}) {len(page.elements)} elements")
    for el in page.elements:
        bb = el.bounding_box
        bbs = f"[{bb.l:.0f},{bb.t:.0f},{bb.r:.0f},{bb.b:.0f}]" if bb else "-"
        extra = ""
        if el.heading_level:
            extra = f" H{el.heading_level}"
        if el.list_marker or el.list_enumerated is not None:
            extra += f" list(marker={el.list_marker!r}, enum={el.list_enumerated})"
        if el.table:
            extra += f" table {el.table.num_rows}x{el.table.num_cols} cells={len(el.table.cells)}"
        if el.image_path:
            extra += f" img={el.image_path}"
        print(f"  ro={el.reading_order:4d} pos={el.position_in_page:3d} {el.element_type.value:11s} "
              f"{el.docling_label:14s} {el.content_layer:9s} {bbs:22s}{extra} :: {el.content[:80]!r}")

for t in doc.tables[:6]:
    print(f"\n--- {t.table_id} p{t.page} {t.num_rows}x{t.num_cols} cells={len(t.cells)} caption={t.caption!r}")
    for row in t.grid[:5]:
        print("   ", " | ".join(c[:25] for c in row))

res = validate_parsed_document(doc)
print("\nVALIDATION passed:", res.passed)
for c in res.checks:
    print(f"  {'OK ' if c['ok'] else 'NOK'} [{c['severity']}] {c['check']}: {c['detail'][:140]}")
rp = write_parse_report(doc, res, parser.output_dir(doc.document_id) / "parse_report.md")
print("report:", rp)

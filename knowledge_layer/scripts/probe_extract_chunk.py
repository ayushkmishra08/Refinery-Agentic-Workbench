"""Probe one chunk through the extractor + validator and show everything.

Usage: python scripts/probe_extract_chunk.py "<document_id>" <chunk_sequence> [--no-llm]

Prints the prompt size, the raw model response, what parsed, and every
validation issue, so extraction problems can be diagnosed without re-running
the whole pipeline.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_layer.schemas.document_profile import DocumentProfile  # noqa: E402
from knowledge_layer.schemas.glossary import DocumentGlossary  # noqa: E402
from knowledge_layer.chunker import Chunk  # noqa: E402
from knowledge_layer.config import load_config  # noqa: E402
from knowledge_layer.extractor import OllamaExtractor  # noqa: E402
from knowledge_layer.retriever import RetrievalContext  # noqa: E402
from knowledge_layer.validator import ExtractionValidator  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

doc_id = sys.argv[1]
seq = int(sys.argv[2])
config = load_config()
kdir = config.paths.knowledge_dir / doc_id
chunks = json.loads((kdir / "chunks.json").read_text(encoding="utf-8"))
cd = next(c for c in chunks if c["sequence"] == seq)
chunk = Chunk(**{k: cd[k] for k in ("chunk_id", "document_id", "sequence", "page_start", "page_end",
                                    "section_path", "parent_heading", "text", "contains_table",
                                    "table_ids", "contains_procedure", "element_types",
                                    "token_estimate", "overlap_text") if k in cd},
              **{k: cd[k] for k in ("chunk_type", "is_engineering", "chapter_number", "section_id", "procedure_ids")
                 if k in cd})
profile = DocumentProfile.model_validate_json((kdir / "document_profile.json").read_text(encoding="utf-8"))
glossary = DocumentGlossary.model_validate_json((kdir / "glossary.json").read_text(encoding="utf-8"))

extractor = OllamaExtractor(config)
prompt = extractor._build_extraction_prompt(chunk, RetrievalContext(), profile, glossary)
print(f"chunk {chunk.chunk_id} pages {chunk.page_start}-{chunk.page_end} type={chunk.chunk_type} "
      f"engineering={chunk.is_engineering} section={chunk.section_path!r}")
print(f"chunk tokens~{chunk.token_estimate}, prompt chars={len(prompt)} (~{len(prompt)//4} tokens)")
print("---- chunk text (first 1200 chars) ----")
print(chunk.text[:1200])
if "--no-llm" in sys.argv:
    sys.exit(0)

t0 = time.time()
raw = extractor._call_ollama(prompt)
print(f"\n---- raw response ({len(raw)} chars, {time.time() - t0:.0f}s) ----")
print(raw[:6000])

extraction = extractor._parse_extraction(raw, chunk, doc_id)
print(f"\nparsed: {len(extraction.entities)} entities, {len(extraction.relationships)} relationships, "
      f"{len(extraction.claims)} claims, refs={extraction.references}")
for e in extraction.entities[:25]:
    print(f"  E {e.name!r} [{e.entity_type.value}/{e.entity_type_raw}] conf={e.confidence:.2f} | {e.evidence[:70]!r}")
for r in extraction.relationships[:25]:
    print(f"  R {r.subject!r} -{r.predicate.value}-> {r.object!r} | {r.evidence[:70]!r}")
for c in extraction.claims[:25]:
    print(f"  C {c.subject!r} {c.predicate.value}={c.value} {c.unit} role={c.parameter_role!r} loc={c.location!r} "
          f"mode={c.operating_mode!r} scen={c.scenario!r} | {c.evidence[:60]!r}")
validator = ExtractionValidator(config, None)
result = validator.validate(extraction, chunk)
print(f"validation passed={result.passed} errors={result.failed_checks} warnings={result.warning_checks} "
      f"valid e/r/c={result.valid_entities}/{result.valid_relationships}/{result.valid_claims}")
for issue in result.issues:
    print(f"  [{issue.severity.value}] {issue.category.value} {issue.item_type} {issue.item_id}: {issue.message[:120]} "
          f"{json.dumps(issue.details)[:160] if issue.details else ''}")
extractor.close()

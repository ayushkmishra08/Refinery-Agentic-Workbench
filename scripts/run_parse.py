"""Run ONLY the parsing phase (Docling) + parse validation + parse report.

Usage:
    python scripts/run_parse.py [--force] [--expected-pages N]

Writes data/parsed/<doc>/ and, when validation passes, marks the pipeline
PARSING checkpoint so that `python -m src` skips straight to normalization.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.checkpoint import PipelineCheckpoint, PipelinePhase  # noqa: E402
from src.config import load_config  # noqa: E402
from src.parse_validation import validate_parsed_document, write_parse_report  # noqa: E402
from src.parser import DocumentParser  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv
    expected_pages = None
    if "--expected-pages" in sys.argv:
        expected_pages = int(sys.argv[sys.argv.index("--expected-pages") + 1])

    config = load_config()
    pdfs = sorted(config.paths.raw_dir.glob("*.pdf"))
    if not pdfs:
        print("No PDFs in data/raw/")
        return 1

    rc = 0
    for pdf in pdfs:
        doc_id = pdf.stem
        out_dir = config.paths.parsed_dir / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / "parse_log.txt"
        handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")]
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=handlers, force=True,
        )
        log = logging.getLogger("run_parse")

        checkpoint = PipelineCheckpoint(config, doc_id)
        if force:
            ck = config.paths.checkpoints_dir / f"{doc_id}_parse.json"
            if ck.exists():
                ck.unlink()
            log.info("--force: parse checkpoint removed")

        log.info(f"=== Parsing {pdf.name} ===")
        t0 = time.time()
        parser = DocumentParser(config)
        doc = parser.parse(pdf)
        result = validate_parsed_document(doc, expected_pages=expected_pages)
        report = write_parse_report(doc, result, out_dir / "parse_report.md")

        log.info(f"Parse finished in {time.time() - t0:.0f}s; validation passed={result.passed}")
        for c in result.checks:
            mark = "OK  " if c["ok"] else ("WARN" if c["severity"] == "warning" else "FAIL")
            log.info(f"  {mark} {c['check']}: {c['detail'][:200]}")
        log.info(f"Report: {report}")

        if result.passed:
            checkpoint.set_source_hash(doc.source_hash)
            checkpoint.mark_phase_complete(PipelinePhase.PARSING)
            log.info("Pipeline checkpoint: PARSING marked complete")
        else:
            log.error("Parse validation FAILED; pipeline checkpoint not updated")
            rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())

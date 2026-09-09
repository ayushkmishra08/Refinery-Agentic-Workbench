"""Remove selected phases from a document's pipeline checkpoint so they re-run.

Usage:
    python scripts/reset_phases.py "<document_id>" normalizing table_classification document_profile glossary chunking embedding
    python scripts/reset_phases.py "<document_id>" --from normalizing   # that phase and everything after it

Parsing is never reset here (use scripts/run_parse.py --force for that).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.checkpoint import PipelinePhase  # noqa: E402
from src.config import load_config  # noqa: E402

ORDER = [p.value for p in PipelinePhase]


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    doc_id = sys.argv[1]
    args = sys.argv[2:]
    if args[0] == "--from":
        start = ORDER.index(args[1])
        phases = set(ORDER[start:])
    else:
        phases = set(args)
    phases.discard(PipelinePhase.PARSING.value)

    config = load_config()
    path = config.paths.checkpoints_dir / f"{doc_id}_pipeline.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    before = list(state.get("completed_phases", []))
    state["completed_phases"] = [p for p in before if p not in phases]
    if {"extraction", "chunking"} & phases:
        state["completed_chunks"] = []
        state["failed_chunks"] = []
    state["current_phase"] = state["completed_phases"][-1] if state["completed_phases"] else "parsing"
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"{doc_id}: completed phases {before} -> {state['completed_phases']}")

    # Stale derived files are overwritten on re-run, but chunk cache must go so
    # the chunker's output is not silently reused.
    if "chunking" in phases:
        for f in ("chunks.json", "chunk_embeddings.json"):
            p = config.paths.knowledge_dir / doc_id / f
            if p.exists():
                p.unlink()
                print(f"removed {p}")


if __name__ == "__main__":
    main()

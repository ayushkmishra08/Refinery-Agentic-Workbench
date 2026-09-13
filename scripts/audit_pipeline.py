"""Offline quality audit of the deterministic layers (no Neo4j / Ollama needed).

Usage: python scripts/audit_pipeline.py "<document_id>" [--pages 61,225] [--samples 15] [--json out.json]

Re-runs table classification -> normalization -> chunking -> deterministic
claims (table / specification / prose) on the parsed document and prints the
quality metrics that used to be the pipeline's weak points:

  * chunk text contamination: overlap bridges, figure placeholders, header boxes,
    approval blocks, running titles;
  * structure: chapters recovered vs. the table of contents, section levels;
  * semantic chunk types and procedure detection;
  * deterministic claims with their context dimensions (role / location / basis /
    scenario), plus a random sample for eyeballing;
  * document references that carry a real identifier, cross references,
    standing instructions.

Exit code 1 when a hard quality gate fails (contamination > 0, no procedures,
no chapters), so it can run in CI against a committed parsed document.
"""
from __future__ import annotations

import collections
import io
import json
import random
import statistics
import sys
from pathlib import Path

if sys.platform == "win32":  # console code page cannot print °, ², −
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    doc_id = args[0]
    pages = [int(x) for x in args[args.index("--pages") + 1].split(",")] if "--pages" in args else []
    n_samples = int(args[args.index("--samples") + 1]) if "--samples" in args else 15
    out_json = args[args.index("--json") + 1] if "--json" in args else None

    import logging
    logging.basicConfig(level=logging.WARNING)

    from src.chunker import DocumentChunker
    from src.document_profile import DocumentProfiler
    from src.normalizer import DocumentNormalizer
    from src.parser import load_parsed_document
    from src.spec_claims import extract_prose_claims, extract_spec_claims
    from src.table_claims import extract_table_claims
    from src.table_classifier import TableClassifier
    from src.table_context import build_table_contexts

    config = load_config()
    parsed = load_parsed_document(config, doc_id)
    if parsed is None:
        print(f"no parsed document for {doc_id!r} under {config.paths.parsed_dir}")
        return 2
    table_results = TableClassifier(config).classify_all(parsed)
    normalized = DocumentNormalizer(config).normalize(parsed, table_results)
    chunks = DocumentChunker(config).chunk(normalized, table_results)
    profile = DocumentProfiler(config).build_profile(normalized, table_results, parsed.tables)
    tables_by_id = {t.table_id: t for t in parsed.tables}
    contexts = build_table_contexts(normalized, tables_by_id)
    class_by_id = {t.table_id: t.classification.value for t in table_results.classified_tables}

    report: dict = {"document_id": doc_id}
    s = normalized.stats
    report["normalization"] = {k: v for k, v in s.model_dump().items() if v}
    report["structure"] = {
        "toc_chapters": s.chapters_from_toc,
        "chapters": len(normalized.chapters),
        "sections": len(normalized.sections),
        "section_levels": dict(collections.Counter(n.level for n in normalized.sections)),
        "procedures": len(normalized.procedures),
        "procedure_types": dict(collections.Counter(p.procedure_type.value for p in normalized.procedures)),
        "procedure_steps": sum(len(p.steps) for p in normalized.procedures),
    }
    toks = [c.token_estimate for c in chunks]
    contamination = {
        "overlap_marker": sum(1 for c in chunks if "[Context from previous" in c.text),
        "figure_placeholder": sum(c.text.count("[Figure on page") for c in chunks),
        "header_box": sum(1 for c in chunks if "Chapter Rev No" in c.text or "PLANT NAME:" in c.text),
        "approval_block": sum(1 for c in chunks if "Approved by | " in c.text or "Approved By | " in c.text),
        "operating_manual_title": sum(1 for c in chunks if "OPERATING MANUAL" in c.text),
    }
    report["chunks"] = {
        "count": len(chunks),
        "tokens": {"min": min(toks), "max": max(toks), "mean": sum(toks) // len(toks), "median": statistics.median(toks)},
        "over_max": sum(1 for c in chunks if c.token_estimate > config.chunker.max_tokens),
        "heading_only": sum(1 for c in chunks if all(e.content_type.value == "heading" for e in c.elements)),
        "types": dict(collections.Counter(c.chunk_type for c in chunks)),
        "engineering": sum(1 for c in chunks if c.is_engineering),
        "with_procedure": sum(1 for c in chunks if c.contains_procedure),
        "with_table": sum(1 for c in chunks if c.contains_table),
        "distinct_section_paths": len({c.section_path for c in chunks}),
        "contamination": contamination,
    }

    # deterministic claims
    all_claims = []
    for c in chunks:
        if not c.is_engineering:
            continue
        all_claims += extract_spec_claims(c.elements, c.chunk_id, doc_id, c.section_path, c.parent_heading)
        all_claims += extract_prose_claims(c.text, c.chunk_id, doc_id, c.section_path, c.page_start, c.parent_heading)
        for tid in dict.fromkeys(c.table_ids):
            tbl = tables_by_id.get(tid)
            if tbl is None:
                continue
            ctx = contexts.get(tid)
            inherit = tables_by_id.get(ctx.continuation_of) if ctx is not None and ctx.continuation_of else None
            all_claims += extract_table_claims(tbl, c.chunk_id, doc_id, c.section_path, subject_hint=c.parent_heading or None,
                                               classification=class_by_id.get(tid), context=ctx, inherit_from=inherit)
    by_source = collections.Counter(cl.source for cl in all_claims)
    by_pred_raw = collections.Counter(cl.predicate_raw.split(":")[0] for cl in all_claims)
    report["claims"] = {
        "total": len(all_claims),
        "by_source": dict(by_source),
        "by_kind": dict(by_pred_raw),
        "with_parameter_role": sum(1 for cl in all_claims if cl.parameter_role),
        "with_location": sum(1 for cl in all_claims if cl.location),
        "with_scenario": sum(1 for cl in all_claims if cl.scenario),
        "with_pressure_basis": sum(1 for cl in all_claims if cl.pressure_basis),
        "predicates": dict(collections.Counter(cl.predicate.value for cl in all_claims).most_common(12)),
        "distinct_context_keys": len({(cl.subject.lower(), cl.context_key()) for cl in all_claims}),
    }
    report["references"] = {
        "documents": len(profile.referenced_documents),
        "by_type": dict(collections.Counter(r.document_type for r in profile.referenced_documents)),
        "cross_references": len(profile.cross_references),
        "standing_instructions": len(profile.standing_instructions),
        "examples": [r.reference_text for r in profile.referenced_documents[:12]],
    }

    print(json.dumps(report, indent=2, default=str))

    if pages:
        seen = set()
        for pg in pages:
            c = next((c for c in chunks if c.page_start <= pg <= c.page_end), None)
            if c is None or c.sequence in seen:
                continue
            seen.add(c.sequence)
            print(f"\n===== chunk {c.sequence} p{c.page_start}-{c.page_end} type={c.chunk_type} eng={c.is_engineering} "
                  f"procs={c.procedure_ids} tokens={c.token_estimate}\nPATH: {c.section_path}")
            print("TEXT:", c.text[:900].replace("\n", " // "))
            for cl in [x for x in all_claims if x.chunk_id == c.chunk_id]:
                print(f"  CLAIM[{cl.source}] {cl.subject!r} {cl.predicate.value}={cl.value} {cl.unit} role={cl.parameter_role!r} "
                      f"loc={cl.location!r} basis={cl.pressure_basis!r} scen={cl.scenario!r} qual={cl.qualifier!r} | {cl.evidence[:60]!r}")

    if n_samples and all_claims:
        random.seed(7)
        print(f"\n--- random sample of {n_samples} deterministic claims ---")
        for cl in random.sample(all_claims, min(n_samples, len(all_claims))):
            print(f"  [{cl.source}] {cl.subject[:32]!r} {cl.predicate.value}={cl.value} {cl.unit} role={cl.parameter_role} "
                  f"loc={cl.location} scen={cl.scenario} qual={cl.qualifier[:40]!r} p{cl.page} | {cl.evidence[:60]!r}")

    if out_json:
        Path(out_json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    failed = []
    if any(contamination.values()):
        failed.append(f"chunk contamination {contamination}")
    if not normalized.chapters:
        failed.append("no chapters recovered")
    if not normalized.procedures:
        failed.append("no procedures detected")
    if report["chunks"]["heading_only"]:
        failed.append(f"{report['chunks']['heading_only']} heading-only chunks")
    if failed:
        print("\nQUALITY GATE FAILED:", "; ".join(failed))
        return 1
    print("\nQUALITY GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

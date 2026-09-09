"""Parse validation and report generation.

Checks that a ParsedDocument is fit for semantic extraction *before* any
normalization or LLM work happens.  A process exit code of 0 is not enough:
we verify page coverage, table cell availability, provenance consistency and
representative table content, and write ``parse_report.md`` next to the
parsed output.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schemas.parsed_document import ElementType, ParsedDocument, ParsedTable

logger = logging.getLogger(__name__)


@dataclass
class ParseValidationResult:
    passed: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    representative_tables: list[dict[str, Any]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str, severity: str = "error") -> None:
        self.checks.append({"check": name, "ok": ok, "severity": severity, "detail": detail})
        if not ok and severity == "error":
            self.passed = False


# Keyword sets used to find representative tables for the manual inspection.
_REPRESENTATIVE_QUERIES: dict[str, list[str]] = {
    "abbreviation table": ["abbreviation", "acronym", "full form", "expansion"],
    "table of contents": ["title", "from page", "page no", "contents", "chapter no"],
    "material-balance table": ["material balance", "feed", "product", "stream", "kg/h", "t/h", "mt/h", "flow"],
    "equipment table": ["equipment", "tag no", "tag", "service", "description", "pump", "exchanger", "vessel"],
    "operating-limit table": ["limit", "range", "normal", "min", "max", "alarm", "trip", "set point", "setpoint"],
    "safety table": ["hazard", "safety", "ppe", "fire", "emergency", "msds", "toxic", "lel", "tlv"],
    "procedure/checklist table": ["step", "check", "action", "procedure", "verify", "ensure", "checklist"],
}


def _table_text(table: ParsedTable) -> str:
    return " ".join(c.content for c in table.cells).lower()


def _table_headers(table: ParsedTable) -> str:
    hdr = [c.content for c in table.cells if c.is_header]
    if not hdr and table.grid:
        hdr = table.grid[0]
    return " ".join(hdr).lower()


def _score_table(table: ParsedTable, keywords: list[str]) -> int:
    text = _table_text(table)
    headers = _table_headers(table)
    score = 0
    for kw in keywords:
        if kw in headers:
            score += 3
        if kw in text:
            score += 1
    # Prefer tables with real bodies
    if table.num_rows >= 3 and table.num_cols >= 2:
        score += 1
    return score


def find_representative_tables(doc: ParsedDocument, max_rows: int = 6) -> list[dict[str, Any]]:
    """Pick one best-matching table per category and return a compact preview."""
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    # Ignore the repeating page header box when picking examples.
    candidates = [t for t in doc.tables if not _looks_like_page_header(t)]
    for category, kws in _REPRESENTATIVE_QUERIES.items():
        best, best_score = None, 0
        for t in candidates:
            if t.table_id in used:
                continue
            s = _score_table(t, kws)
            if s > best_score:
                best, best_score = t, s
        if best is None or best_score < 3:
            out.append({"category": category, "found": False})
            continue
        used.add(best.table_id)
        non_empty = sum(1 for c in best.cells if c.content.strip())
        out.append({
            "category": category,
            "found": True,
            "table_id": best.table_id,
            "page": best.page,
            "num_rows": best.num_rows,
            "num_cols": best.num_cols,
            "cells": len(best.cells),
            "non_empty_cells": non_empty,
            "merged_cells": sum(1 for c in best.cells if c.rowspan > 1 or c.colspan > 1),
            "header_cells": sum(1 for c in best.cells if c.is_header),
            "caption": best.caption,
            "score": best_score,
            "preview_rows": [row[:8] for row in best.grid[:max_rows]],
        })
    return out


def _looks_like_page_header(table: ParsedTable) -> bool:
    text = _table_text(table)
    return ("operating manual" in text and "page" in text and table.num_rows <= 6)


def validate_parsed_document(
    doc: ParsedDocument,
    expected_pages: int | None = None,
    min_text_ratio: float = 0.9,
) -> ParseValidationResult:
    """Run structural checks on the parsed document."""
    res = ParseValidationResult()
    pdf_pages = doc.total_pages
    requested_pages = int(doc.metadata.get("pages_requested") or pdf_pages)
    if requested_pages < pdf_pages:
        res.add(
            "page_limit_active",
            False,
            f"parser limited to first {requested_pages} of {pdf_pages} pages (max_pages set) - test run only",
            severity="warning",
        )
        pdf_pages = requested_pages
    pages_with_records = len(doc.pages)
    page_numbers = sorted(p.page_number for p in doc.pages)
    elements = [e for p in doc.pages for e in p.elements]
    type_counts = Counter(e.element_type.value for e in elements)
    label_counts = Counter(e.docling_label for e in elements)

    pages_with_text = {
        p.page_number for p in doc.pages
        if any(e.content.strip() and e.element_type not in (ElementType.FIGURE,) for e in p.elements)
    }
    pages_empty = [p for p in range(1, pdf_pages + 1) if p not in pages_with_text]

    # ---- page coverage -------------------------------------------------
    if expected_pages is not None:
        res.add(
            "expected_page_count",
            pdf_pages == expected_pages,
            f"PDF reports {pdf_pages} pages, expected ~{expected_pages}",
            severity="warning" if abs(pdf_pages - expected_pages) <= 5 else "error",
        )
    windows = doc.metadata.get("windows", [])
    covered = set()
    for w in windows:
        if w.get("status") in ("success", "partial_success"):
            covered.update(range(int(w["page_start"]), int(w["page_end"]) + 1))
    res.add(
        "pages_processed",
        len(covered) == pdf_pages,
        f"{len(covered)}/{pdf_pages} pages covered by successful conversion windows",
    )
    res.add(
        "windows_status",
        all(w.get("status") == "success" for w in windows),
        "window statuses: " + ", ".join(sorted(set(str(w.get("status")) for w in windows))),
        severity="warning" if any(w.get("status") == "partial_success" for w in windows) else "error",
    )
    res.add(
        "pages_with_text",
        len(pages_with_text) >= min_text_ratio * pdf_pages,
        f"{len(pages_with_text)}/{pdf_pages} pages have text content "
        f"({len(pages_with_text) / max(pdf_pages, 1):.1%}); empty pages: "
        f"{pages_empty[:30]}{' ...' if len(pages_empty) > 30 else ''}",
        severity="error" if len(pages_with_text) < 0.8 * pdf_pages else "warning",
    )
    # A run of 3+ consecutive empty pages is suspicious (catastrophic empties)
    runs, run = [], []
    for p in pages_empty:
        if run and p == run[-1] + 1:
            run.append(p)
        else:
            if len(run) >= 3:
                runs.append((run[0], run[-1]))
            run = [p]
    if len(run) >= 3:
        runs.append((run[0], run[-1]))
    res.add(
        "no_catastrophic_empty_runs",
        not runs,
        "consecutive empty page runs: " + (", ".join(f"{a}-{b}" for a, b in runs) or "none"),
        severity="warning",
    )

    # ---- provenance ----------------------------------------------------
    bad_prov = 0
    no_prov = 0
    for p in doc.pages:
        for e in p.elements:
            if not e.provenance:
                no_prov += 1
            elif e.provenance[0].page_no != p.page_number:
                bad_prov += 1
    res.add(
        "provenance_consistent",
        bad_prov == 0,
        f"{bad_prov} elements whose provenance page differs from their page record; "
        f"{no_prov} elements without provenance",
    )
    bbox_count = sum(1 for e in elements if e.bounding_box is not None)
    res.add(
        "bounding_boxes_present",
        bbox_count >= 0.95 * max(len(elements), 1),
        f"{bbox_count}/{len(elements)} elements carry a bounding box",
        severity="warning",
    )
    order_values = [e.reading_order for e in elements]
    res.add(
        "reading_order_unique",
        len(set(order_values)) == len(order_values),
        f"{len(order_values)} elements, {len(set(order_values))} distinct reading-order indices",
    )
    # Reading order should be non-decreasing with page number
    disorder = 0
    last = -1
    for p in doc.pages:
        for e in sorted(p.elements, key=lambda x: x.reading_order):
            if e.reading_order < last:
                disorder += 1
            last = e.reading_order
    res.add(
        "reading_order_monotonic_across_pages",
        disorder == 0,
        f"{disorder} reading-order inversions across page boundaries",
        severity="warning",
    )

    # ---- tables ----------------------------------------------------------
    tables = doc.tables
    tables_with_cells = [t for t in tables if t.cells]
    tables_with_text = [t for t in tables if any(c.content.strip() for c in t.cells)]
    total_cells = sum(len(t.cells) for t in tables)
    non_empty_cells = sum(1 for t in tables for c in t.cells if c.content.strip())
    merged_cells = sum(1 for t in tables for c in t.cells if c.rowspan > 1 or c.colspan > 1)
    header_tables = [t for t in tables if _looks_like_page_header(t)]
    res.add("tables_detected", len(tables) > 0, f"{len(tables)} tables detected")
    res.add(
        "table_cells_available",
        len(tables_with_cells) == len(tables) and total_cells > 0,
        f"{len(tables_with_cells)}/{len(tables)} tables have cells; {total_cells} cells total, "
        f"{non_empty_cells} non-empty ({non_empty_cells / max(total_cells, 1):.1%}), "
        f"{merged_cells} merged (rowspan/colspan>1)",
    )
    res.add(
        "tables_have_text",
        len(tables_with_text) >= 0.95 * max(len(tables), 1),
        f"{len(tables_with_text)}/{len(tables)} tables have textual cell content",
        severity="warning",
    )
    placeholder_tables = [
        e for e in elements
        if e.element_type == ElementType.TABLE and re.search(r"\(\d+ cells\)\s*$", e.content or "")
    ]
    res.add(
        "no_placeholder_table_text",
        not placeholder_tables,
        f"{len(placeholder_tables)} table elements reduced to '(N cells)' placeholders",
    )
    content_tables = len(tables) - len(header_tables)
    res.add(
        "content_tables_present",
        content_tables > 0,
        f"{content_tables} tables beyond the repeating page-header box ({len(header_tables)} header boxes)",
    )

    # ---- text -------------------------------------------------------------
    text_chars = sum(len(e.content) for e in elements if e.element_type in (
        ElementType.TEXT, ElementType.LIST_ITEM, ElementType.HEADING, ElementType.CAPTION,
        ElementType.FOOTNOTE, ElementType.EQUATION,
    ))
    res.add("text_extracted", text_chars > 0, f"{text_chars:,} characters of non-table text")
    res.add(
        "headings_detected",
        type_counts.get("heading", 0) > 0,
        f"{type_counts.get('heading', 0)} headings",
        severity="warning",
    )
    res.add(
        "parser_completed",
        not doc.errors,
        f"{len(doc.errors)} parser errors, {len(doc.warnings)} warnings",
        severity="error" if doc.errors else "warning",
    )

    env = doc.metadata.get("environment", {})
    res.stats = {
        "pdf_pages": pdf_pages,
        "pages_with_records": pages_with_records,
        "pages_with_text": len(pages_with_text),
        "empty_pages": pages_empty,
        "elements": len(elements),
        "element_type_counts": dict(type_counts),
        "docling_label_counts": dict(label_counts),
        "tables": len(tables),
        "page_header_tables": len(header_tables),
        "content_tables": content_tables,
        "table_cells": total_cells,
        "table_cells_non_empty": non_empty_cells,
        "table_cells_merged": merged_cells,
        "text_chars": text_chars,
        "headings": type_counts.get("heading", 0),
        "list_items": type_counts.get("list_item", 0),
        "equations": type_counts.get("equation", 0),
        "figures": type_counts.get("figure", 0),
        "images_exported": sum(int(w.get("images", 0) or 0) for w in windows),
        "warnings": len(doc.warnings),
        "errors": len(doc.errors),
        "ocr_engine": env.get("ocr_engine"),
        "ocr_backend": env.get("ocr_backend"),
        "onnxruntime_providers": env.get("onnxruntime_providers"),
        "accelerator_device": env.get("accelerator_device_resolved"),
        "cuda_available": env.get("cuda_available"),
        "gpu_name": env.get("gpu_name"),
        "gpu_total_memory_gb": env.get("gpu_total_memory_gb"),
        "torch_version": env.get("torch_version"),
        "docling_version": doc.parser_config.parser_version,
        "processing_time_seconds": doc.parse_duration_seconds,
        "window_count": len(windows),
        "max_rss_mb": max((w.get("rss_mb") or 0 for w in windows), default=None),
        "max_gpu_peak_mb": max((w.get("gpu_peak_mb") or 0 for w in windows), default=None),
    }
    res.representative_tables = find_representative_tables(doc)
    for rt in res.representative_tables:
        if rt.get("found"):
            ok = rt["non_empty_cells"] >= max(4, 0.3 * rt["cells"])
            res.add(
                f"representative:{rt['category']}",
                ok,
                f"{rt['table_id']} p{rt['page']} {rt['num_rows']}x{rt['num_cols']} "
                f"{rt['non_empty_cells']}/{rt['cells']} non-empty cells",
                severity="warning",
            )
        else:
            res.add(f"representative:{rt['category']}", False, "no matching table found", severity="warning")
    return res


def _fmt_seconds(s: float) -> str:
    s = float(s or 0)
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m:02d}m {sec:02d}s" if h else f"{m}m {sec:02d}s"


def write_parse_report(doc: ParsedDocument, result: ParseValidationResult, out_path: Path) -> Path:
    """Write parse_report.md."""
    s = result.stats
    lines: list[str] = []
    lines.append(f"# Parse Report: {doc.document_id}")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Source: `{doc.source_filename}` (sha256 `{doc.source_hash[:16]}...`)")
    lines.append(f"Overall: **{'PASSED' if result.passed else 'FAILED'}** "
                 f"({sum(1 for c in result.checks if c['ok'])}/{len(result.checks)} checks ok)")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    rows = [
        ("Pages in PDF", s["pdf_pages"]),
        ("Pages processed (with records)", s["pages_with_records"]),
        ("Pages with text", s["pages_with_text"]),
        ("Empty pages", len(s["empty_pages"])),
        ("Elements", s["elements"]),
        ("Text characters (non-table)", f"{s['text_chars']:,}"),
        ("Headings", s["headings"]),
        ("List items", s["list_items"]),
        ("Tables detected", s["tables"]),
        ("  of which page-header boxes", s["page_header_tables"]),
        ("  content tables", s["content_tables"]),
        ("Table cells", s["table_cells"]),
        ("  non-empty cells", s["table_cells_non_empty"]),
        ("  merged cells (rowspan/colspan>1)", s["table_cells_merged"]),
        ("Equations (formula regions)", s["equations"]),
        ("Figures", s["figures"]),
        ("Images exported", s["images_exported"]),
        ("Warnings", s["warnings"]),
        ("Errors", s["errors"]),
        ("OCR engine", s["ocr_engine"]),
        ("OCR provider", s["ocr_backend"]),
        ("ONNX Runtime providers", ", ".join(s.get("onnxruntime_providers") or [])),
        ("Layout/TableFormer device", s["accelerator_device"]),
        ("CUDA available (torch)", s["cuda_available"]),
        ("GPU", f"{s['gpu_name']} ({s['gpu_total_memory_gb']} GB)" if s.get("gpu_name") else "n/a"),
        ("torch", s["torch_version"]),
        ("Docling", s["docling_version"]),
        ("Windows", s["window_count"]),
        ("Peak process RSS (MB)", s["max_rss_mb"]),
        ("Peak GPU allocation (MB)", s["max_gpu_peak_mb"]),
        ("Processing time", _fmt_seconds(s["processing_time_seconds"])),
    ]
    for k, v in rows:
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## Element types")
    lines.append("")
    lines.append("| Element type | Count |")
    lines.append("|---|---|")
    for k, v in sorted(s["element_type_counts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("| Docling label | Count |")
    lines.append("|---|---|")
    for k, v in sorted(s["docling_label_counts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## Validation checks")
    lines.append("")
    lines.append("| Check | Result | Severity | Detail |")
    lines.append("|---|---|---|---|")
    for c in result.checks:
        mark = "OK" if c["ok"] else ("WARN" if c["severity"] == "warning" else "FAIL")
        lines.append(f"| {c['check']} | {mark} | {c['severity']} | {c['detail']} |")
    lines.append("")
    lines.append("## Representative tables (real cell content check)")
    lines.append("")
    for rt in result.representative_tables:
        lines.append(f"### {rt['category']}")
        if not rt.get("found"):
            lines.append("")
            lines.append("_No matching table found by keyword search._")
            lines.append("")
            continue
        lines.append("")
        lines.append(f"`{rt['table_id']}` page {rt['page']} — {rt['num_rows']}x{rt['num_cols']}, "
                     f"{rt['non_empty_cells']}/{rt['cells']} non-empty cells, "
                     f"{rt['merged_cells']} merged, {rt['header_cells']} header cells"
                     + (f", caption: {rt['caption']}" if rt.get("caption") else ""))
        lines.append("")
        if rt["preview_rows"]:
            width = max(len(r) for r in rt["preview_rows"])
            lines.append("| " + " | ".join(f"c{i}" for i in range(width)) + " |")
            lines.append("|" + "---|" * width)
            for row in rt["preview_rows"]:
                cells = [c.replace("|", "\\|").replace("\n", " ")[:60] for c in row] + [""] * (width - len(row))
                lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    lines.append("## Windows")
    lines.append("")
    lines.append("| # | Pages | Status | Seconds | Elements | Tables | Images | RSS MB | GPU peak MB | Warn | Err |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for w in doc.metadata.get("windows", []):
        lines.append(
            f"| {w['window_index']} | {w['page_start']}-{w['page_end']} | {w['status']} | "
            f"{w['duration_seconds']} | {w['elements']} | {w['tables']} | {w['images']} | "
            f"{w.get('rss_mb')} | {w.get('gpu_peak_mb')} | {w['warnings']} | {w['errors']} |"
        )
    lines.append("")
    if doc.errors:
        lines.append("## Errors")
        lines.append("")
        for e in doc.errors[:100]:
            lines.append(f"- {e}")
        lines.append("")
    if doc.warnings:
        lines.append("## Warnings")
        lines.append("")
        counts = Counter(re.sub(r"\d+", "#", w) for w in doc.warnings)
        for w, n in counts.most_common(40):
            lines.append(f"- ({n}x) {w}")
        lines.append("")
    lines.append("## Environment")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(doc.metadata.get("environment", {}), indent=2, default=str))
    lines.append("```")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Parse report written: {out_path}")
    return out_path

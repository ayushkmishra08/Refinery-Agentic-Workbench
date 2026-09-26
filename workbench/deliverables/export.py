"""Entry point: ``export_response(resp, fmt, out_dir)`` -> ExportResult."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from pydantic import BaseModel

from workbench.deliverables.common import (
    DeliverableSpec, audit_line, calculation_steps_from_response, evidence_rows, figures_from_response, spec_for,
    strip_banner,
)

FORMATS = ("docx", "pptx", "xlsx", "md")


class ExportResult(BaseModel):
    path: str
    fmt: str
    bytes: int
    sha256: str
    figures_count: int
    evidence_count: int
    status_line: str
    classification: str | None = None
    title: str = ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _filename(resp, fmt: str) -> str:
    rid = re.sub(r"[^A-Za-z0-9._-]+", "_", str(getattr(resp, "response_id", "response")))[:60]
    return f"{time.strftime('%Y%m%d-%H%M%S')}_{rid}.{fmt}"


def export_markdown(resp, out_dir: Path, *, title: str | None = None, spec: DeliverableSpec | None = None) -> ExportResult:
    spec = spec or spec_for(resp, title=title)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _filename(resp, "md")
    lines = [f"# {spec.title}", "", f"_{spec.classification or 'UNCLASSIFIED'} · {spec.status_line}_", "", strip_banner(resp.answer_markdown), ""]
    steps = calculation_steps_from_response(resp)
    if steps:
        lines += ["## Calculation steps", "", "| Step | Expression | Result | Note | Ref |", "|---|---|---|---|---|"]
        lines += [f"| {s['step']} | {s['expression']} | {s['result']} | {s['note']} | {', '.join(r for r in s['refs'] if r)} |" for s in steps]
        lines.append("")
    figs = figures_from_response(resp)
    lines += ["## Provenance", "", "| Figure | Value | Unit | Document | Page | Ref |", "|---|---|---|---|---|---|"]
    lines += [f"| {f['label']} | {f['value']} | {f['unit'] or ''} | {f['document_id'] or '—'} | {f['page'] or '—'} | {f['citation'] or ''} |" for f in figs]
    lines += ["", "## Evidence", ""]
    lines += [f"- {e['ref']} {e['document_id']} p.{e['page']} ({e['source']}): {e['text'][:300]}" for e in evidence_rows(resp)]
    lines += ["", f"_{audit_line(resp)}_", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return ExportResult(path=str(path), fmt="md", bytes=path.stat().st_size, sha256=_sha256(path), figures_count=len(figs),
                        evidence_count=len(resp.evidence), status_line=spec.status_line, classification=spec.classification, title=spec.title)


def export_response(resp, fmt: str, out_dir: Path, *, title: str | None = None, author: str = "Refinery Engineering AI Workbench",
                    figures=None, signed_off: bool = False) -> ExportResult:
    """Write the response as a Word, PowerPoint, Excel or Markdown file and describe the file."""
    fmt = fmt.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format {fmt!r}; choose one of {', '.join(FORMATS)}")
    spec = spec_for(resp, title=title, author=author, signed_off=signed_off)
    if fmt == "md":
        return export_markdown(resp, out_dir, title=title, spec=spec)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _filename(resp, fmt)
    if fmt == "docx":
        from workbench.deliverables.docx_builder import build_docx

        build_docx(resp, spec, path, figures=figures)
    elif fmt == "pptx":
        from workbench.deliverables.pptx_builder import build_pptx

        build_pptx(resp, spec, path, figures=figures)
    else:
        from workbench.deliverables.xlsx_builder import build_xlsx

        build_xlsx(resp, spec, path, figures=figures)
    figs = figures if figures is not None else figures_from_response(resp)
    return ExportResult(path=str(path), fmt=fmt, bytes=path.stat().st_size, sha256=_sha256(path), figures_count=len(figs),
                        evidence_count=len(resp.evidence), status_line=spec.status_line, classification=spec.classification, title=spec.title)

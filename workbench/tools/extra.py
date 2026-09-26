"""Tools that live outside workbench/tools: on-device OCR / vision, and the deliverable builders.

Registered by the orchestrator on top of ``default_registry()`` so the agent loop can read a
scan, describe a P&ID, and turn gathered material into a Word / Excel / PowerPoint file — all
inside the same session workspace, all logged to the same chained tool log.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from workbench.tools.base import Tool, ToolContext, ToolError, ToolResult, confine


def _resolve_input(ctx: ToolContext, rel: str) -> Path:
    """A file inside the workspace, or one the session uploaded (its uploads directory is a sibling tree)."""
    try:
        p = confine(ctx.workspace, rel)
        if p.exists():
            return p
    except ToolError:
        pass
    extra = ctx.extras.get("uploads_dir") if ctx.extras else None
    if extra:
        cand = (Path(extra) / rel).resolve()
        if cand.exists() and Path(extra).resolve() in cand.parents:
            return cand
    raise ToolError(f"no such file in the workspace: {rel!r}")


class OcrImageTool(Tool):
    name = "ocr_image"
    description = ("On-device OCR (RapidOCR, CPU) over an image or a PDF page from the workspace. Returns the text "
                   "with a confidence per line; lines under the threshold are flagged for human review.")
    parameters = {"type": "object",
                  "properties": {"path": {"type": "string"}, "page": {"type": "integer", "description": "PDF page index (0-based)"},
                                 "threshold": {"type": "number"}},
                  "required": ["path"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        from workbench.intake.ocr import ocr_image, ocr_pdf

        src = _resolve_input(ctx, args["path"])
        threshold = float(args.get("threshold") or 0.6)
        if src.suffix.lower() == ".pdf":
            res = ocr_pdf(src, pages=[int(args.get("page") or 0)], threshold=threshold, force=True)
        else:
            res = ocr_image(src, threshold=threshold)
        flagged = [l for l in res.lines if l.confidence < threshold]
        out = res.text.strip() or "(no text recognised)"
        steps = [f"{len(res.lines)} line(s), mean confidence {res.mean_confidence:.2f}, {len(flagged)} below {threshold}"]
        evidence = [{"document_id": src.name, "page": (l.page if l.page is not None else args.get("page")), "text": l.text,
                     "confidence": l.confidence, "source": "ocr"} for l in res.lines[:60]]
        return ToolResult(tool=self.name, ok=True, output=out[:4000], steps=steps, evidence=evidence,
                          data={"lines": [l.model_dump() for l in res.lines], "mean_confidence": res.mean_confidence,
                                "flagged": [l.model_dump() for l in flagged], "engine": res.engine, "duration_ms": res.duration_ms})


class DescribeImageTool(Tool):
    name = "describe_image"
    description = ("Read an image with the local vision model: a P&ID (equipment tags, lines, instruments), a "
                   "handwritten note (literal transcription), a photo of a gauge (the reading). Never guesses "
                   "values that are not visible.")
    parameters = {"type": "object",
                  "properties": {"path": {"type": "string"},
                                 "purpose": {"type": "string", "description": "general | pid | handwriting | photo | gauge"}},
                  "required": ["path"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        from workbench.intake.vision import describe_image

        src = _resolve_input(ctx, args["path"])
        note = describe_image(src, ctx.resources or ctx.llm, purpose=args.get("purpose") or "general")
        if not note.ok:
            return ToolResult.failure(self.name, note.error or "vision model unavailable")
        return ToolResult(tool=self.name, ok=True, output=note.text, steps=[f"{note.model} in {note.duration_ms} ms"],
                          evidence=[{"document_id": src.name, "page": None, "text": note.text[:400], "source": "vision"}],
                          data=note.model_dump(mode="json"))


class _MakeDeliverable(Tool):
    fmt = "docx"
    parameters = {"type": "object",
                  "properties": {"title": {"type": "string"},
                                 "content": {"type": "string", "description": "Markdown body: headings, bullets, tables"},
                                 "response_id": {"type": "string", "description": "a released answer to export instead of raw content"},
                                 "figures": {"type": "array", "description": "[{label, value, unit, source}] to list under Provenance"},
                                 "filename": {"type": "string"}},
                  "required": ["title"]}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        from workbench.deliverables.export import export_response

        resp = None
        store = ctx.extras.get("responses") if ctx.extras else None
        if args.get("response_id") and store is not None:
            found = store.load(args["response_id"])
            if found is None:
                raise ToolError(f"no released answer {args['response_id']!r}")
            resp = found[0]
        if resp is None:
            resp = _response_from_content(args["title"], args.get("content") or "", ctx)
        out_dir = ctx.workspace / "deliverables"
        result = export_response(resp, self.fmt, out_dir, title=args["title"], figures=None)
        rel = Path(result.path).relative_to(ctx.workspace.resolve()).as_posix()
        if args.get("filename"):
            target = confine(ctx.workspace, f"deliverables/{Path(args['filename']).name}")
            Path(result.path).replace(target)
            rel = target.relative_to(ctx.workspace.resolve()).as_posix()
        return ToolResult(tool=self.name, ok=True, output=f"{self.fmt} written: {rel} ({result.bytes} bytes). {result.status_line}",
                          files=[rel], steps=[result.status_line],
                          data={**result.model_dump(mode="json"), "path": rel})


def _response_from_content(title: str, content: str, ctx: ToolContext):
    """Wrap free content produced during an agent run as a FinalResponse so the builders can render it."""
    from workbench.core.evidence import Confidence
    from workbench.core.request import TaskType
    from workbench.core.result import FinalResponse, SecurityEnvelope
    from workbench.core.blocks import AuditBlock, TextBlock

    principal = ctx.principal
    env = SecurityEnvelope(access_control=True, principal=getattr(principal, "username", "tool"),
                           role=getattr(getattr(principal, "role", None), "value", "user"), authenticated=True)
    return FinalResponse(response_id=f"resp-tool-{int(time.time())}", session_id=ctx.session_key, task_type=TaskType.REPORT,
                         status="answered", answer_markdown=content or f"# {title}",
                         blocks=[TextBlock(id="body", markdown=content or ""), AuditBlock(id="audit", audit_id="tool", phases=[])],
                         confidence=Confidence(score=0.5, basis="content assembled by the tool agent; figures need review"),
                         requires_human_review=True, review_reason="deliverable produced by an agent run", audit_trail_id="tool",
                         security=env)


class MakeDocxTool(_MakeDeliverable):
    name = "make_docx"
    fmt = "docx"
    description = "Write a Word document (.docx) from a released answer or from Markdown content; classification in the header, provenance and audit at the end, status 'pending human sign-off'."


class MakeXlsxTool(_MakeDeliverable):
    name = "make_xlsx"
    fmt = "xlsx"
    description = "Write an Excel workbook (.xlsx): Answer, Values, Calculation (with live formulas), Provenance, Evidence, Audit sheets."


class MakePptxTool(_MakeDeliverable):
    name = "make_pptx"
    fmt = "pptx"
    description = "Write a PowerPoint deck (.pptx) summarising a released answer with provenance and a sign-off footer on every slide."


def extra_tools() -> list[Tool]:
    return [OcrImageTool(), DescribeImageTool(), MakeDocxTool(), MakeXlsxTool(), MakePptxTool()]

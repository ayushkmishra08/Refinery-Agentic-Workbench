"""The intake pipeline: one file in, an IntakeResult out.

- a PDF with a text layer   -> ``pdf_text``: text taken verbatim; figures on the first pages described by the vision model
- a PDF without one (a scan) -> ``pdf_scanned``: every page OCR'd, first page also shown to the vision model
- an image                   -> ``image``: OCR + vision

``flagged`` is the review queue: every OCR line under the confidence threshold and every
``[illegible]`` span the vision model admitted to. Nothing low-confidence is passed through as
fact — the review registry turns these into open flags a reviewer must resolve before sign-off.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from workbench.intake.ocr import OcrLine, OcrResult, is_scanned_pdf, ocr_image, ocr_pdf, render_pdf_page
from workbench.intake.vision import VisionNote, describe_image

logger = logging.getLogger(__name__)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
DRAWING_RE = re.compile(r"\b(p&id|p & id|piping and instrumentation|drawing|diagram|schematic|flow ?sheet|pfd)\b", re.IGNORECASE)


class FlaggedItem(BaseModel):
    page: int | None = None
    text: str
    confidence: float
    reason: str
    source: str = "ocr"                       # ocr | vision


class IntakeResult(BaseModel):
    source: str
    kind: str                                  # pdf_text | pdf_scanned | image
    pages: int = 0
    ocr: OcrResult | None = None
    vision: list[VisionNote] = Field(default_factory=list)
    figures_ocr: list[OcrResult] = Field(default_factory=list)
    flagged: list[FlaggedItem] = Field(default_factory=list)
    text: str = ""
    summary: dict = Field(default_factory=dict)
    duration_ms: int = 0
    threshold: float = 0.6


def _page_has_figure(path: Path, index: int) -> bool:
    """Does this page carry a raster or a vector drawing? pypdfium2 exposes page objects."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw

        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[index]
            kinds = {raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH}
            n_img = n_path = 0
            for obj in page.get_objects(max_depth=1):
                if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                    n_img += 1
                elif obj.type == raw.FPDF_PAGEOBJ_PATH:
                    n_path += 1
            return n_img > 0 or n_path > 40
        finally:
            doc.close()
    except Exception:
        return False


def _page_text(path: Path, index: int) -> str:
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        try:
            return doc[index].get_textpage().get_text_range() or ""
        finally:
            doc.close()
    except Exception:
        return ""


def _flags_from_ocr(ocr: OcrResult, threshold: float) -> list[FlaggedItem]:
    return [FlaggedItem(page=ln.page, text=ln.text, confidence=ln.confidence,
                        reason=f"low OCR confidence {ln.confidence:.2f} (threshold {threshold:.2f})", source="ocr")
            for ln in ocr.lines if ln.confidence < threshold]


def _flags_from_vision(note: VisionNote) -> list[FlaggedItem]:
    if not note.ok or not note.illegible_spans:
        return []
    return [FlaggedItem(page=None, text=f"{note.illegible_spans} span(s) marked [illegible] in {Path(note.path).name}",
                        confidence=0.0, reason="vision model could not read part of the image", source="vision")]


def intake_file(path: str | Path, *, llm_or_resources: Any = None, threshold: float = 0.6, run_vision: bool = True,
                max_vision_calls: int = 2, max_pages: int | None = None, purpose: str = "general",
                dpi: int = 150, scratch_dir: Path | None = None) -> IntakeResult:
    """Read one file with OCR and, when a vision model is at hand, with the vision model."""
    t0 = time.time()
    path = Path(path)
    suffix = path.suffix.lower()
    # Renders go to a scratch area, never next to the source file: a sample document folder is not a place for artefacts.
    import tempfile

    scratch = Path(scratch_dir) if scratch_dir else Path(tempfile.gettempdir()) / "refinery_intake" / path.stem
    vision_budget = max_vision_calls if (run_vision and llm_or_resources is not None) else 0

    if suffix in IMAGE_SUFFIXES:
        res = IntakeResult(source=str(path), kind="image", pages=1, threshold=threshold)
        try:
            res.ocr = ocr_image(path, threshold=threshold, page=0)
            res.flagged.extend(_flags_from_ocr(res.ocr, threshold))
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", path, exc)
            res.summary["ocr_error"] = str(exc)
        if vision_budget:
            note = describe_image(path, llm_or_resources, purpose=purpose)
            res.vision.append(note)
            res.flagged.extend(_flags_from_vision(note))
        res.text = (res.ocr.text if res.ocr else "") or "\n".join(v.text for v in res.vision if v.ok)
    elif suffix == ".pdf":
        scanned = is_scanned_pdf(path)
        res = IntakeResult(source=str(path), kind="pdf_scanned" if scanned else "pdf_text", threshold=threshold)
        res.ocr = ocr_pdf(path, threshold=threshold, max_pages=max_pages, dpi=dpi, force=False)
        res.pages = res.ocr.page_count
        res.text = res.ocr.text
        res.flagged.extend(_flags_from_ocr(res.ocr, threshold))
        if vision_budget:
            scratch.mkdir(parents=True, exist_ok=True)
            candidates = [0] if scanned else [i for i in range(res.pages) if _page_has_figure(path, i)]
            for i in candidates[:vision_budget]:
                try:
                    pil = render_pdf_page(path, i, dpi=dpi)
                    out = scratch / f"{path.stem}_p{i}.png"
                    pil.save(out)
                except Exception as exc:
                    logger.warning("page %s could not be rendered: %s", i, exc)
                    continue
                page_purpose = "pid" if DRAWING_RE.search(_page_text(path, i)) else ("handwriting" if scanned and purpose == "handwriting" else "general")
                note = describe_image(out, llm_or_resources, purpose=page_purpose,
                                      extra_prompt=f"This is page {i + 1} of {path.name}.")
                res.vision.append(note)
                res.flagged.extend(_flags_from_vision(note))
                # figure crops on a born-digital page: OCR the raster too, to catch text inside drawings
                if not scanned:
                    try:
                        fig = ocr_image(pil, threshold=threshold, page=i, source=f"{path.name}#p{i}:figure")
                        res.figures_ocr.append(fig)
                    except Exception as exc:
                        logger.debug("figure OCR failed on page %s: %s", i, exc)
    else:
        res = IntakeResult(source=str(path), kind="image", pages=0, threshold=threshold)
        res.summary["error"] = f"unsupported file type {suffix}"

    res.duration_ms = int((time.time() - t0) * 1000)
    res.summary.update({
        "kind": res.kind,
        "pages": res.pages,
        "ocr_lines": len(res.ocr.lines) if res.ocr else 0,
        "ocr_mean_confidence": res.ocr.mean_confidence if res.ocr else None,
        "text_layer_lines": sum(1 for ln in res.ocr.lines if ln.note == "text layer") if res.ocr else 0,
        "vision_calls": len(res.vision),
        "vision_ok": sum(1 for v in res.vision if v.ok),
        "tags_seen": sorted({t for v in res.vision for t in v.tags_found}),
        "figure_ocr_lines": sum(len(f.lines) for f in res.figures_ocr),
        "flagged": len(res.flagged),
        "duration_ms": res.duration_ms,
    })
    return res


def intake_to_chunks(result: IntakeResult, *, max_chars: int = 1500) -> list[dict]:
    """Rows a session backend can index: {text, page, source, confidence}."""
    rows: list[dict] = []
    if result.ocr is not None:
        by_page: dict[int | None, list[OcrLine]] = {}
        for ln in result.ocr.lines:
            by_page.setdefault(ln.page, []).append(ln)
        for page, lines in by_page.items():
            buf: list[OcrLine] = []
            size = 0
            for ln in lines:
                if size + len(ln.text) > max_chars and buf:
                    rows.append(_chunk(buf, page))
                    buf, size = [], 0
                buf.append(ln)
                size += len(ln.text) + 1
            if buf:
                rows.append(_chunk(buf, page))
    for fig in result.figures_ocr:
        if fig.lines:
            rows.append({**_chunk(fig.lines, fig.lines[0].page), "source": "ocr", "note": "figure"})
    for note in result.vision:
        if note.ok:
            rows.append({"text": note.text, "page": None, "source": "vision", "confidence": 0.5,
                         "note": f"{note.purpose} description by {note.model}"})
    return rows


def _chunk(lines: list[OcrLine], page: int | None) -> dict:
    source = "text_layer" if all(ln.note == "text layer" for ln in lines) else "ocr"
    conf = min(ln.confidence for ln in lines) if lines else 0.0
    return {"text": "\n".join(ln.text for ln in lines), "page": page, "source": source, "confidence": round(conf, 4)}

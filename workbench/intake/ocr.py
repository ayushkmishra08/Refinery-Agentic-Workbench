"""On-device OCR with RapidOCR (PaddleOCR models on ONNX Runtime, CPU).

``ocr_image`` reads one raster; ``ocr_pdf`` rasterises pages with pypdfium2 and reads them,
unless a page already has a text layer, in which case the text layer is taken verbatim (and
marked confidence 1.0, note "text layer") — OCR of a born-digital page only adds errors.

Every line carries the recogniser's confidence. The threshold decides what is *flagged*, never
what is dropped: a misread pressure value must reach the reviewer, not vanish.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_ENGINE = None
_ENGINE_LOCK = threading.Lock()
_ENGINE_ERROR: str | None = None
TEXT_LAYER_MIN_CHARS = 40


class OcrLine(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[float] = Field(default_factory=list, description="[x0, y0, x1, y1] in pixels of the raster read")
    page: int | None = None
    note: str = ""                       # "text layer" when taken from the PDF text layer


class OcrResult(BaseModel):
    source: str
    page_count: int = 1
    lines: list[OcrLine] = Field(default_factory=list)
    text: str = ""
    mean_confidence: float = 0.0
    low_confidence_lines: int = 0
    threshold: float = 0.6
    engine: str = "rapidocr"
    duration_ms: int = 0
    notes: list[str] = Field(default_factory=list)

    def flagged(self) -> list[OcrLine]:
        return [ln for ln in self.lines if ln.confidence < self.threshold]


def _engine():
    """One RapidOCR instance per process (its ONNX sessions take ~1 s to build)."""
    global _ENGINE, _ENGINE_ERROR
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        if _ENGINE_ERROR is not None:
            raise RuntimeError(_ENGINE_ERROR)
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            import logging as _logging

            _logging.getLogger("RapidOCR").setLevel(_logging.WARNING)
            from rapidocr import RapidOCR

            _ENGINE = RapidOCR()
            return _ENGINE
        except Exception as exc:  # models missing, onnxruntime absent
            _ENGINE_ERROR = f"RapidOCR unavailable: {exc}"
            raise RuntimeError(_ENGINE_ERROR) from exc


def engine_available() -> bool:
    try:
        _engine()
        return True
    except Exception:
        return False


def _as_array(image: Any):
    import numpy as np
    from PIL import Image

    if isinstance(image, (str, Path)):
        image = Image.open(image)
    if isinstance(image, Image.Image):
        return np.asarray(image.convert("RGB"))
    return image


def _parse_output(out: Any) -> list[tuple[str, float, list[float]]]:
    """RapidOCR 3.x returns an object with .boxes/.txts/.scores; 1.x/2.x a (list, elapse) tuple."""
    rows: list[tuple[str, float, list[float]]] = []
    txts = getattr(out, "txts", None)
    if txts is not None:
        scores = getattr(out, "scores", None) or ()
        boxes = getattr(out, "boxes", None)
        for i, t in enumerate(txts):
            score = float(scores[i]) if i < len(scores) else 0.0
            bbox: list[float] = []
            try:
                if boxes is not None:
                    pts = boxes[i]
                    xs = [float(p[0]) for p in pts]
                    ys = [float(p[1]) for p in pts]
                    bbox = [min(xs), min(ys), max(xs), max(ys)]
            except Exception:
                bbox = []
            rows.append((str(t), max(0.0, min(1.0, score)), bbox))
        return rows
    if isinstance(out, tuple):
        out = out[0]
    if isinstance(out, list):
        for item in out:
            try:
                pts, t, score = item[0], item[1], float(item[2])
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                rows.append((str(t), max(0.0, min(1.0, score)), [min(xs), min(ys), max(xs), max(ys)]))
            except Exception:
                continue
    return rows


def _reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Top-to-bottom, then left-to-right, with lines whose vertical centres are close treated as one row."""
    if not lines or any(not ln.bbox for ln in lines):
        return lines
    heights = [max(1.0, ln.bbox[3] - ln.bbox[1]) for ln in lines]
    tol = max(4.0, sorted(heights)[len(heights) // 2] * 0.6)
    ordered = sorted(lines, key=lambda ln: ((ln.bbox[1] + ln.bbox[3]) / 2, ln.bbox[0]))
    rows: list[list[OcrLine]] = []
    for ln in ordered:
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        if rows and abs(((rows[-1][0].bbox[1] + rows[-1][0].bbox[3]) / 2) - cy) <= tol:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    out: list[OcrLine] = []
    for row in rows:
        out.extend(sorted(row, key=lambda ln: ln.bbox[0]))
    return out


def _finish(result: OcrResult, t0: float) -> OcrResult:
    result.lines = _reading_order(result.lines) if result.page_count == 1 else result.lines
    result.text = "\n".join(ln.text for ln in result.lines)
    result.mean_confidence = round(sum(ln.confidence for ln in result.lines) / len(result.lines), 4) if result.lines else 0.0
    result.low_confidence_lines = sum(1 for ln in result.lines if ln.confidence < result.threshold)
    result.duration_ms = int((time.time() - t0) * 1000)
    return result


def ocr_image(image: Any, *, threshold: float = 0.6, page: int | None = None, source: str | None = None) -> OcrResult:
    """OCR one image (path, PIL image or numpy array)."""
    t0 = time.time()
    src = source or (str(image) if isinstance(image, (str, Path)) else "image")
    result = OcrResult(source=src, threshold=threshold)
    arr = _as_array(image)
    out = _engine()(arr)
    for text, score, bbox in _parse_output(out):
        if text.strip():
            result.lines.append(OcrLine(text=text.strip(), confidence=score, bbox=bbox, page=page))
    return _finish(result, t0)


def _page_text(page) -> str:
    try:
        return page.get_textpage().get_text_range() or ""
    except Exception:
        return ""


def is_scanned_pdf(path: str | Path, *, sample_pages: int = 3) -> bool:
    """True when the first pages carry no usable text layer (a scan, or a drawing exported as image)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        n = min(len(doc), sample_pages)
        if n == 0:
            return False
        with_text = sum(1 for i in range(n) if len(_page_text(doc[i]).strip()) >= TEXT_LAYER_MIN_CHARS)
        return with_text == 0
    finally:
        doc.close()


def render_pdf_page(path: str | Path, page_index: int, *, dpi: int = 150):
    """The page as a PIL image (used by the vision step and by OCR)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[page_index]
        return page.render(scale=dpi / 72).to_pil()
    finally:
        doc.close()


def ocr_pdf(path: str | Path, *, pages: list[int] | None = None, dpi: int = 150, threshold: float = 0.6,
            force: bool = False, max_pages: int | None = None) -> OcrResult:
    """Read a PDF page by page: the text layer where one exists (unless ``force``), OCR otherwise."""
    import pypdfium2 as pdfium

    t0 = time.time()
    path = Path(path)
    result = OcrResult(source=str(path), threshold=threshold)
    doc = pdfium.PdfDocument(str(path))
    try:
        total = len(doc)
        indices = list(pages) if pages is not None else list(range(total))
        if max_pages is not None:
            indices = indices[:max_pages]
        indices = [i for i in indices if 0 <= i < total]
        result.page_count = len(indices)
        for i in indices:
            page = doc[i]
            layer = _page_text(page)
            if not force and len(layer.strip()) >= TEXT_LAYER_MIN_CHARS:
                for raw in layer.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                    if raw.strip():
                        result.lines.append(OcrLine(text=raw.strip(), confidence=1.0, page=i, note="text layer"))
                result.notes.append(f"page {i}: text layer ({len(layer)} chars)")
                continue
            pil = page.render(scale=dpi / 72).to_pil()
            part = ocr_image(pil, threshold=threshold, page=i, source=f"{path.name}#p{i}")
            result.lines.extend(part.lines)
            result.notes.append(f"page {i}: OCR {len(part.lines)} lines, mean confidence {part.mean_confidence:.2f}")
    finally:
        doc.close()
    return _finish(result, t0)

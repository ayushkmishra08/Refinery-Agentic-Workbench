"""Docling PDF parser with bounded page-window processing.

Design goals (GTX 1650 4 GB VRAM, ~12 GB RAM laptop):
  * Convert the PDF in bounded page windows (``page_range``) so that only a
    handful of rendered pages, layout predictions and table cells live in
    memory at any time.  Docling model weights are loaded once and reused
    across windows.
  * Persist a *complete* representation per window (Docling's native
    ``export_to_dict()`` plus our normalized ``ParsedDocument`` slice) so
    that an interrupted run resumes at the last completed window.
  * Preserve Docling's reading order (depth-first walk of the body tree),
    page provenance, bounding boxes, heading levels, list grouping, table
    cells with row/col spans and header flags, figures (with exported
    images) and formula regions.

Output layout::

    data/parsed/<document_id>/
        parsed_document.json      ParsedDocument (all pages, elements, tables)
        tables.json               all tables with cells, spans, grid, markdown
        metadata.json             environment + per-window timing + warnings
        images/                   exported figure crops (PNG)
        windows/                  per-window Docling JSON + parsed slices
        parse_report.md           validation report (written by parse_validation)
"""

from __future__ import annotations

import gc
import hashlib
import json
import logging
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge_layer.schemas.parsed_document import (
    BoundingBox,
    ElementType,
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    ParsedTable,
    ParsedTableCell,
    ParserConfig,
    Provenance,
)
from knowledge_layer.config import PipelineConfig

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of a file for integrity checking."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _make_element_id(doc_id: str, page: int, position: int) -> str:
    """Create a deterministic element ID."""
    return f"{doc_id}_p{page}_e{position}"


def _make_table_id(doc_id: str, page: int, table_index: int) -> str:
    """Create a deterministic table ID."""
    return f"{doc_id}_p{page}_t{table_index}"


_LABEL_MAP: dict[str, ElementType] = {
    "title": ElementType.HEADING,
    "section_header": ElementType.HEADING,
    "section-header": ElementType.HEADING,
    "heading": ElementType.HEADING,
    "text": ElementType.TEXT,
    "paragraph": ElementType.TEXT,
    "reference": ElementType.TEXT,
    "document_index": ElementType.TEXT,
    "list_item": ElementType.LIST_ITEM,
    "list-item": ElementType.LIST_ITEM,
    "table": ElementType.TABLE,
    "figure": ElementType.FIGURE,
    "picture": ElementType.FIGURE,
    "chart": ElementType.FIGURE,
    "formula": ElementType.EQUATION,
    "equation": ElementType.EQUATION,
    "code": ElementType.CODE,
    "page_header": ElementType.PAGE_HEADER,
    "page-header": ElementType.PAGE_HEADER,
    "page_footer": ElementType.PAGE_FOOTER,
    "page-footer": ElementType.PAGE_FOOTER,
    "caption": ElementType.CAPTION,
    "footnote": ElementType.FOOTNOTE,
}


def _map_docling_label(label: str) -> ElementType:
    """Map Docling's content label to our ElementType enum."""
    label_lower = label.lower().strip() if label else ""
    return _LABEL_MAP.get(label_lower, ElementType.UNKNOWN)


def _extract_heading_level(item: object) -> int | None:
    """Try to extract heading level from a Docling content item."""
    label = str(getattr(getattr(item, "label", ""), "value", getattr(item, "label", ""))).lower()
    if label == "title":
        return 1
    level = getattr(item, "level", None)
    if isinstance(level, int):
        # Docling section headers are 1-based; keep title at 1, sections at 2+
        return level + 1
    if label in ("section_header", "section-header"):
        return 2
    return None


def _bbox_from_docling(bbox: Any) -> BoundingBox | None:
    if bbox is None:
        return None
    try:
        origin = getattr(bbox.coord_origin, "value", str(bbox.coord_origin))
        return BoundingBox(l=float(bbox.l), t=float(bbox.t), r=float(bbox.r), b=float(bbox.b),
                           coord_origin=str(origin))
    except Exception:
        return None


def _provs_from_docling(item: Any) -> list[Provenance]:
    provs: list[Provenance] = []
    for p in getattr(item, "prov", None) or []:
        try:
            charspan = None
            cs = getattr(p, "charspan", None)
            if cs is not None and len(cs) == 2:
                charspan = (int(cs[0]), int(cs[1]))
            provs.append(Provenance(page_no=int(p.page_no), bbox=_bbox_from_docling(p.bbox), charspan=charspan))
        except Exception:
            continue
    return provs


def _dense_grid(cells: list[ParsedTableCell], num_rows: int, num_cols: int) -> list[list[str]]:
    """Build a dense text grid with merged cells propagated into spanned positions."""
    if num_rows <= 0 or num_cols <= 0:
        return []
    grid = [["" for _ in range(num_cols)] for _ in range(num_rows)]
    for c in cells:
        for r in range(c.row, min(c.row + max(c.rowspan, 1), num_rows)):
            for k in range(c.col, min(c.col + max(c.colspan, 1), num_cols)):
                if r >= 0 and k >= 0:
                    grid[r][k] = c.content
    return grid


def _grid_to_text(grid: list[list[str]]) -> str:
    """Render a grid as pipe-separated rows (used as the table element's text content)."""
    lines = []
    for row in grid:
        lines.append(" | ".join(cell.replace("\n", " ").strip() for cell in row))
    return "\n".join(lines)


def _runtime_environment(accelerator_device: str) -> dict[str, Any]:
    """Collect versions and hardware status for the parse metadata."""
    env: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "accelerator_device_requested": accelerator_device,
    }
    try:
        import importlib.metadata as md
        for pkg in ("docling", "docling-core", "docling-ibm-models", "docling-parse",
                    "rapidocr", "onnxruntime", "onnxruntime-gpu", "torch", "torchvision",
                    "transformers", "pypdfium2"):
            try:
                env[f"pkg:{pkg}"] = md.version(pkg)
            except md.PackageNotFoundError:
                env[f"pkg:{pkg}"] = None
    except Exception:
        pass
    try:
        import torch
        env["torch_version"] = torch.__version__
        env["torch_cuda_build"] = torch.version.cuda
        env["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            env["gpu_name"] = props.name
            env["gpu_total_memory_gb"] = round(props.total_memory / (1024 ** 3), 2)
            env["gpu_compute_capability"] = f"{props.major}.{props.minor}"
    except Exception as e:  # pragma: no cover
        env["torch_error"] = str(e)
    try:
        import onnxruntime as ort
        env["onnxruntime_providers"] = ort.get_available_providers()
    except Exception as e:  # pragma: no cover
        env["onnxruntime_error"] = str(e)
    try:
        from docling.utils.accelerator_utils import decide_device
        env["accelerator_device_resolved"] = str(decide_device(accelerator_device))
    except Exception as e:  # pragma: no cover
        env["accelerator_device_resolved"] = f"unknown ({e})"
    try:
        import psutil
        vm = psutil.virtual_memory()
        env["ram_total_gb"] = round(vm.total / (1024 ** 3), 2)
        env["ram_available_gb_at_start"] = round(vm.available / (1024 ** 3), 2)
    except Exception:
        pass
    return env


def _rss_mb() -> float | None:
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / (1024 ** 2), 1)
    except Exception:
        return None


def _gpu_mem_mb() -> float | None:
    try:
        import torch
        if torch.cuda.is_available():
            return round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# Window result container
# --------------------------------------------------------------------------- #

@dataclass
class WindowResult:
    """Parsed slice for one page window."""
    window_index: int
    page_start: int
    page_end: int
    pages: list[ParsedPage] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: str = ""
    duration_seconds: float = 0.0
    element_count: int = 0
    rss_mb: float | None = None
    gpu_peak_mb: float | None = None
    images_exported: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "window_index": self.window_index,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "element_count": self.element_count,
            "rss_mb": self.rss_mb,
            "gpu_peak_mb": self.gpu_peak_mb,
            "images_exported": self.images_exported,
            "warnings": self.warnings,
            "errors": self.errors,
            "pages": [p.model_dump(mode="json") for p in self.pages],
            "tables": [t.model_dump(mode="json") for t in self.tables],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "WindowResult":
        wr = cls(
            window_index=data["window_index"],
            page_start=data["page_start"],
            page_end=data["page_end"],
            status=data.get("status", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            element_count=data.get("element_count", 0),
            rss_mb=data.get("rss_mb"),
            gpu_peak_mb=data.get("gpu_peak_mb"),
            images_exported=data.get("images_exported", 0),
            warnings=data.get("warnings", []),
            errors=data.get("errors", []),
        )
        wr.pages = [ParsedPage.model_validate(p) for p in data.get("pages", [])]
        wr.tables = [ParsedTable.model_validate(t) for t in data.get("tables", [])]
        return wr


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

class DocumentParser:
    """Parses PDF documents using Docling with memory-safe page windowing."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._converter = None
        self._docling_version = ""
        self._environment: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Converter setup
    # ------------------------------------------------------------------ #
    def _init_converter(self) -> None:
        """Lazily initialize the Docling converter (models load once)."""
        if self._converter is not None:
            return

        try:
            import docling
            from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                f"Docling is not installed. Run: pip install docling\nError: {e}"
            ) from e

        ps = self.config.parser
        self._docling_version = getattr(docling, "__version__", "unknown")

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = ps.ocr_enabled
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.mode = (
            TableFormerMode.ACCURATE if ps.table_structure_mode == "accurate" else TableFormerMode.FAST
        )
        pipeline_options.table_structure_options.do_cell_matching = ps.table_cell_matching
        pipeline_options.generate_picture_images = ps.generate_picture_images
        pipeline_options.generate_page_images = False
        pipeline_options.images_scale = ps.images_scale
        pipeline_options.do_formula_enrichment = ps.formula_enrichment
        pipeline_options.do_code_enrichment = False

        device_map = {
            "auto": AcceleratorDevice.AUTO,
            "cuda": AcceleratorDevice.CUDA,
            "cpu": AcceleratorDevice.CPU,
        }
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=ps.num_threads,
            device=device_map.get(ps.device.lower(), AcceleratorDevice.AUTO),
        )

        if ps.ocr_enabled:
            ocr_engine = ps.ocr_engine.lower()
            if ocr_engine == "rapidocr":
                from docling.datamodel.pipeline_options import RapidOcrOptions
                pipeline_options.ocr_options = RapidOcrOptions(
                    lang=list(ps.ocr_languages),
                    force_full_page_ocr=ps.ocr_force_full_page,
                )
            elif ocr_engine == "easyocr":
                from docling.datamodel.pipeline_options import EasyOcrOptions
                pipeline_options.ocr_options = EasyOcrOptions(
                    lang=["en"], use_gpu=False, force_full_page_ocr=ps.ocr_force_full_page,
                )
            elif ocr_engine == "tesseract":
                from docling.datamodel.pipeline_options import TesseractOcrOptions
                pipeline_options.ocr_options = TesseractOcrOptions(
                    force_full_page_ocr=ps.ocr_force_full_page,
                )
            else:
                logger.warning(f"Unknown OCR engine '{ps.ocr_engine}', using Docling default")

        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
        self._environment = _runtime_environment(ps.device)
        self._environment["ocr_engine"] = ps.ocr_engine if ps.ocr_enabled else None
        self._environment["ocr_backend"] = (
            "onnxruntime(" + ",".join(self._environment.get("onnxruntime_providers", [])) + ")"
            if ps.ocr_enabled and ps.ocr_engine.lower() == "rapidocr" else None
        )
        self._environment["table_structure_mode"] = ps.table_structure_mode
        self._environment["table_cell_matching"] = ps.table_cell_matching
        self._environment["formula_enrichment"] = ps.formula_enrichment
        self._environment["page_window_size"] = ps.page_window_size
        logger.info(
            f"Docling {self._docling_version} converter ready | device="
            f"{self._environment.get('accelerator_device_resolved')} | "
            f"cuda={self._environment.get('cuda_available')} | "
            f"ocr={self._environment.get('ocr_engine')} via {self._environment.get('ocr_backend')}"
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def output_dir(self, doc_id: str) -> Path:
        return self.config.paths.parsed_dir / doc_id

    @staticmethod
    def count_pdf_pages(pdf_path: Path) -> int:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return len(pdf)
        finally:
            pdf.close()

    def parse(self, pdf_path: Path) -> ParsedDocument:
        """Parse a PDF document with memory-safe page windowing."""
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        doc_id = pdf_path.stem
        source_hash = _compute_file_hash(pdf_path)
        start_time = time.time()
        ps = self.config.parser

        out_dir = self.output_dir(doc_id)
        windows_dir = out_dir / "windows"
        images_dir = out_dir / "images"
        for d in (out_dir, windows_dir, images_dir):
            d.mkdir(parents=True, exist_ok=True)

        config_hash = hashlib.md5(
            json.dumps(ps.model_dump(), sort_keys=True, default=str).encode()
        ).hexdigest()[:12]

        total_pdf_pages = self.count_pdf_pages(pdf_path)
        last_page = total_pdf_pages if ps.max_pages is None else min(total_pdf_pages, ps.max_pages)
        window_size = max(1, ps.page_window_size)
        windows = [
            (idx, start, min(start + window_size - 1, last_page))
            for idx, start in enumerate(range(1, last_page + 1, window_size))
        ]
        logger.info(
            f"Parsing {pdf_path.name}: {total_pdf_pages} pages, "
            f"{len(windows)} windows of {window_size} pages (hash {source_hash[:12]})"
        )

        # ---- checkpoint -------------------------------------------------
        checkpoint_path = self.config.paths.checkpoints_dir / f"{doc_id}_parse.json"
        completed: set[int] = set()
        if checkpoint_path.exists():
            try:
                ck = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if ck.get("source_hash") == source_hash and ck.get("config_hash") == config_hash \
                        and ck.get("window_size") == window_size:
                    completed = set(int(i) for i in ck.get("completed_windows", []))
                    logger.info(f"Resuming: {len(completed)}/{len(windows)} windows already parsed")
                else:
                    logger.info("Checkpoint exists but source/config changed; starting fresh")
            except Exception as e:
                logger.warning(f"Could not load parse checkpoint: {e}. Starting fresh.")

        def save_checkpoint(done: set[int], finished: bool = False) -> None:
            checkpoint_path.write_text(json.dumps({
                "source_hash": source_hash,
                "config_hash": config_hash,
                "window_size": window_size,
                "total_windows": len(windows),
                "completed_windows": sorted(done),
                "completed": finished,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, indent=2), encoding="utf-8")

        # ---- run windows ------------------------------------------------
        results: dict[int, WindowResult] = {}
        for idx, w_start, w_end in windows:
            slice_path = windows_dir / f"window_{idx:03d}.parsed.json"
            if idx in completed and slice_path.exists():
                try:
                    results[idx] = WindowResult.from_json(json.loads(slice_path.read_text(encoding="utf-8")))
                    continue
                except Exception as e:
                    logger.warning(f"Window {idx} slice unreadable ({e}); re-parsing")
                    completed.discard(idx)

            self._init_converter()
            wr = self._parse_window(pdf_path, doc_id, idx, w_start, w_end, windows_dir, images_dir)
            slice_path.write_text(json.dumps(wr.to_json(), ensure_ascii=False), encoding="utf-8")
            results[idx] = wr
            completed.add(idx)
            save_checkpoint(completed)

            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

        # ---- merge ------------------------------------------------------
        parsed_doc = self._merge(
            doc_id, pdf_path, source_hash, total_pdf_pages, results, windows,
            config_hash, time.time() - start_time,
        )

        # ---- persist ----------------------------------------------------
        (out_dir / "parsed_document.json").write_text(
            parsed_doc.model_dump_json(indent=1), encoding="utf-8",
        )
        (out_dir / "tables.json").write_text(
            json.dumps({
                "document_id": doc_id,
                "total_tables": len(parsed_doc.tables),
                "tables": [t.model_dump(mode="json") for t in parsed_doc.tables],
            }, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        (out_dir / "metadata.json").write_text(
            json.dumps(parsed_doc.metadata | {
                "document_id": doc_id,
                "source_filename": pdf_path.name,
                "source_hash": source_hash,
                "total_pages": total_pdf_pages,
                "parse_timestamp": parsed_doc.parse_timestamp,
                "parse_duration_seconds": parsed_doc.parse_duration_seconds,
                "parser_config": parsed_doc.parser_config.model_dump(),
                "parser_settings": ps.model_dump(mode="json"),
            }, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        save_checkpoint(completed, finished=True)

        logger.info(
            f"Parsed {pdf_path.name}: {len(parsed_doc.pages)} pages with content / "
            f"{total_pdf_pages} pdf pages, {len(parsed_doc.tables)} tables, "
            f"{sum(len(p.elements) for p in parsed_doc.pages)} elements "
            f"in {parsed_doc.parse_duration_seconds:.1f}s"
        )
        return parsed_doc

    # ------------------------------------------------------------------ #
    # Window conversion
    # ------------------------------------------------------------------ #
    def _parse_window(
        self,
        pdf_path: Path,
        doc_id: str,
        idx: int,
        w_start: int,
        w_end: int,
        windows_dir: Path,
        images_dir: Path,
    ) -> WindowResult:
        from docling.datamodel.base_models import ConversionStatus

        wr = WindowResult(window_index=idx, page_start=w_start, page_end=w_end)
        t0 = time.time()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass

        logger.info(f"Window {idx}: converting pages {w_start}-{w_end} ...")
        result = self._converter.convert(
            str(pdf_path), page_range=(w_start, w_end), raises_on_error=False,
        )
        wr.status = str(getattr(result.status, "value", result.status))
        for err in getattr(result, "errors", []) or []:
            msg = f"[window {idx} p{w_start}-{w_end}] {getattr(err, 'component_type', '')} " \
                  f"{getattr(err, 'module_name', '')}: {getattr(err, 'error_message', err)}"
            wr.errors.append(msg)
        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            wr.warnings.append(f"Window {idx} conversion status {wr.status}")
            logger.error(f"Window {idx} failed: status={wr.status} errors={wr.errors}")
            wr.duration_seconds = time.time() - t0
            return wr

        doc = result.document

        # Raw Docling representation (complete)
        if self.config.parser.save_raw_docling_json:
            try:
                raw = doc.export_to_dict()
                (windows_dir / f"window_{idx:03d}.docling.json").write_text(
                    json.dumps(raw, ensure_ascii=False), encoding="utf-8",
                )
                del raw
            except Exception as e:
                wr.warnings.append(f"Window {idx}: could not export raw docling json: {e}")

        try:
            self._extract_window(doc, doc_id, idx, w_start, w_end, images_dir, wr)
        except Exception as e:
            logger.exception(f"Window {idx}: extraction error")
            wr.errors.append(f"Window {idx} extraction error: {e}")

        wr.duration_seconds = time.time() - t0
        wr.rss_mb = _rss_mb()
        wr.gpu_peak_mb = _gpu_mem_mb()
        logger.info(
            f"Window {idx}: pages {w_start}-{w_end} done in {wr.duration_seconds:.1f}s | "
            f"{wr.element_count} elements, {len(wr.tables)} tables, {wr.images_exported} images | "
            f"rss={wr.rss_mb}MB gpu_peak={wr.gpu_peak_mb}MB status={wr.status}"
        )

        del doc, result
        return wr

    def _extract_window(
        self,
        doc: Any,
        doc_id: str,
        idx: int,
        w_start: int,
        w_end: int,
        images_dir: Path,
        wr: WindowResult,
    ) -> None:
        """Walk the Docling document in reading order and build our schema."""
        from docling_core.types.doc import (
            DocItemLabel,
            GroupItem,
            PictureItem,
            TableItem,
            TextItem,
        )
        try:
            from docling_core.types.doc import ContentLayer
        except ImportError:  # pragma: no cover
            from docling_core.types.doc.document import ContentLayer  # type: ignore

        page_elements: dict[int, list[ParsedElement]] = {}
        table_index_by_page: dict[int, int] = {}
        page_sizes: dict[int, tuple[float, float]] = {}
        for pno, page in (getattr(doc, "pages", None) or {}).items():
            try:
                page_sizes[int(pno)] = (float(page.size.width), float(page.size.height))
            except Exception:
                continue

        # Any page with size info gets a page record, even if empty (so empty
        # pages are visible to the validation step).
        for pno in page_sizes:
            page_elements.setdefault(pno, [])

        group_lookup: dict[str, Any] = {}
        for g in getattr(doc, "groups", []) or []:
            group_lookup[g.self_ref] = g

        layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
        local_order = 0

        for item, depth in doc.iterate_items(with_groups=True, included_content_layers=layers):
            if isinstance(item, GroupItem):
                continue

            provs = _provs_from_docling(item)
            if provs:
                page_num = provs[0].page_no
            else:
                # Items without provenance are rare; attach to the window's first page
                page_num = w_start
                wr.warnings.append(f"Window {idx}: item {getattr(item, 'self_ref', '?')} has no provenance")

            if page_num < w_start or page_num > w_end:
                wr.warnings.append(
                    f"Window {idx}: item {getattr(item, 'self_ref', '?')} reports page {page_num} outside "
                    f"{w_start}-{w_end}"
                )

            label_raw = getattr(item, "label", "")
            label = str(getattr(label_raw, "value", label_raw))
            content_layer = str(getattr(getattr(item, "content_layer", None), "value", "body"))
            parent_ref = None
            try:
                parent_ref = item.parent.cref if item.parent is not None else None
            except Exception:
                pass

            elements = page_elements.setdefault(page_num, [])
            position = len(elements)
            bbox = provs[0].bbox if provs else None

            # ---------------- tables ----------------
            if isinstance(item, TableItem):
                table_idx = table_index_by_page.get(page_num, 0)
                table_index_by_page[page_num] = table_idx + 1
                table_id = _make_table_id(doc_id, page_num, table_idx)

                data = item.data
                cells: list[ParsedTableCell] = []
                for c in getattr(data, "table_cells", []) or []:
                    try:
                        cells.append(ParsedTableCell(
                            row=int(c.start_row_offset_idx),
                            col=int(c.start_col_offset_idx),
                            rowspan=int(getattr(c, "row_span", 1) or 1),
                            colspan=int(getattr(c, "col_span", 1) or 1),
                            content=(c.text or ""),
                            is_header=bool(getattr(c, "column_header", False)),
                            is_row_header=bool(getattr(c, "row_header", False)),
                            is_row_section=bool(getattr(c, "row_section", False)),
                            bbox=_bbox_from_docling(getattr(c, "bbox", None)),
                        ))
                    except Exception as e:
                        wr.warnings.append(f"Window {idx}: bad cell in {table_id}: {e}")

                num_rows = int(getattr(data, "num_rows", 0) or 0) or (max((c.row + c.rowspan for c in cells), default=0))
                num_cols = int(getattr(data, "num_cols", 0) or 0) or (max((c.col + c.colspan for c in cells), default=0))
                grid = _dense_grid(cells, num_rows, num_cols)

                caption = None
                try:
                    caption = item.caption_text(doc) or None
                except Exception:
                    pass
                raw_md = None
                try:
                    raw_md = item.export_to_markdown(doc=doc)
                except Exception:
                    try:
                        raw_md = item.export_to_markdown()
                    except Exception:
                        raw_md = None

                table = ParsedTable(
                    table_id=table_id,
                    page=page_num,
                    num_rows=num_rows,
                    num_cols=num_cols,
                    cells=cells,
                    grid=grid,
                    caption=caption,
                    raw_markdown=raw_md,
                    bbox=bbox,
                    provenance=provs,
                    docling_ref=getattr(item, "self_ref", None),
                    reading_order=local_order,
                )
                wr.tables.append(table)

                content = _grid_to_text(grid) if grid else (raw_md or "")
                if not content.strip():
                    content = f"[Table {table_id}: {len(cells)} cells]"
                    wr.warnings.append(f"Window {idx}: table {table_id} has no textual cell content")

                elements.append(ParsedElement(
                    element_id=_make_element_id(doc_id, page_num, position),
                    element_type=ElementType.TABLE,
                    docling_label=label,
                    content=content,
                    page=page_num,
                    position_in_page=position,
                    reading_order=local_order,
                    table=table,
                    bounding_box=bbox,
                    provenance=provs,
                    content_layer=content_layer,
                    docling_ref=getattr(item, "self_ref", None),
                    parent_ref=parent_ref,
                    tree_depth=depth,
                    caption=caption,
                ))
                local_order += 1
                continue

            # ---------------- pictures ----------------
            if isinstance(item, PictureItem):
                caption = None
                try:
                    caption = item.caption_text(doc) or None
                except Exception:
                    pass
                image_rel = None
                if self.config.parser.generate_picture_images:
                    try:
                        pil = item.get_image(doc)
                        if pil is not None:
                            fname = f"p{page_num:04d}_fig{position:03d}.png"
                            pil.save(images_dir / fname)
                            image_rel = f"images/{fname}"
                            wr.images_exported += 1
                    except Exception as e:
                        wr.warnings.append(f"Window {idx}: image export failed on p{page_num}: {e}")
                elements.append(ParsedElement(
                    element_id=_make_element_id(doc_id, page_num, position),
                    element_type=ElementType.FIGURE,
                    docling_label=label,
                    content=caption or f"[Figure on page {page_num}]",
                    page=page_num,
                    position_in_page=position,
                    reading_order=local_order,
                    bounding_box=bbox,
                    provenance=provs,
                    content_layer=content_layer,
                    docling_ref=getattr(item, "self_ref", None),
                    parent_ref=parent_ref,
                    tree_depth=depth,
                    image_path=image_rel,
                    caption=caption,
                ))
                local_order += 1
                continue

            # ---------------- text-like items ----------------
            text = getattr(item, "text", None)
            if text is None:
                text = getattr(item, "orig", "") or ""
            element_type = _map_docling_label(label)
            if element_type == ElementType.UNKNOWN and isinstance(item, TextItem):
                element_type = ElementType.TEXT

            heading_level = _extract_heading_level(item) if element_type == ElementType.HEADING else None

            list_group_ref = None
            list_enumerated = None
            list_marker = None
            if element_type == ElementType.LIST_ITEM or label == DocItemLabel.LIST_ITEM.value:
                element_type = ElementType.LIST_ITEM
                list_group_ref = parent_ref
                list_enumerated = getattr(item, "enumerated", None)
                list_marker = getattr(item, "marker", None)
                grp = group_lookup.get(parent_ref or "")
                if grp is not None and list_enumerated is None:
                    list_enumerated = str(getattr(grp.label, "value", grp.label)) == "ordered_list"

            if not (text or "").strip() and element_type not in (ElementType.EQUATION,):
                # Keep empty items out of the page stream, but count them
                wr.warnings.append(f"Window {idx}: empty {label} item on page {page_num} skipped")
                continue

            elements.append(ParsedElement(
                element_id=_make_element_id(doc_id, page_num, position),
                element_type=element_type,
                docling_label=label,
                content=text or "",
                page=page_num,
                position_in_page=position,
                reading_order=local_order,
                heading_level=heading_level,
                bounding_box=bbox,
                provenance=provs,
                content_layer=content_layer,
                docling_ref=getattr(item, "self_ref", None),
                parent_ref=parent_ref,
                tree_depth=depth,
                list_group_ref=list_group_ref,
                list_enumerated=list_enumerated,
                list_marker=list_marker,
            ))
            local_order += 1

        for page_num in sorted(page_elements):
            w, h = page_sizes.get(page_num, (None, None))
            wr.pages.append(ParsedPage(
                page_number=page_num, width=w, height=h, elements=page_elements[page_num],
            ))
        wr.element_count = sum(len(p.elements) for p in wr.pages)

    # ------------------------------------------------------------------ #
    # Merge windows
    # ------------------------------------------------------------------ #
    def _merge(
        self,
        doc_id: str,
        pdf_path: Path,
        source_hash: str,
        total_pdf_pages: int,
        results: dict[int, WindowResult],
        windows: list[tuple[int, int, int]],
        config_hash: str,
        duration: float,
    ) -> ParsedDocument:
        ps = self.config.parser
        all_pages: list[ParsedPage] = []
        all_tables: list[ParsedTable] = []
        warnings: list[str] = []
        errors: list[str] = []
        window_meta: list[dict[str, Any]] = []
        global_order = 0

        for idx, _, _ in windows:
            wr = results.get(idx)
            if wr is None:
                errors.append(f"Window {idx} missing from results")
                continue
            # Renumber reading order globally, in window order (windows are page-ordered)
            order_map: dict[int, int] = {}
            for page in sorted(wr.pages, key=lambda p: p.page_number):
                for el in sorted(page.elements, key=lambda e: e.reading_order):
                    order_map.setdefault(el.reading_order, None)
            for local in sorted(order_map):
                order_map[local] = global_order
                global_order += 1
            for page in wr.pages:
                for el in page.elements:
                    el.reading_order = order_map.get(el.reading_order, el.reading_order)
                    if el.table is not None and el.table.reading_order is not None:
                        el.table.reading_order = el.reading_order
            for t in wr.tables:
                if t.reading_order is not None:
                    t.reading_order = order_map.get(t.reading_order, t.reading_order)
            all_pages.extend(sorted(wr.pages, key=lambda p: p.page_number))
            all_tables.extend(wr.tables)
            warnings.extend(wr.warnings)
            errors.extend(wr.errors)
            window_meta.append({
                "window_index": wr.window_index,
                "page_start": wr.page_start,
                "page_end": wr.page_end,
                "status": wr.status,
                "duration_seconds": round(wr.duration_seconds, 2),
                "elements": wr.element_count,
                "tables": len(wr.tables),
                "images": wr.images_exported,
                "rss_mb": wr.rss_mb,
                "gpu_peak_mb": wr.gpu_peak_mb,
                "warnings": len(wr.warnings),
                "errors": len(wr.errors),
            })

        all_pages.sort(key=lambda p: p.page_number)

        # Populate parent_heading on elements (last heading seen in reading order)
        current_heading: str | None = None
        for page in all_pages:
            for el in page.elements:
                if el.element_type == ElementType.HEADING:
                    current_heading = el.content.strip()
                else:
                    el.parent_heading = current_heading

        label_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        for page in all_pages:
            for el in page.elements:
                label_counts[el.docling_label] = label_counts.get(el.docling_label, 0) + 1
                type_counts[el.element_type.value] = type_counts.get(el.element_type.value, 0) + 1

        metadata: dict[str, Any] = {
            "environment": self._environment,
            "windows": window_meta,
            "docling_label_counts": label_counts,
            "element_type_counts": type_counts,
            "pdf_page_count": total_pdf_pages,
            "pages_requested": windows[-1][2] if windows else 0,
            "pages_with_records": len(all_pages),
            "output_dir": str(self.output_dir(doc_id)),
        }

        return ParsedDocument(
            document_id=doc_id,
            source_filename=pdf_path.name,
            source_path=str(pdf_path),
            source_hash=source_hash,
            total_pages=total_pdf_pages,
            pages=all_pages,
            tables=all_tables,
            parser_config=ParserConfig(
                parser_name="docling",
                parser_version=self._docling_version or str(self._environment.get("pkg:docling", "")),
                ocr_enabled=ps.ocr_enabled,
                ocr_engine=ps.ocr_engine,
                table_structure_mode=ps.table_structure_mode,
                page_window_size=ps.page_window_size,
                config_hash=config_hash,
            ),
            parse_timestamp=datetime.now(timezone.utc).isoformat(),
            parse_duration_seconds=duration,
            warnings=warnings,
            errors=errors,
            metadata=metadata,
        )


def load_parsed_document(config: PipelineConfig, doc_id: str) -> ParsedDocument | None:
    """Load a previously parsed document from data/parsed/<doc_id>/parsed_document.json."""
    path = config.paths.parsed_dir / doc_id / "parsed_document.json"
    if not path.exists():
        # Legacy flat layout
        legacy = config.paths.parsed_dir / f"{doc_id}_parsed.json"
        if legacy.exists():
            path = legacy
        else:
            return None
    return ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))

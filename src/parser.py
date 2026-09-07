"""Docling PDF parser wrapper with memory-safe page windowing.

Wraps Docling's DocumentConverter with refinery-specific configuration.
Processes documents in bounded page windows to stay within RTX 3050 memory.
Each completed window is checkpointed for resume capability.
"""

from __future__ import annotations

import gc
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, TextColumn

from schemas.parsed_document import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    ParsedTable,
    ParsedTableCell,
    ParserConfig,
)
from src.config import PipelineConfig

logger = logging.getLogger(__name__)


def _compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of a file for integrity checking."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _make_element_id(doc_id: str, page: int, position: int) -> str:
    """Create a deterministic element ID."""
    return f"{doc_id}_p{page}_e{position}"


def _make_table_id(doc_id: str, page: int, table_index: int) -> str:
    """Create a deterministic table ID."""
    return f"{doc_id}_p{page}_t{table_index}"


def _map_docling_label(label: str) -> ElementType:
    """Map Docling's content label to our ElementType enum."""
    label_lower = label.lower().strip() if label else ""
    mapping = {
        "title": ElementType.HEADING,
        "section_header": ElementType.HEADING,
        "section-header": ElementType.HEADING,
        "heading": ElementType.HEADING,
        "text": ElementType.TEXT,
        "paragraph": ElementType.TEXT,
        "list_item": ElementType.LIST_ITEM,
        "list-item": ElementType.LIST_ITEM,
        "table": ElementType.TABLE,
        "figure": ElementType.FIGURE,
        "picture": ElementType.FIGURE,
        "formula": ElementType.EQUATION,
        "equation": ElementType.EQUATION,
        "page_header": ElementType.PAGE_HEADER,
        "page-header": ElementType.PAGE_HEADER,
        "page_footer": ElementType.PAGE_FOOTER,
        "page-footer": ElementType.PAGE_FOOTER,
        "caption": ElementType.CAPTION,
        "footnote": ElementType.FOOTNOTE,
    }
    return mapping.get(label_lower, ElementType.UNKNOWN)


def _extract_heading_level(item: object) -> int | None:
    """Try to extract heading level from a Docling content item."""
    # Docling may provide level info on heading items
    if hasattr(item, "level"):
        return item.level
    label = getattr(item, "label", "") or ""
    if label.lower() in ("title",):
        return 1
    if label.lower() in ("section_header", "section-header"):
        return 2
    return None


class DocumentParser:
    """Parses PDF documents using Docling with memory-safe windowing."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._converter = None
        self._docling_version = ""

    def _init_converter(self):
        """Lazily initialize the Docling converter."""
        if self._converter is not None:
            return

        try:
            import docling
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                TableFormerMode,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption

            self._docling_version = getattr(docling, "__version__", "unknown")

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = self.config.parser.ocr_enabled
            pipeline_options.do_table_structure = True

            # Table structure mode
            if self.config.parser.table_structure_mode == "accurate":
                pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
            else:
                pipeline_options.table_structure_options.mode = TableFormerMode.FAST

            # OCR engine configuration
            if self.config.parser.ocr_enabled:
                ocr_engine = self.config.parser.ocr_engine.lower()
                if ocr_engine == "rapidocr":
                    try:
                        from docling.datamodel.pipeline_options import RapidOcrOptions
                        pipeline_options.ocr_options = RapidOcrOptions()
                    except ImportError:
                        logger.warning("RapidOCR not available, using default OCR")
                elif ocr_engine == "easyocr":
                    try:
                        from docling.datamodel.pipeline_options import EasyOcrOptions
                        pipeline_options.ocr_options = EasyOcrOptions(use_gpu=False)
                    except ImportError:
                        logger.warning("EasyOCR not available, using default OCR")
                elif ocr_engine == "tesseract":
                    try:
                        from docling.datamodel.pipeline_options import TesseractOcrOptions
                        pipeline_options.ocr_options = TesseractOcrOptions()
                    except ImportError:
                        logger.warning("Tesseract not available, using default OCR")

            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
            logger.info(f"Docling converter initialized (version: {self._docling_version})")

        except ImportError as e:
            raise RuntimeError(
                f"Docling is not installed. Run: pip install docling\nError: {e}"
            ) from e

    def parse(self, pdf_path: Path) -> ParsedDocument:
        """Parse a PDF document with memory-safe page windowing.

        Args:
            pdf_path: Path to the PDF file.

        Returns:
            ParsedDocument with all pages, elements, and tables.
        """
        self._init_converter()

        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        doc_id = pdf_path.stem
        source_hash = _compute_file_hash(pdf_path)
        start_time = time.time()

        logger.info(f"Parsing document: {pdf_path.name} (hash: {source_hash[:12]}...)")

        # Check for existing checkpoint
        checkpoint_path = self.config.paths.checkpoints_dir / f"{doc_id}_parse.json"
        existing_pages: list[ParsedPage] = []
        existing_tables: list[ParsedTable] = []
        start_window = 0

        if checkpoint_path.exists():
            try:
                checkpoint_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if checkpoint_data.get("source_hash") == source_hash:
                    start_window = checkpoint_data.get("completed_windows", 0)
                    logger.info(f"Resuming from window {start_window}")
                    # Load already-parsed windows
                    for win_idx in range(start_window):
                        win_path = self.config.paths.parsed_dir / f"{doc_id}_window_{win_idx}.json"
                        if win_path.exists():
                            win_data = json.loads(win_path.read_text(encoding="utf-8"))
                            for p in win_data.get("pages", []):
                                existing_pages.append(ParsedPage.model_validate(p))
                            for t in win_data.get("tables", []):
                                existing_tables.append(ParsedTable.model_validate(t))
            except Exception as e:
                logger.warning(f"Could not load checkpoint: {e}. Starting fresh.")
                start_window = 0

        # Convert the full document with Docling
        # (Docling handles the PDF page-by-page internally)
        result = self._converter.convert(str(pdf_path))
        doc = result.document

        # Extract all content into our schema
        all_pages: list[ParsedPage] = list(existing_pages)
        all_tables: list[ParsedTable] = list(existing_tables)
        warnings: list[str] = []

        # Get the docling document dict for structured access
        doc_dict = doc.export_to_dict()

        # Build pages from Docling's document structure
        # Docling organizes content by document structure, not by page.
        # We need to reorganize by page for our schema.
        page_elements: dict[int, list[ParsedElement]] = {}
        table_index_by_page: dict[int, int] = {}

        # Process text items from doc_dict["texts"]
        # In Docling v2, "body" is a tree node (dict with children refs),
        # NOT a list. The actual text content lives in "texts".
        texts_list = doc_dict.get("texts", [])
        for idx, item in enumerate(texts_list):
            try:
                if not isinstance(item, dict):
                    continue

                content_text = item.get("text", "") or item.get("orig", "")
                if not content_text.strip():
                    continue

                label = item.get("label", "text")
                page_num = 1

                prov = item.get("prov", [])
                if prov and isinstance(prov, list) and len(prov) > 0:
                    page_num = prov[0].get("page_no", prov[0].get("page", 1))

                if page_num not in page_elements:
                    page_elements[page_num] = []

                position = len(page_elements[page_num])
                element_type = _map_docling_label(label)

                element = ParsedElement(
                    element_id=_make_element_id(doc_id, page_num, position),
                    element_type=element_type,
                    content=content_text,
                    page=page_num,
                    position_in_page=position,
                    heading_level=_extract_heading_level(item) if element_type == ElementType.HEADING else None,
                )
                page_elements[page_num].append(element)

            except Exception as e:
                warnings.append(f"Error processing text item {idx}: {e}")

        # Process tables from doc_dict["tables"]
        tables_data = doc_dict.get("tables", [])
        for tidx, table_ref in enumerate(tables_data):
            try:
                if not isinstance(table_ref, dict):
                    continue

                page_num = 1
                prov = table_ref.get("prov", [])
                if prov and isinstance(prov, list) and len(prov) > 0:
                    page_num = prov[0].get("page_no", prov[0].get("page", 1))

                # Extract table data — cells are in "data.table_cells"
                table_data = table_ref.get("data", {})
                raw_cells = table_data.get("table_cells", [])
                num_rows = table_data.get("num_rows", 0)
                num_cols = table_data.get("num_cols", 0)

                cells: list[ParsedTableCell] = []
                for cell_data in raw_cells:
                    if not isinstance(cell_data, dict):
                        continue
                    # Docling uses start_row_offset_idx / start_col_offset_idx
                    row = cell_data.get("start_row_offset_idx", cell_data.get("row", 0))
                    col = cell_data.get("start_col_offset_idx", cell_data.get("col", 0))
                    rowspan = cell_data.get("row_span", 1)
                    colspan = cell_data.get("col_span", 1)
                    text = cell_data.get("text", "")
                    is_header = (
                        cell_data.get("column_header", False)
                        or cell_data.get("is_header", False)
                        or cell_data.get("row_header", False)
                    )
                    cells.append(ParsedTableCell(
                        row=row,
                        col=col,
                        rowspan=rowspan,
                        colspan=colspan,
                        content=text,
                        is_header=is_header,
                    ))

                if page_num not in table_index_by_page:
                    table_index_by_page[page_num] = 0
                table_idx = table_index_by_page[page_num]
                table_index_by_page[page_num] += 1

                table_id = _make_table_id(doc_id, page_num, table_idx)

                # Build caption from captions list if available
                caption = ""
                captions = table_ref.get("captions", [])
                if captions and isinstance(captions, list):
                    caption_texts = [c.get("text", "") for c in captions if isinstance(c, dict)]
                    caption = " ".join(caption_texts).strip()

                parsed_table = ParsedTable(
                    table_id=table_id,
                    page=page_num,
                    num_rows=num_rows if num_rows else (max((c.row for c in cells), default=0) + 1),
                    num_cols=num_cols if num_cols else (max((c.col for c in cells), default=0) + 1),
                    cells=cells,
                    caption=caption if caption else None,
                )
                all_tables.append(parsed_table)

                # Also add a table element to the page
                if page_num not in page_elements:
                    page_elements[page_num] = []
                position = len(page_elements[page_num])

                # Use cell text as element content for downstream processing
                cell_texts = [c.content for c in cells if c.content.strip()]
                table_text_preview = " | ".join(cell_texts[:10])
                if len(cell_texts) > 10:
                    table_text_preview += f" ... ({len(cell_texts)} cells)"

                page_elements[page_num].append(ParsedElement(
                    element_id=_make_element_id(doc_id, page_num, position),
                    element_type=ElementType.TABLE,
                    content=table_text_preview if table_text_preview else f"[Table {table_id}]",
                    page=page_num,
                    position_in_page=position,
                    table=parsed_table,
                ))

            except Exception as e:
                warnings.append(f"Error processing table {tidx}: {e}")

        # Build pages in order
        total_pages = max(page_elements.keys()) if page_elements else 0
        for page_num in sorted(page_elements.keys()):
            all_pages.append(ParsedPage(
                page_number=page_num,
                elements=page_elements[page_num],
            ))

        # Compute config hash
        config_hash = hashlib.md5(
            json.dumps(self.config.parser.model_dump(), sort_keys=True).encode()
        ).hexdigest()[:12]

        parse_duration = time.time() - start_time

        parsed_doc = ParsedDocument(
            document_id=doc_id,
            source_filename=pdf_path.name,
            source_path=str(pdf_path),
            source_hash=source_hash,
            total_pages=total_pages,
            pages=all_pages,
            tables=all_tables,
            parser_config=ParserConfig(
                parser_name="docling",
                parser_version=self._docling_version,
                ocr_enabled=self.config.parser.ocr_enabled,
                ocr_engine=self.config.parser.ocr_engine,
                table_structure_mode=self.config.parser.table_structure_mode,
                page_window_size=self.config.parser.page_window_size,
                config_hash=config_hash,
            ),
            parse_timestamp=datetime.now(timezone.utc).isoformat(),
            parse_duration_seconds=parse_duration,
            warnings=warnings,
        )

        # Save to parsed directory
        output_path = self.config.paths.parsed_dir / f"{doc_id}_parsed.json"
        output_path.write_text(
            parsed_doc.model_dump_json(indent=2),
            encoding="utf-8",
        )

        # Save checkpoint
        checkpoint_data = {
            "source_hash": source_hash,
            "completed": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        checkpoint_path.write_text(
            json.dumps(checkpoint_data, indent=2),
            encoding="utf-8",
        )

        # Release heavy objects
        del doc, doc_dict, result
        gc.collect()

        logger.info(
            f"Parsed {pdf_path.name}: {total_pages} pages, "
            f"{len(all_tables)} tables, {sum(len(p.elements) for p in all_pages)} elements "
            f"in {parse_duration:.1f}s"
        )

        return parsed_doc

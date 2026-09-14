"""Unit tests for parser helpers, template-table detection, heading inference, table splitting."""

from knowledge_layer.schemas.normalized_document import ContentType, NormalizedElement
from knowledge_layer.schemas.parsed_document import (
    ElementType,
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    ParsedTable,
    ParsedTableCell,
)
from knowledge_layer.config import PipelineConfig
from knowledge_layer.parser import _dense_grid, _grid_to_text, _map_docling_label
from knowledge_layer.normalizer import infer_heading_level
from knowledge_layer.table_classifier import detect_page_template_tables
from knowledge_layer.chunker import DocumentChunker


def _cell(r, c, text, rs=1, cs=1, header=False):
    return ParsedTableCell(row=r, col=c, rowspan=rs, colspan=cs, content=text, is_header=header)


def test_dense_grid_propagates_spans():
    cells = [_cell(0, 0, "H", cs=3, header=True), _cell(1, 0, "a"), _cell(1, 1, "b", rs=2), _cell(1, 2, "c"), _cell(2, 0, "d"), _cell(2, 2, "e")]
    grid = _dense_grid(cells, 3, 3)
    assert grid[0] == ["H", "H", "H"]
    assert grid[1] == ["a", "b", "c"]
    assert grid[2] == ["d", "b", "e"]
    assert "H | H | H" in _grid_to_text(grid)


def test_label_mapping_covers_new_labels():
    assert _map_docling_label("picture") == ElementType.FIGURE
    assert _map_docling_label("formula") == ElementType.EQUATION
    assert _map_docling_label("page_header") == ElementType.PAGE_HEADER
    assert _map_docling_label("document_index") == ElementType.TEXT


def test_infer_heading_level():
    assert infer_heading_level("3.2.1 Overhead System", 2) == 3
    assert infer_heading_level("3.2 Feed", 2) == 2
    assert infer_heading_level("5 INTRODUCTION", 2) == 1
    assert infer_heading_level("CHAPTER 5: PROCESS", 2) == 1
    assert infer_heading_level("Plant Overview", 2) == 2
    assert infer_heading_level("Plant Overview", None) == 2


def _header_box(doc_id, page, chapter, title):
    cells = [
        _cell(0, 0, "OPERATING MANUAL", cs=3, header=True),
        _cell(1, 0, f"Chapter No: {chapter}"), _cell(1, 1, "PLANT NO: PLANT NAME:"), _cell(1, 2, "10, 11 & 12 CDU II"),
        _cell(2, 1, "Page No"), _cell(2, 2, f"Page {page} of 562"),
        _cell(3, 1, "Chapter Rev No:"), _cell(3, 2, "0"),
        _cell(4, 0, title, cs=3),
    ]
    return ParsedTable(table_id=f"{doc_id}_p{page}_t0", page=page, num_rows=5, num_cols=3, cells=cells)


def test_detect_page_template_tables():
    doc_id = "d"
    tables = []
    for page in range(1, 11):
        tables.append(_header_box(doc_id, page, 1 + page // 4, f"CHAPTER TITLE {page // 4}"))
    data_table = ParsedTable(
        table_id="d_p3_t1", page=3, num_rows=3, num_cols=2,
        cells=[_cell(0, 0, "Tag", header=True), _cell(0, 1, "Service", header=True),
               _cell(1, 0, "P-101", ), _cell(1, 1, "Crude charge pump"),
               _cell(2, 0, "E-201"), _cell(2, 1, "Preheat exchanger")],
    )
    tables.append(data_table)
    doc = ParsedDocument(document_id=doc_id, source_filename="x.pdf", source_path="x", source_hash="h",
                         total_pages=10, pages=[ParsedPage(page_number=p) for p in range(1, 11)], tables=tables)
    ids = detect_page_template_tables(doc)
    assert len(ids) == 10
    assert "d_p3_t1" not in ids


def test_chunker_splits_large_tables():
    config = PipelineConfig()
    config.chunker.max_tokens = 60
    config.chunker.target_tokens = 40
    header = "Tag | Service | Design Pressure barg"
    rows = [f"P-{i:03d} | Pump number {i} service | {i * 1.5}" for i in range(40)]
    elem = NormalizedElement(
        element_id="e1", content_type=ContentType.TABLE, content="\n".join([header, *rows]),
        page=5, position_in_page=0, table_id="t1",
    )
    chunker = DocumentChunker(config)
    parts = chunker._split_large_tables([elem])
    assert len(parts) > 1
    assert all(p.content.split("\n")[1] == header for p in parts)
    assert parts[0].content.startswith("[Table t1 part 1/")
    body_rows = sum(len(p.content.split("\n")) - 2 for p in parts)
    assert body_rows == 40

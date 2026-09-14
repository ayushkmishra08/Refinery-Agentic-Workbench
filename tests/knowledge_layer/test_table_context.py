"""Tests for table context derivation (labels above tables, continuation tables)."""

from knowledge_layer.schemas.normalized_document import ContentType, NormalizedDocument, NormalizedElement
from knowledge_layer.schemas.parsed_document import ParsedTable, ParsedTableCell
from knowledge_layer.table_context import build_table_contexts, clean_label, is_label_text


def _el(i, ctype, content, page=1, table_id=None, parent_heading=None):
    return NormalizedElement(
        element_id=f"e{i}", content_type=ctype, content=content, page=page, position_in_page=i,
        section_path="S", parent_heading=parent_heading, table_id=table_id,
    )


def _table(tid, rows, page=1, header_rows=1):
    cells = [ParsedTableCell(row=r, col=c, content=t, is_header=r < header_rows)
             for r, row in enumerate(rows) for c, t in enumerate(row)]
    return ParsedTable(table_id=tid, page=page, num_rows=len(rows), num_cols=len(rows[0]),
                       cells=cells, grid=[list(r) for r in rows])


ASSAY = [["Property", "Basrah", "Bombay high"], ["Flash point 0 C", "45", "48"], ["Sp.Gravity@15 0 C", "0.8104", "0.803"]]


def test_labels_are_assigned_fifo_to_following_tables():
    tables = {
        "t1": _table("t1", ASSAY), "t2": _table("t2", ASSAY),
        "t3": _table("t3", [["Naphthenes, vol %", "19.5", "30.0"], ["Aromatics, vol %", "14.7", "17.2"]]),
        "t4": _table("t4", ASSAY), "t5": _table("t5", ASSAY),
    }
    doc = NormalizedDocument(document_id="d", source_filename="d.pdf", total_pages=2, elements=[
        _el(0, ContentType.HEADING, "4.2.2 Liquefied Petroleum Gas (LPG) (As per IS-4576 standards)"),
        _el(1, ContentType.LIST_ITEM, "Stabilized Naphtha (SRN):"),
        _el(2, ContentType.LIST_ITEM, "Heavy Naphtha (HN) Product:"),
        _el(3, ContentType.TABLE, "Property | Basrah | Bombay high", table_id="t1"),
        _el(4, ContentType.TABLE, "Property | Basrah | Bombay high", table_id="t2"),
        _el(5, ContentType.LIST_ITEM, "Kerosene Product:", page=2),
        _el(6, ContentType.LIST_ITEM, "ATF product:", page=2),
        _el(7, ContentType.PARAGRAPH, "[Figure on page 2]", page=2),
        _el(8, ContentType.TABLE, "Naphthenes, vol % | 19.5 | 30.0", page=2, table_id="t3"),
        _el(9, ContentType.TABLE, "Property | Basrah | Bombay high", page=2, table_id="t4"),
        _el(10, ContentType.TABLE, "Property | Basrah | Bombay high", page=2, table_id="t5"),
    ])
    ctx = build_table_contexts(doc, tables)
    assert ctx["t1"].label == "Stabilized Naphtha (SRN)"
    assert ctx["t2"].label == "Heavy Naphtha (HN) Product"
    # t3 has no header row -> continuation of t2, keeps t2's label
    assert ctx["t3"].continuation_of == "t2" and ctx["t3"].label == "Heavy Naphtha (HN) Product"
    assert ctx["t4"].label == "Kerosene Product"
    assert ctx["t5"].label == "ATF product"


def test_heading_resets_label_queue_and_enumerated_sublabel_gets_parent():
    tables = {"t1": _table("t1", [["Product", "TBP cut Range C", "kg/h"], ["Diesel", "270-380", "66066"]]),
              "t2": _table("t2", [["Product", "TBP cut Range C", "kg/h"], ["Diesel", "230-380", "9041"]])}
    doc = NormalizedDocument(document_id="d", source_filename="d.pdf", total_pages=1, elements=[
        _el(0, ContentType.HEADING, "3.3 Material Balance (design case):"),
        _el(1, ContentType.HEADING, "3.3.1.1 Atmospheric Distillation column: (Basrah Crude)"),
        _el(2, ContentType.HEADING, "i.)  SKO operation:"),
        _el(3, ContentType.TABLE, "Product | ...", table_id="t1"),
        _el(4, ContentType.HEADING, "ii.)  ATF operation:"),
        _el(5, ContentType.TABLE, "Product | ...", table_id="t2"),
    ])
    ctx = build_table_contexts(doc, tables)
    assert ctx["t1"].label == "Atmospheric Distillation column (Basrah Crude) / SKO operation"
    assert ctx["t2"].label == "Atmospheric Distillation column (Basrah Crude) / ATF operation"


def test_list_of_labels_supersedes_section_heading():
    mnm = [["", "Minimum", "Normal", "Maximum", "Mech. Design"], ["Pressure (Kg/cm 2 )", "3.5", "4.0", "5.0", "6.5"]]
    tables = {f"t{i}": _table(f"t{i}", mnm) for i in range(1, 6)}
    doc = NormalizedDocument(document_id="d", source_filename="d.pdf", total_pages=1, elements=[
        _el(0, ContentType.HEADING, "3.5 UTILITIES CONDITIONS AT UNIT BATTERY LIMIT:"),
        _el(1, ContentType.PARAGRAPH, "Utilities And Their Specifications:"),
        _el(2, ContentType.LIST_ITEM, "LP Steam:"),
        _el(3, ContentType.LIST_ITEM, "MP steam:"),
        _el(4, ContentType.LIST_ITEM, "HP steam:"),
        _el(5, ContentType.LIST_ITEM, "Instrument Air:"),
        _el(6, ContentType.LIST_ITEM, "Plant air:"),
        *[_el(7 + i, ContentType.TABLE, "| Minimum | ...", table_id=f"t{i + 1}") for i in range(5)],
    ])
    ctx = build_table_contexts(doc, tables)
    assert [ctx[f"t{i}"].label for i in range(1, 6)] == ["LP Steam", "MP steam", "HP steam", "Instrument Air", "Plant air"]


def test_numbered_paragraph_acts_as_heading_and_footnotes_are_ignored():
    tables = {"t1": _table("t1", [["Products", "kg/h"], ["LPG", "8343"]]),
              "t2": _table("t2", [["Products", "kg/h"], ["LPG", "7610"]])}
    doc = NormalizedDocument(document_id="d", source_filename="d.pdf", total_pages=1, elements=[
        _el(0, ContentType.PARAGRAPH, "3.3.1.2 Naphtha Stabilizer Material Balance for Basrah crude:"),
        _el(1, ContentType.TABLE, "Products | kg/h", table_id="t1"),
        _el(2, ContentType.PARAGRAPH, "** LPG quantity corresponds to 94% recovery based on 2.2% by wt. LPG on crude"),
        _el(3, ContentType.PARAGRAPH, "The performance of the above design has further been checked for the following cases."),
        _el(4, ContentType.PARAGRAPH, "Crude Distillation Column Material Balance for Kuwait crude SKO operation"),
        _el(5, ContentType.TABLE, "Products | kg/h", table_id="t2"),
    ])
    ctx = build_table_contexts(doc, tables)
    assert ctx["t1"].label == "Naphtha Stabilizer Material Balance for Basrah crude"
    assert ctx["t2"].label == "Crude Distillation Column Material Balance for Kuwait crude SKO operation"


def test_label_text_rules():
    assert is_label_text("Diesel (DSL):")
    assert is_label_text("Heavy Naphtha (HN) Product:")
    assert not is_label_text("[Figure on page 40]")
    assert not is_label_text("* LPG for further treatment.")
    assert not is_label_text("The vacuum column is designed with a recycle rate equal to 6 v% of the tower feed.")
    assert clean_label("4.2.2 Liquefied Petroleum Gas (LPG) (As per IS-4576 standards)") == "Liquefied Petroleum Gas (LPG)"
    assert clean_label("i.)  SKO operation:") == "SKO operation"

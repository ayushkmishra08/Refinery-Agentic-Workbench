"""Tests for document structure reconstruction (TOC, running titles, procedures, references)."""

from knowledge_layer.schemas.normalized_document import ContentType, NormalizedElement
from knowledge_layer.schemas.parsed_document import (
    ElementType, ParsedDocument, ParsedElement, ParsedPage, ParsedTable, ParsedTableCell,
)
from knowledge_layer.structure import (
    chapter_page_map,
    clean_toc_title,
    detect_procedures,
    extract_cross_references,
    extract_document_references,
    is_imperative,
    parse_standing_instructions,
    parse_toc,
    running_title_texts,
)


def _table(tid, rows, page=1):
    cells = [ParsedTableCell(row=r, col=c, content=t, is_header=(r == 0))
             for r, row in enumerate(rows) for c, t in enumerate(row)]
    return ParsedTable(table_id=tid, page=page, num_rows=len(rows), num_cols=len(rows[0]), cells=cells,
                       grid=[list(r) for r in rows])


def _el(i, ctype, content, page=1, path="", chapter=None):
    return NormalizedElement(element_id=f"e{i}", content_type=ctype, content=content, page=page,
                             position_in_page=i, section_path=path, chapter_number=chapter)


TOC = [
    ["CHAPTER No:", "TITLE", "FROM PAGE NO", "LATEST REV NO.", "REV DATE"],
    ["1", "Administrative Requirements of the Manual Section A : Foreword Section B", "1", "0", "31-03-2012"],
    ["2", "Introduction", "17", "0", "31-03-2012"],
    ["3", "Basis of Design", "20", "1", "31-03-2012"],
    ["", "b. First aid Procedures c. PPE requirements", "", "", ""],
    ["26", "Plant Chemicals a. Withdrawal management b. Max Storage", "347", "0", "31-03-2012"],
]


def _doc_with_toc():
    pages = [ParsedPage(page_number=p, elements=[]) for p in range(1, 401)]
    for p in (17, 18, 19):
        pages[p - 1].elements.append(ParsedElement(
            element_id=f"h{p}", element_type=ElementType.HEADING, content="INTRODUCTION", page=p, position_in_page=0))
    for p in range(20, 30):
        pages[p - 1].elements.append(ParsedElement(
            element_id=f"h{p}", element_type=ElementType.HEADING, content="BASIS OF DESIGN", page=p, position_in_page=0))
    pages[24].elements.append(ParsedElement(element_id="a", element_type=ElementType.HEADING, content="Actions:", page=25, position_in_page=1))
    pages[25].elements.append(ParsedElement(element_id="b", element_type=ElementType.HEADING, content="Actions:", page=26, position_in_page=1))
    pages[26].elements.append(ParsedElement(element_id="c", element_type=ElementType.HEADING, content="Actions:", page=27, position_in_page=1))
    pages[20].elements.append(ParsedElement(element_id="t", element_type=ElementType.TEXT, content="Chapter No:  3", page=21, position_in_page=0))
    return ParsedDocument(document_id="d", source_filename="d.pdf", source_path="d.pdf", source_hash="x",
                          total_pages=400, pages=pages, tables=[_table("toc", TOC, page=2)])


def test_clean_toc_title_cuts_enumerated_tails_and_wrapped_prefix():
    assert clean_toc_title("Plant Chemicals a. Withdrawal management b. Max Storage") == "Plant Chemicals"
    assert clean_toc_title("Administrative Requirements of the Manual Section A : Foreword") == "Administrative Requirements of the Manual"
    assert clean_toc_title("List of Annexures : a) Unit master blind list") == "List of Annexures"
    assert clean_toc_title("Employed'-(PSM/FR/2.9) Sampling requirement and Sampling Procedures") == "Sampling requirement and Sampling Procedures"


def test_parse_toc_gives_chapters_with_ranges_and_admin_flag():
    doc = _doc_with_toc()
    chapters = parse_toc(doc)
    assert [c.number for c in chapters] == [1, 2, 3, 26]
    assert chapters[0].is_administrative and not chapters[1].is_administrative
    assert (chapters[1].page_start, chapters[1].page_end) == (17, 19)
    assert chapters[2].revision == "1" and chapters[2].revision_date == "31-03-2012"
    assert chapters[-1].page_end == 400


def test_chapter_page_map_prefers_page_header_over_toc():
    doc = _doc_with_toc()
    chapters = parse_toc(doc)
    pm = chapter_page_map(doc, set(), chapters)
    assert pm[17] == 2 and pm[19] == 2 and pm[20] == 3 and pm[21] == 3 and pm[347] == 26
    assert len(pm) == 400


def test_running_titles_match_toc_but_not_short_repeated_labels():
    doc = _doc_with_toc()
    chapters = parse_toc(doc)
    rt = running_title_texts(doc, chapters)
    assert "basis of design" in rt and "introduction" in rt
    assert "actions" not in rt


def test_is_imperative():
    assert is_imperative("Ensure all the utilities like BCW, seal steam and self coolant are open.")
    assert is_imperative("Slowly open the discharge valve.")
    assert is_imperative("1. Check the pump basement and remove any foreign materials present.")
    assert not is_imperative("The pump is a motor and turbine driven centrifugal pump.")
    assert not is_imperative("Preparation of the unit")


def test_detect_procedures_marks_steps_and_reads_label_and_tags():
    els = [
        _el(0, ContentType.HEADING, "16.1 CRUDE CHARGE PUMP 11-PM- 01A/B:", path="Ch 16 > 16.1", chapter=16),
        _el(1, ContentType.PARAGRAPH, "It is a motor and turbine driven centrifugal pump.", path="Ch 16 > 16.1", chapter=16),
        _el(2, ContentType.PARAGRAPH, "Pump Change over Procedure: (motor to turbine)", path="Ch 16 > 16.1", chapter=16),
        _el(3, ContentType.LIST_ITEM, "First take clearance from CPP for consumption of HP steam.", path="Ch 16 > 16.1", chapter=16),
        _el(4, ContentType.LIST_ITEM, "Ensure all the utilities like BCW and seal steam are open.", path="Ch 16 > 16.1", chapter=16),
        _el(5, ContentType.LIST_ITEM, "Check the pump basement and remove any foreign materials.", path="Ch 16 > 16.1", chapter=16),
        _el(6, ContentType.LIST_ITEM, "Switch over seal flushing to 11-PM-02A from motor to turbine.", path="Ch 16 > 16.1", chapter=16),
        _el(7, ContentType.PARAGRAPH, "This is a long explanatory paragraph that is not an instruction and ends the block because it describes history of the plant in detail.", path="Ch 16 > 16.1", chapter=16),
        _el(8, ContentType.LIST_ITEM, "Crude Charge Pump", path="Ch 16", chapter=16),
        _el(9, ContentType.LIST_ITEM, "Crude Booster Pump", path="Ch 16", chapter=16),
        _el(10, ContentType.LIST_ITEM, "Desalter", path="Ch 16", chapter=16),
    ]
    procs = detect_procedures(els, "d")
    assert len(procs) == 1
    p = procs[0]
    assert p.title == "Pump Change over Procedure: (motor to turbine)"
    assert p.procedure_type.value == "changeover"
    assert [s.sequence for s in p.steps] == [1, 2, 3, 4]
    assert els[2].procedure_id == p.procedure_id and els[2].step_number == 0
    assert els[3].content_type == ContentType.PROCEDURE_STEP and els[3].step_number == 1
    assert "11-PM-02A" in p.applies_to
    # the plain equipment list (not imperative, no procedure heading) is not a procedure
    assert els[8].procedure_id is None


def test_cross_references_need_a_pointer_phrase():
    els = [
        _el(0, ContentType.PARAGRAPH, "The startup checklist is given in Chapter 34 and details are in Section 6.1.6.4.",
            page=195, path="Ch 13 > 13.1"),
        _el(1, ContentType.PARAGRAPH, "Chapter 16 describes major equipment.", page=1, path="Ch 1"),
    ]
    refs = extract_cross_references(els)
    targets = {(r.target_kind, r.target_number) for r in refs}
    assert ("chapter", "34") in targets and ("section", "6.1.6.4") in targets
    assert ("chapter", "16") not in targets


def test_document_references_require_identifiers():
    els = [
        _el(0, ContentType.PARAGRAPH, "Refer P&ID 10-1100-E-203 Rev.5 and P&ID No. 10-1100-E-204. The P&ID should follow pipe class."),
        _el(1, ContentType.PARAGRAPH, "LPG (As per IS-4576 standards). Standing instruction ADM/OPRN/PRODN/ SI/008 applies. PFD level is controlled."),
    ]
    refs = extract_document_references(els)
    texts = {r.reference_text for r in refs}
    assert "P&ID 10-1100-E-203 Rev.5" in texts
    assert "P&ID 10-1100-E-204" in texts
    assert any(r.document_type == "Standard" and "IS-4576" in r.document_number for r in refs)
    assert any(r.document_type == "Standing Instruction" and r.document_number == "ADM/OPRN/PRODN/SI/008" for r in refs)
    assert not any("should" in t or "level" in t for t in texts)


def test_parse_standing_instructions_reads_register_and_continuation():
    t1 = _table("si1", [
        ["SL. NO", "STANDING INSTRUCTION NO.", "STANDING INSTRUCTION TITLE", "DATE OF ISSUE", ""],
        ["1.", "ADM/OPRN/PRODN/ SI/003", "Equipment draining", "Aug 2000", "Standing Instructions Incorporated in Operations Manual Chapter-28"],
        ["2.", "ADM/OPRN/PRODN/ SI/008", "CDU-II startup Check-List", "March 2000", "Incorporated in Operations Manual Chapter-34"],
    ], page=15)
    t2 = _table("si2", [
        ["13.", "ADM/OPRN/PRODN/ SI/035", "Avoid congealing of Heavy oil R/D lines", "May 2011", "incorporated in Chapter-.15"],
    ], page=16)
    t3 = _table("si3", [
        ["SL. NO", "STANDING INSTRUCTION NO.", "STANDING INSTRUCTION TITLE", "DATE", "RE V.", "EXPIRED/ INCORPORATED"],
        ["1.", "ADM/OPRN/PROD N/SI/002", "Effluent Monitoring", "Aug 2000", "0", "EXPIRED"],
    ], page=16)
    items = parse_standing_instructions([t1, t2, t3])
    by_no = {i.number: i for i in items}
    assert by_no["ADM/OPRN/PRODN/SI/008"].incorporated_in_chapter == "34"
    assert by_no["ADM/OPRN/PRODN/SI/008"].status == "incorporated"
    assert by_no["ADM/OPRN/PRODN/SI/035"].incorporated_in_chapter == "15"
    assert by_no["ADM/OPRN/PRODN/SI/002"].status == "expired"       # OCR-split number normalised

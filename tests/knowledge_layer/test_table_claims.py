"""Tests for the deterministic table-to-claims mapper."""

from knowledge_layer.schemas.claims import ClaimCategory
from knowledge_layer.schemas.parsed_document import ParsedTable, ParsedTableCell
from knowledge_layer.table_claims import extract_table_claims


def _table(tid, page, rows, header_rows=1):
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append(ParsedTableCell(row=r, col=c, content=text, is_header=r < header_rows))
    return ParsedTable(table_id=tid, page=page, num_rows=len(rows), num_cols=len(rows[0]),
                       cells=cells, grid=[list(r) for r in rows])


def _by(claims):
    return {(c.subject, c.predicate, c.value): c for c in claims}


def test_min_normal_max_table_uses_heading_as_subject():
    t = _table("t1", 32, [
        ["", "Minimum", "Normal", "Maximum", "Mech. Design"],
        ["Pressure (Kg/cm 2 )", "3.5", "4.0", "5.0", "6.5"],
        ["Temperature (°C)", "Saturated", "150", "170", "190"],
    ])
    claims = extract_table_claims(t, "chunk1", "doc", "3.2 Steam", subject_hint="LP Steam Header")
    by = _by(claims)
    assert ("LP Steam Header", ClaimCategory.MINIMUM_PRESSURE, "3.5") in by
    assert ("LP Steam Header", ClaimCategory.NORMAL_PRESSURE, "4.0") in by
    assert ("LP Steam Header", ClaimCategory.MAXIMUM_PRESSURE, "5.0") in by
    assert ("LP Steam Header", ClaimCategory.DESIGN_PRESSURE, "6.5") in by
    assert ("LP Steam Header", ClaimCategory.NORMAL_TEMPERATURE, "150") in by
    assert ("LP Steam Header", ClaimCategory.DESIGN_TEMPERATURE, "190") in by
    # "Saturated" is not numeric -> no minimum temperature claim
    assert not any(p == ClaimCategory.MINIMUM_TEMPERATURE for _, p, _ in by)
    c = by[("LP Steam Header", ClaimCategory.DESIGN_PRESSURE, "6.5")]
    assert c.unit == "kg/cm2" and c.is_from_table and c.table_id == "t1" and c.page == 32 and c.source == "table"
    assert c.evidence.endswith("Pressure (Kg/cm 2 ) | 3.5 | 4.0 | 5.0 | 6.5")
    assert c.evidence.startswith("Minimum | Normal | Maximum | Mech. Design")


def test_tag_no_table_with_two_header_rows():
    t = _table("t2", 345, [
        ["Tag No", "Description", "Operating Temp (C)", "Operating Temp (C)", "Operating Pressure (Kg/cm2g)", "Operating Pressure (Kg/cm2g)"],
        ["Tag No", "Description", "PG", "BH", "PG", "BH"],
        ["11-V-02", "Crude Desalter", "129", "136.5", "14.0", "14.0"],
        ["10-V-01", "Naphtha Caustic Wash", "40", "40", "6.5", "6.5"],
    ], header_rows=2)
    claims = extract_table_claims(t, "chunk2", "doc", "Equipment")
    by = _by(claims)
    assert ("11-V-02", ClaimCategory.OPERATING_TEMPERATURE, "129") in by
    assert ("11-V-02", ClaimCategory.OPERATING_TEMPERATURE, "136.5") in by
    assert ("11-V-02", ClaimCategory.OPERATING_PRESSURE, "14.0") in by
    assert ("10-V-01", ClaimCategory.OPERATING_PRESSURE, "6.5") in by
    c = by[("11-V-02", ClaimCategory.OPERATING_TEMPERATURE, "129")]
    assert c.unit == "°C"
    assert by[("11-V-02", ClaimCategory.OPERATING_PRESSURE, "14.0")].unit == "kg/cm2"
    # Description column is not numeric -> no claim
    assert not any(v == "Crude Desalter" for _, _, v in by)


def test_product_property_table():
    t = _table("t3", 21, [
        ["Product", "TBP cut range °C", "Temperature °C", "Pressure, Kg/cm 2 A"],
        ["LPG", "C3-C4", "40", "8.0"],
        ["RCO", "380+", "343", "14.4"],
    ])
    by = _by(extract_table_claims(t, "c", "doc"))
    assert ("LPG", ClaimCategory.OPERATING_TEMPERATURE, "40") in by
    assert ("LPG", ClaimCategory.OPERATING_PRESSURE, "8.0") in by
    assert ("RCO", ClaimCategory.CUT_RANGE, "380+") in by


def test_design_feed_property_rows_by_crude_columns():
    t = _table("t4", 20, [
        ["Crude", "Basrah", "Bombay high"],
        ["Kg/ hr", "367647", "367647"],
        ["Sp. Gravity @ 15 °C", "0.848", "0.8284"],
        ["Total Sulfur, wt %", "1.9", "0.15"],
        ["H2S content", "Nil", "Nil"],
    ])
    by = _by(extract_table_claims(t, "c", "doc"))
    assert ("Basrah", ClaimCategory.MASS_FLOW_RATE, "367647") in by
    assert ("Bombay high", ClaimCategory.SPECIFIC_GRAVITY, "0.8284") in by
    assert ("Basrah", ClaimCategory.GENERIC_PROPERTY, "1.9") in by
    assert by[("Basrah", ClaimCategory.MASS_FLOW_RATE, "367647")].unit == "kg/h"
    assert not any(v == "Nil" for _, _, v in by)

"""Tests for qualifier-aware table claims: assay tables, continuations, exchangers, key/value tables."""

from schemas.claims import ClaimCategory
from schemas.parsed_document import ParsedTable, ParsedTableCell
from src.table_claims import clean_value, extract_table_claims
from src.table_context import TableContext


def _table(tid, page, rows, header_rows=1):
    cells = [ParsedTableCell(row=r, col=c, content=t, is_header=r < header_rows)
             for r, row in enumerate(rows) for c, t in enumerate(row)]
    return ParsedTable(table_id=tid, page=page, num_rows=len(rows), num_cols=len(rows[0]),
                       cells=cells, grid=[list(r) for r in rows])


def _by(claims):
    return {(c.subject, c.predicate, c.value): c for c in claims}


def test_assay_table_gets_cut_label_and_property_qualifiers():
    t = _table("p42_t2", 42, [
        ["Property", "Basrah", "Bombay high"],
        ["Flash point 0 C", "115", "115"],
        ["Rate tones/annum", "539099", "639000"],
        ["Sp.Gravity@15 0 C", "0.8527", "0.855"],
        ["Sulfur Wt %", "1.6", "0.129"],
        ["Viscosity Cst", "8.7", ""],
        ["Viscosity Cst @37.8 0 C", "5.2", ""],
        ["N 2", "NA", "52 mg/l"],
        ["ASTM D-86(VOL %)", "Temperature 0 C", "Temperature 0 C"],
        ["IBP", "236.9", "241"],
        ["10%", "272", "269"],
    ])
    ctx = TableContext(table_id="p42_t2", label="Diesel (DSL)")
    claims = extract_table_claims(t, "c", "doc", context=ctx)
    by = _by(claims)
    sg = by[("Basrah", ClaimCategory.SPECIFIC_GRAVITY, "0.8527")]
    assert sg.qualifier == "Diesel (DSL) / @ 15 °C"
    assert by[("Basrah", ClaimCategory.FLASH_POINT, "115")].qualifier == "Diesel (DSL)"
    rate = by[("Bombay high", ClaimCategory.CAPACITY, "639000")]
    assert rate.unit == "TPA"                      # "tones/annum" is a rate, not a yield percentage
    sulfur = by[("Basrah", ClaimCategory.GENERIC_PROPERTY, "1.6")]
    assert sulfur.qualifier == "Diesel (DSL) / Sulfur"     # property name kept -> no clash with wax/API
    assert by[("Basrah", ClaimCategory.VISCOSITY, "5.2")].qualifier == "Diesel (DSL) / @ 37.8 °C"
    assert by[("Basrah", ClaimCategory.VISCOSITY, "8.7")].qualifier == "Diesel (DSL)"
    n2 = by[("Bombay high", ClaimCategory.GENERIC_PROPERTY, "52")]
    assert n2.unit == "mg/l"
    # distillation points are claims on the crude, never subjects of their own
    ibp = by[("Bombay high", ClaimCategory.DISTILLATION_TEMPERATURE, "241")]
    assert ibp.qualifier == "Diesel (DSL) / ASTM D-86 / IBP" and ibp.unit == "°C"
    assert by[("Basrah", ClaimCategory.DISTILLATION_TEMPERATURE, "272")].qualifier == "Diesel (DSL) / ASTM D-86 / 10%"
    assert not any(s in ("IBP", "10%", "Temperature 0 C") for s, _, _ in by)
    assert not any(v.startswith("Temperature") for _, _, v in by)


def test_continuation_table_inherits_subject_columns_and_label():
    root = _table("p43_t3", 43, [
        ["Property", "Basrah", "Bombay high"],
        ["Pour point 0 C", "+48", "+30"],
        ["Viscosity Cp", "1.12@246 0 C", "NA"],
        ["", "0.77@314.2 0 C", "NA"],
    ])
    cont = _table("p44_t1", 44, [["70%", "480.0", "467"], ["90%", "511.4", "512"], ["95%", "529.2", "538"]])
    ctx_root = TableContext(table_id="p43_t3", label="FCCU FEED (LVGO + HVGO)")
    by_root = _by(extract_table_claims(root, "c", "doc", context=ctx_root))
    v1 = by_root[("Basrah", ClaimCategory.VISCOSITY, "1.12")]
    assert v1.unit == "cP" and v1.qualifier == "FCCU FEED (LVGO + HVGO) / @ 246 °C"
    # blank row label inherits the previous property ("Viscosity Cp")
    assert by_root[("Basrah", ClaimCategory.VISCOSITY, "0.77")].qualifier == "FCCU FEED (LVGO + HVGO) / @ 314.2 °C"
    assert by_root[("Basrah", ClaimCategory.POUR_POINT, "+48")].value == "+48"

    ctx_cont = TableContext(table_id="p44_t1", label="FCCU FEED (LVGO + HVGO)", continuation_of="p43_t3")
    claims = extract_table_claims(cont, "c2", "doc", context=ctx_cont, inherit_from=root)
    by = _by(claims)
    assert ("Basrah", ClaimCategory.DISTILLATION_TEMPERATURE, "480.0") in by
    assert by[("Bombay high", ClaimCategory.DISTILLATION_TEMPERATURE, "512")].qualifier == "FCCU FEED (LVGO + HVGO) / 90%"
    assert all(c.evidence.startswith("Property | Basrah | Bombay high") for c in claims)
    assert not any(s in ("70%", "90%", "95%") for s, _, _ in by)


def test_orphan_distillation_table_without_context_yields_nothing():
    t = _table("p42_t1", 42, [["IBP", "150", "110"], ["10%", "170", "149"]])
    assert extract_table_claims(t, "c", "doc") == []


def test_exchanger_table_columns_become_qualifiers_and_tags_become_subjects():
    t = _table("p62_t1", 62, [
        ["Heat exchanger (shell side and tube side fluids)", "BH mode operation", "BH mode operation", "PG mode operation", "PG mode operation"],
        ["Heat exchanger (shell side and tube side fluids)", "Crude inlet temperature, °C", "Crude outlet temperature, °C", "Crude inlet temperature, °C", "Crude outlet temperature, °C"],
        ["11-E-01 (crude/ HN)", "30", "37.5", "30", "35"],
        ["11-E-04A/B (crude/TPA)", "66", "101", "60", "95"],
    ], header_rows=2)
    ctx = TableContext(table_id="p62_t1", label="PREHEAT TRAIN-I (PHT-I)")
    claims = extract_table_claims(t, "c", "doc", context=ctx)
    by = _by(claims)
    assert by[("11-E-01", ClaimCategory.OPERATING_TEMPERATURE, "37.5")].qualifier == \
        "PREHEAT TRAIN-I (PHT-I) / BH mode operation Crude outlet"
    assert by[("11-E-01", ClaimCategory.OPERATING_TEMPERATURE, "35")].qualifier == \
        "PREHEAT TRAIN-I (PHT-I) / PG mode operation Crude outlet"
    assert ("11-E-04A/B", ClaimCategory.OPERATING_TEMPERATURE, "101") in by
    e01 = [c for c in claims if c.subject == "11-E-01"]
    assert len(e01) == 4 and len({c.qualifier for c in e01}) == 4     # four measurements, no self-conflict


def test_product_yield_table_keeps_vol_and_wt_apart_and_carries_case_label():
    t = _table("p24_t1", 24, [
        ["Product", "TBP cut Range C", "Sp. Gr @15C", "Vol % cut", "Wt % cut", "kg/h", "m³h@ 15C", "MTPA"],
        ["Diesel", "270-380", "0.85275", "17.87", "17.97", "66066", "7.474", "539099"],
        ["Crude", "", "0.848", "100", "100", "367647", "433.545", "3000000"],
    ])
    ctx = TableContext(table_id="p24_t1", label="Atmospheric Distillation column (Basrah Crude) / SKO operation")
    by = _by(extract_table_claims(t, "c", "doc", context=ctx))
    vol = by[("Diesel", ClaimCategory.YIELD, "17.87")]
    wt = by[("Diesel", ClaimCategory.YIELD, "17.97")]
    assert vol.qualifier.endswith("/ Vol cut") and wt.qualifier.endswith("/ Wt cut")
    assert vol.qualifier.startswith("Atmospheric Distillation column (Basrah Crude) / SKO operation")
    assert by[("Diesel", ClaimCategory.CUT_RANGE, "270-380")].unit == "°C"
    flow = by[("Diesel", ClaimCategory.VOLUMETRIC_FLOW_RATE, "7.474")]        # "m³h" without slash
    assert flow.unit == "m³/h" and flow.qualifier.endswith("/ @ 15 °C")
    assert by[("Diesel", ClaimCategory.SPECIFIC_GRAVITY, "0.85275")].qualifier.endswith("/ @ 15 °C")
    # the feed total row keeps the operating-case label too
    assert by[("Crude", ClaimCategory.MASS_FLOW_RATE, "367647")].qualifier.startswith("Atmospheric Distillation column")


def test_two_column_parameter_table_uses_label_subject_not_header_word():
    t = _table("p64_t1", 64, [
        ["Parameter", "Specifications"],
        ["Electric power requirement", "20 Kv"],
        ["Pressure drop across the mixed valve (kg/cm2)", "0.7 - 1.0"],
        ["Operating temperature °C", "120-130 ° C"],
        ["Operating pressure (kg/cm2)", "10.5"],
    ])
    ctx = TableContext(table_id="p64_t1", label="Desalter (11-V-02) Description")
    by = _by(extract_table_claims(t, "c", "doc", context=ctx))
    assert ("11-V-02", ClaimCategory.OPERATING_PRESSURE, "10.5") in by     # tag in the label wins
    assert ("11-V-02", ClaimCategory.OPERATING_TEMPERATURE, "120-130") in by
    assert not any(s == "Specifications" for s, _, _ in by)
    dp = by[("11-V-02", ClaimCategory.GENERIC_PROPERTY, "0.7-1.0")]
    assert dp.qualifier == "Pressure drop across the mixed valve"


def test_headerless_key_value_table_and_utility_consumption_table():
    kv = _table("p34_t1", 34, [
        ["pH", "6.5-8.0"],
        ["Pressure (Kg/cm 2 )at grade", "3.0"],
        ["Mech. Design Pressure (Kg/cm 2 )", "7.0"],
    ], header_rows=0)
    by = _by(extract_table_claims(kv, "c", "doc", subject_hint="3.5.4 Service water:"))
    assert ("Service water", ClaimCategory.OPERATING_PRESSURE, "3.0") in by
    assert ("Service water", ClaimCategory.DESIGN_PRESSURE, "7.0") in by
    assert by[("Service water", ClaimCategory.GENERIC_PROPERTY, "6.5-8.0")].qualifier == "pH"
    assert not any(s == "6.5-8.0" for s, _, _ in by)

    util = _table("p35_t3", 35, [
        ["Utility", "Consumption"],
        ["L.P Steam (Kg/hr)", "1800"],
        ["H.P Steam (Kg/hr)", "*21100"],
        ["Cooling Sea Water (m3/hr)", "3257 (*3606)"],
    ])
    by = _by(extract_table_claims(util, "c", "doc", context=TableContext(table_id="p35_t3", label="Utilities")))
    assert by[("L.P Steam", ClaimCategory.MASS_FLOW_RATE, "1800")].unit == "kg/h"
    assert ("H.P Steam", ClaimCategory.MASS_FLOW_RATE, "21100") in by
    assert ("Cooling Sea Water", ClaimCategory.VOLUMETRIC_FLOW_RATE, "3257") in by
    assert not any(s == "Consumption" for s, _, _ in by)


def test_implausible_values_and_value_cleaning():
    t = _table("p44_t3", 44, [
        ["Property", "Basrah", "Bombay high"],
        ["Sp.Gravity@15 0 C", "10.56", "0.99"],          # OCR: 1.056
        ["Sulfur Wt %", "5.50", "0.68"],
        ["Smoke point mm.", "25", "NA"],
    ])
    by = _by(extract_table_claims(t, "c", "doc", context=TableContext(table_id="p44_t3", label="Vacuum Residue (VR/SR)")))
    assert ("Basrah", ClaimCategory.SPECIFIC_GRAVITY, "10.56") not in by
    assert ("Bombay high", ClaimCategory.SPECIFIC_GRAVITY, "0.99") in by
    smoke = by[("Basrah", ClaimCategory.GENERIC_PROPERTY, "25")]
    assert smoke.qualifier == "Vacuum Residue (VR/SR) / Smoke point" and smoke.unit == "mm"
    assert clean_value("9.0 cst @ 20°C") == ("9.0", "cSt", "@ 20 °C")
    assert clean_value("2.876 KV @ 40 °C") == ("2.876", "kV", "@ 40 °C")
    # ... and a viscosity row turns "KV" into centistokes while a power row keeps kilovolts
    visc = _table("v", 38, [["Property", "Basrah", "Bombay high"], ["Viscosity", "9.0 cst @ 20°C", "2.876 KV @ 40 °C"],
                            ["Pour point , 0 C", "-15", "+30"]])
    byv = _by(extract_table_claims(visc, "c", "doc"))
    assert byv[("Bombay high", ClaimCategory.VISCOSITY, "2.876")].unit == "cSt"
    assert byv[("Bombay high", ClaimCategory.VISCOSITY, "2.876")].qualifier == "@ 40 °C"
    assert clean_value("16.87 Kg\\Cm 2") == ("16.87", "kg/cm2", "")
    assert clean_value("0.45*") == ("0.45", "", "")

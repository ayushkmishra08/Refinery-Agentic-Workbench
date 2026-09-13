"""Tests for claim context dimensions: comparable claims, pressure basis, table roles."""

from schemas.claims import ClaimCategory, EngineeringClaim, leading_number
from schemas.parsed_document import ParsedTable, ParsedTableCell
from src.table_claims import extract_table_claims, parameter_role_for, scenario_for
from src.validator import normalize_unit, pressure_basis


def _claim(**kw):
    base = dict(claim_id="c", subject="11-P-01A", predicate=ClaimCategory.OPERATING_PRESSURE, value="24.45",
                unit="kg/cm2", evidence="e", page=1)
    base.update(kw)
    return EngineeringClaim(**base)


def test_context_key_separates_roles_locations_modes_scenarios_and_basis():
    base = _claim()
    assert _claim(location="suction").context_key() != _claim(location="discharge").context_key()
    assert _claim(parameter_role="normal").context_key() != _claim(parameter_role="maximum").context_key()
    assert _claim(operating_mode="startup").context_key() != _claim(operating_mode="emergency").context_key()
    assert _claim(scenario="Basrah").context_key() != _claim(scenario="Bombay High").context_key()
    assert _claim(pressure_basis="absolute").context_key() != _claim(pressure_basis="gauge").context_key()
    assert base.context_key() == _claim(value="99").context_key()          # value is not part of the key


def test_pressure_basis_and_unit_normalization_are_separate():
    assert normalize_unit("kg/cm2A") == "kg/cm2" and pressure_basis("kg/cm2A") == "absolute"
    assert normalize_unit("kg/cm2 g") == "kg/cm2" and pressure_basis("kg/cm2 g") == "gauge"
    assert pressure_basis("barg") == "gauge" and pressure_basis("bara") == "absolute"
    assert pressure_basis("kg/cm2") == "" and pressure_basis("°C") == ""
    assert normalize_unit("meters") == "m" and normalize_unit("m3/hr") == "m3/h"


def test_leading_number():
    assert leading_number("24.45") == 24.45
    assert leading_number("1,200 kg/h") == 1200.0
    assert leading_number("7-9") == 7.0
    assert leading_number("SS410") is None


def test_parameter_role_and_scenario_helpers():
    assert parameter_role_for("Mech. Design") == "mechanical_design"
    assert parameter_role_for("Pressure (Kg/cm2) / Maximum") == "maximum"
    assert parameter_role_for("Rated capacity") == "rated"
    assert scenario_for("Atmospheric Distillation column (Basrah Crude) / SKO operation") == "Basrah"
    assert scenario_for("BH mode operation Crude inlet temperature") == "BH mode"
    assert scenario_for("Diesel product") == ""


def _table(tid, rows, page=1):
    cells = [ParsedTableCell(row=r, col=c, content=t, is_header=(r == 0))
             for r, row in enumerate(rows) for c, t in enumerate(row)]
    return ParsedTable(table_id=tid, page=page, num_rows=len(rows), num_cols=len(rows[0]), cells=cells,
                       grid=[list(r) for r in rows])


def test_min_normal_max_design_columns_become_different_roles():
    t = _table("t1", [
        ["", "Minimum", "Normal", "Maximum", "Mech. Design"],
        ["Pressure (Kg/cm2 g)", "3.5", "4.0", "5.0", "6.5"],
        ["Temperature (0 C)", "150", "160", "170", "200"],
    ])
    claims = extract_table_claims(t, "c1", "d", "3.5 UTILITIES", subject_hint="3.5.1 LP Steam:")
    pressures = [c for c in claims if "press" in c.predicate.value]
    assert {c.predicate for c in pressures} == {
        ClaimCategory.MINIMUM_PRESSURE, ClaimCategory.NORMAL_PRESSURE, ClaimCategory.MAXIMUM_PRESSURE,
        ClaimCategory.DESIGN_PRESSURE,
    }
    roles = {c.parameter_role for c in pressures}
    assert roles == {"minimum", "normal", "maximum", "mechanical_design"}
    assert all(c.pressure_basis == "gauge" for c in pressures)
    assert len({c.context_key() for c in pressures}) == 4
    assert all(c.subject == "LP Steam" for c in claims)


def test_location_rows_describe_the_table_subject_not_an_entity_called_inlet():
    t = _table("t2", [
        ["", "Temperature, °C Maximum", "Temperature, °C Normal", "Pressure Normal"],
        ["Inlet", "95", "80", "5.0 mm Hg abs"],
        ["Outlet", "-", "-", "1.1 kg/cm 2 .a"],
    ])
    claims = extract_table_claims(t, "c1", "d", "16.11 Ejectors", subject_hint="16.11 Ejectors:")
    assert claims and all(c.subject == "Ejectors" for c in claims)
    inlet_t = [c for c in claims if c.location == "inlet" and "temp" in c.predicate.value]
    assert {c.parameter_role for c in inlet_t} == {"maximum", "normal"}
    outlet_p = next(c for c in claims if c.location == "outlet")
    assert outlet_p.pressure_basis == "absolute"


def test_molecular_weight_column_is_not_power():
    t = _table("t3", [
        ["Component", "Kg/hr", "MW", "Moles/hr", "° API"],
        ["Non condensable", "840", "33.7", "24.93", "-"],
    ])
    claims = extract_table_claims(t, "c1", "d", "16.11 Ejectors", subject_hint="16.11 Ejectors:")
    preds = {c.predicate for c in claims}
    assert ClaimCategory.MOLECULAR_WEIGHT in preds and ClaimCategory.POWER not in preds

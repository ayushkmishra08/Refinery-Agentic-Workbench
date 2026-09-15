"""Operating-envelope checks: a measured value against documented min / normal / max / design / trip.

The Calculation agent selects the claims (by context key); these functions only compare.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from workbench.core.knowledge import ClaimRecord
from workbench.services.calculators.units import Quantity, convert, family

ROLE_ORDER = ["minimum", "normal", "rated", "operating", "maximum", "design", "mechanical_design", "alarm", "trip", "relief", "test"]
PREDICATE_FAMILY = {
    "flow_rate": "volumetric_flow", "volumetric_flow_rate": "volumetric_flow", "capacity": "volumetric_flow", "mass_flow_rate": "mass_flow",
    "pressure": "pressure", "operating_pressure": "pressure", "design_pressure": "pressure", "discharge_pressure": "pressure", "suction_pressure": "pressure",
    "temperature": "temperature", "operating_temperature": "temperature", "design_temperature": "temperature",
}


@dataclass
class LimitMarker:
    label: str
    value: float
    unit: str
    claim: ClaimRecord


@dataclass
class LimitVerdict:
    verdict: str                       # within_normal | within_design | outside_design | unknown
    message: str
    markers: list[LimitMarker] = field(default_factory=list)
    value: Quantity | None = None
    unit: str = ""
    deviations: dict[str, float] = field(default_factory=dict)   # % deviation from normal / max


def _role_of(c: ClaimRecord) -> str:
    role = (c.parameter_role or "").lower()
    if role:
        return role
    if "design" in c.predicate:
        return "design"
    if "minimum" in (c.qualifier or "").lower() or "min" == (c.qualifier or "").lower():
        return "minimum"
    if "max" in (c.qualifier or "").lower():
        return "maximum"
    if "normal" in (c.qualifier or "").lower():
        return "normal"
    return "operating"


def markers_from_claims(claims: list[ClaimRecord], target_unit: str | None = None) -> list[LimitMarker]:
    out: list[LimitMarker] = []
    for c in claims:
        if c.numeric_value is None or not c.unit:
            continue
        unit = c.unit
        value = c.numeric_value
        if target_unit and family(unit) != "unknown" and family(target_unit) != "unknown" and family(unit) != family(target_unit):
            continue                                       # a temperature can never bound a flow check
        if target_unit and family(unit) == family(target_unit) and unit.lower() != target_unit.lower():
            try:
                value = convert(Quantity(value, unit, c.pressure_basis), target_unit).value
                unit = target_unit
            except ValueError:
                continue
        out.append(LimitMarker(_role_of(c), value, unit, c))
    # one marker per role: keep the first (claims are pre-ranked by authority)
    seen: set[str] = set()
    dedup = []
    for m in sorted(out, key=lambda m: ROLE_ORDER.index(m.label) if m.label in ROLE_ORDER else 99):
        if m.label in seen:
            continue
        seen.add(m.label)
        dedup.append(m)
    return dedup


def check_value(value: Quantity, claims: list[ClaimRecord]) -> LimitVerdict:
    markers = markers_from_claims(claims, target_unit=value.unit)
    if not markers:
        return LimitVerdict("unknown", "No documented limit with a comparable unit was found; the value cannot be judged from the documents.", [], value, value.unit)
    by = {m.label: m for m in markers}
    lo = by.get("minimum")
    hi = by.get("maximum") or by.get("rated")
    normal = by.get("normal") or by.get("operating")
    design = by.get("design") or by.get("mechanical_design")
    trip = by.get("trip")
    v = value.value
    deviations: dict[str, float] = {}
    if normal:
        deviations["vs_normal_pct"] = round((v - normal.value) / normal.value * 100, 1) if normal.value else 0.0
    if design:
        deviations["vs_design_pct"] = round((v - design.value) / design.value * 100, 1) if design.value else 0.0
    parts = []
    verdict = "unknown"
    if trip and v >= trip.value:
        verdict = "outside_design"
        parts.append(f"{v:g} {value.unit} reaches or exceeds the documented trip value {trip.value:g} {trip.unit}.")
    elif design and v > design.value:
        verdict = "outside_design"
        parts.append(f"{v:g} {value.unit} exceeds the design value {design.value:g} {design.unit} ({deviations.get('vs_design_pct', 0):+.1f}%).")
    elif (hi and v > hi.value) or (lo and v < lo.value):
        verdict = "within_design" if design else "outside_design"
        bound = hi if hi and v > hi.value else lo
        rel = "at" if design and abs(v - design.value) < 1e-9 else "below"
        parts.append(f"{v:g} {value.unit} is outside the documented {bound.label} value {bound.value:g} {bound.unit}"
                     + (f" but {rel} the design value {design.value:g} {design.unit}." if design and verdict == "within_design" else "."))
    elif normal and abs(v - normal.value) / max(normal.value, 1e-9) <= 0.05:
        verdict = "within_normal"
        parts.append(f"{v:g} {value.unit} is within 5% of the documented normal value {normal.value:g} {normal.unit}.")
    elif lo or hi or normal:
        verdict = "within_normal" if (lo or hi) else "within_design"
        ref = " / ".join(f"{m.label} {m.value:g} {m.unit}" for m in markers if m.label in ("minimum", "normal", "maximum", "rated"))
        parts.append(f"{v:g} {value.unit} lies inside the documented operating range ({ref}).")
    elif design:
        verdict = "within_design"
        parts.append(f"{v:g} {value.unit} is below the design value {design.value:g} {design.unit}; no normal operating range is documented.")
    if normal and verdict != "within_normal":
        parts.append(f"Deviation from normal ({normal.value:g} {normal.unit}): {deviations.get('vs_normal_pct', 0):+.1f}%.")
    return LimitVerdict(verdict, " ".join(parts), markers, value, value.unit, deviations)

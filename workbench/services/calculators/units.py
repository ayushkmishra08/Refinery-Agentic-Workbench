"""Unit handling for refinery process data. Pure functions, no LLM.

Absolute and gauge pressure are different quantities: kg/cm2A -> kg/cm2G subtracts the
atmospheric pressure (1.033 kg/cm2) and the result says so explicitly; no silent conversion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ATM_KG_CM2 = 1.033

PRESSURE_TO_KG_CM2 = {  # multiply to get kg/cm2
    "kg/cm2": 1.0, "kg/cm²": 1.0, "kgf/cm2": 1.0, "kg/cm2a": 1.0, "kg/cm2g": 1.0, "ata": 1.0, "atg": 1.0,
    "bar": 1.01972, "bara": 1.01972, "barg": 1.01972, "kpa": 0.0101972, "mpa": 10.1972, "psi": 0.0703070, "psig": 0.0703070, "psia": 0.0703070,
    "mmhg": 0.00135951, "torr": 0.00135951, "mmwc": 0.0001, "mm wc": 0.0001, "atm": 1.033,
}
FLOW_TO_M3H = {"m3/h": 1.0, "m³/h": 1.0, "m3/hr": 1.0, "m3h": 1.0, "l/h": 0.001, "l/min": 0.06, "lpm": 0.06, "gpm": 0.227125, "usgpm": 0.227125, "m3/d": 1 / 24, "bpd": 0.00662447, "bbl/d": 0.00662447}
MASS_FLOW_TO_KGH = {"kg/h": 1.0, "kg/hr": 1.0, "t/h": 1000.0, "tph": 1000.0, "mt/h": 1000.0, "kg/s": 3600.0, "mtpa": 1e6 / 8760.0, "mmtpa": 1e9 / 8760.0}
TEMP_UNITS = {"c", "°c", "degc", "deg c", "℃", "f", "°f", "degf", "k"}


@dataclass
class Quantity:
    value: float
    unit: str
    basis: str | None = None       # absolute | gauge for pressures

    def __str__(self) -> str:
        b = f" ({self.basis})" if self.basis else ""
        return f"{self.value:g} {self.unit}{b}"


def normalize_unit(unit: str | None) -> str:
    if not unit:
        return ""
    u = unit.strip().lower().replace(" ", "").replace("²", "2").replace("³", "3")
    u = u.replace("kgf", "kg").replace("deg", "").replace("°", "")
    u = {"c": "°C", "f": "°F", "k": "K", "m3/hr": "m3/h", "kg/hr": "kg/h", "m3h": "m3/h", "℃": "°C"}.get(u, u)
    return u


def pressure_basis(unit: str | None, text: str = "") -> str | None:
    u = (unit or "").lower().replace(" ", "")
    t = text.lower()
    if u.endswith("a") and u not in ("mpa", "kpa", "ata") or "absolute" in t or "(a)" in t or "ata" in u:
        return "absolute"
    if u.endswith("g") or "gauge" in t or "(g)" in t:
        return "gauge"
    return None


def family(unit: str | None) -> str:
    u = normalize_unit(unit).lower()
    if not u:
        return "unknown"
    if u in TEMP_UNITS or u in ("°c", "°f"):
        return "temperature"
    if u in PRESSURE_TO_KG_CM2 or u.rstrip("ag") in PRESSURE_TO_KG_CM2:
        return "pressure"
    if u in FLOW_TO_M3H:
        return "volumetric_flow"
    if u in MASS_FLOW_TO_KGH:
        return "mass_flow"
    if u in ("%", "percent", "vol%", "wt%"):
        return "percentage"
    if u in ("m", "mm", "cm", "km", "ft", "in"):
        return "length"
    if u in ("rpm",):
        return "speed"
    if u in ("kw", "mw", "hp"):
        return "power"
    return "unknown"


def convert(q: Quantity, to_unit: str) -> Quantity:
    """Convert within a family. Raises ValueError across families or absolute<->gauge without basis."""
    fam_from, fam_to = family(q.unit), family(to_unit)
    if fam_from != fam_to:
        raise ValueError(f"cannot convert {q.unit} ({fam_from}) to {to_unit} ({fam_to})")
    u_from, u_to = normalize_unit(q.unit).lower(), normalize_unit(to_unit).lower()
    if fam_from == "temperature":
        c = q.value if u_from in ("°c", "c") else (q.value - 32) * 5 / 9 if u_from in ("°f", "f") else q.value - 273.15
        out = c if u_to in ("°c", "c") else c * 9 / 5 + 32 if u_to in ("°f", "f") else c + 273.15
        return Quantity(round(out, 3), normalize_unit(to_unit))
    if fam_from == "pressure":
        kg = q.value * PRESSURE_TO_KG_CM2[u_from if u_from in PRESSURE_TO_KG_CM2 else u_from.rstrip("ag")]
        basis_from = q.basis or pressure_basis(q.unit)
        basis_to = pressure_basis(to_unit)
        if basis_from and basis_to and basis_from != basis_to:
            kg = kg - ATM_KG_CM2 if basis_to == "gauge" else kg + ATM_KG_CM2
        factor = PRESSURE_TO_KG_CM2[u_to if u_to in PRESSURE_TO_KG_CM2 else u_to.rstrip("ag")]
        return Quantity(round(kg / factor, 4), normalize_unit(to_unit), basis_to or basis_from)
    table = FLOW_TO_M3H if fam_from == "volumetric_flow" else MASS_FLOW_TO_KGH if fam_from == "mass_flow" else None
    if table is None:
        if u_from == u_to:
            return Quantity(q.value, normalize_unit(to_unit))
        raise ValueError(f"no conversion table for {fam_from}")
    base = q.value * table[u_from]
    return Quantity(round(base / table[u_to], 4), normalize_unit(to_unit))


QUANTITY_RE = re.compile(
    r"(?<![\w.])(-?\d+(?:[.,]\d+)?)\s*(kg/cm2\s*[ag]?|kg/cm²\s*[ag]?|kgf/cm2|bar[ag]?|kpa|mpa|psi[ag]?|mmhg|mmwc|m3/h|m³/h|m3/hr|kg/h|kg/hr|t/h|tph|mtpa|mmtpa|"
    r"°c|deg\s*c|℃|°f|rpm|kw|mw|%|percent|mm|m)\b",
    re.IGNORECASE,
)
PARAMETER_FOR_FAMILY = {"pressure": "pressure", "temperature": "temperature", "volumetric_flow": "flow_rate", "mass_flow": "flow_rate", "speed": "speed", "power": "power", "percentage": "percentage"}


def find_quantities(text: str) -> list[Quantity]:
    out = []
    for m in QUANTITY_RE.finditer(text):
        val = float(m.group(1).replace(",", "."))
        unit = re.sub(r"\s+", "", m.group(2))
        unit = normalize_unit(unit) if unit.lower() not in ("m", "mm") else unit
        out.append(Quantity(val, unit, pressure_basis(unit)))
    return out

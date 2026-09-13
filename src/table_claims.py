"""Deterministic table-to-claims mapping.

Engineering tables carry most of a manual's numbers.  Instead of asking the
LLM to read pipe-separated grids, this module maps parsed table cells to
EngineeringClaim objects:

  * Layout B  - subject rows x property columns
        Tag No | Description | Operating Temp (C) | Operating Pressure (Kg/cm2g)
        Product | TBP cut range degC | Temperature degC | Pressure, Kg/cm2 A
  * Layout D  - subject rows x one value column, property named in the row label
        Utility | Consumption
        L.P Steam (Kg/hr) | 1800
  * Layout A  - property rows x condition/value columns (subject = table label)
        <blank> | Minimum | Normal | Maximum | Mech. Design
        Pressure (Kg/cm2) | 3.5 | 4.0 | 5.0 | 6.5
        Parameter | Specifications
        Operating pressure (kg/cm2) | 10.5
  * Layout C  - property rows x subject columns
        Property | Basrah | Bombay high
        Sp.Gravity@15 C | 0.8527 | 0.855

Every claim carries a ``qualifier`` that says *which* measurement it is, so that
legitimately different numbers on one subject are not reported as contradictions:
  - the table's label / operating case from src.table_context
    ("Diesel (DSL)", "Atmospheric Distillation column (Basrah Crude) / SKO operation"),
  - the column condition for Layout B ("BH mode operation Crude inlet", "Vol cut"),
  - the row reference condition for Layout C/A ("@ 20 °C"), the distillation point
    ("ASTM D-86 IBP") or, for generic_property, the property name ("Total Sulfur").

Continuation tables (a table split by a page break, whose first row is data or
ASTM points) inherit the header row of the table they continue.

Evidence is the row's cells joined with " | " (exactly how the table appears in
the chunk text), page is the table page, ``is_from_table=True`` and ``table_id``
are set, ``source='table'``.  The unit validator still applies.
"""

from __future__ import annotations

import re

from schemas.claims import ClaimCategory, EngineeringClaim, leading_number
from schemas.parsed_document import ParsedTable
from src.entity_identity import TAG_SCAN_RE, parse_tag
from src.table_context import (
    TableContext,
    clean_label,
    is_astm_label,
    is_distillation_point,
    is_numeric_like,
    scenario_for,
)
from src.validator import normalize_unit, pressure_basis

_ROLE_BY_COND = {
    "minimum": "minimum", "normal": "normal", "operating": "operating", "maximum": "maximum",
    "design": "design", "test": "test", "relief": "relief",
}
_MECH_DESIGN_RE = re.compile(r"mech\.?\s*design|mechanical\s*design", re.I)
_RATED_RE = re.compile(r"\brated\b|\bguaranteed\b", re.I)
_LOCATION_RE = re.compile(
    r"\b(suction|discharge|inlet|outlet|top|bottom|overhead|flash\s*zone|shell|tube|reflux|stripping|"
    r"skin|arch|stack|pumparound|draw|casing)\b", re.I,
)
_BASIS_IN_TEXT_RE = re.compile(
    r"kg\s*/\s*cm\s*[2²]\s*\.?\s*\(?\s*(a|g|abs|absolute|gauge)\b\s*\)?|\bbar\s*\(?(a|g|abs|absolute|gauge)\b\)?|"
    r"\bpsi\s*(a|g|abs|absolute|gauge)\b|\bmm\s*hg\s*(a|abs|absolute)\b", re.I,
)


def parameter_role_for(*texts: str) -> str:
    """design | normal | minimum | maximum | operating | mechanical_design | test | relief | rated | ''."""
    joined = " ".join(t for t in texts if t)
    if _MECH_DESIGN_RE.search(joined):
        return "mechanical_design"
    if _RATED_RE.search(joined):
        return "rated"
    cond = _condition_from(joined)
    return _ROLE_BY_COND.get(cond or "", "")


def location_for(*texts: str) -> str:
    for t in texts:
        m = _LOCATION_RE.search(t or "")
        if m:
            return re.sub(r"\s+", "_", m.group(1).lower())
    return ""


def basis_from_text(text: str) -> str:
    """Pressure basis written in a header or cell: 'Pressure, Kg/cm2 A' -> 'absolute'."""
    m = _BASIS_IN_TEXT_RE.search(text or "")
    if not m:
        return ""
    letter = next(g for g in m.groups() if g).lower()
    return "absolute" if letter.startswith("a") else "gauge"

TABLE_CONFIDENCE = 0.8

_CONDITION_WORDS = {
    "minimum": "minimum", "min": "minimum", "min.": "minimum",
    "normal": "normal", "nor": "normal", "operating": "operating", "oper": "operating",
    "maximum": "maximum", "max": "maximum", "max.": "maximum",
    "design": "design", "mech. design": "design", "mech design": "design", "mechanical design": "design",
    "test": "test", "relief": "relief", "set": "relief",
}
_PRESSURE_BY_COND = {
    "minimum": ClaimCategory.MINIMUM_PRESSURE, "normal": ClaimCategory.NORMAL_PRESSURE,
    "maximum": ClaimCategory.MAXIMUM_PRESSURE, "design": ClaimCategory.DESIGN_PRESSURE,
    "operating": ClaimCategory.OPERATING_PRESSURE, "test": ClaimCategory.TEST_PRESSURE,
    "relief": ClaimCategory.RELIEF_PRESSURE,
}
_TEMPERATURE_BY_COND = {
    "minimum": ClaimCategory.MINIMUM_TEMPERATURE, "normal": ClaimCategory.NORMAL_TEMPERATURE,
    "maximum": ClaimCategory.MAXIMUM_TEMPERATURE, "design": ClaimCategory.DESIGN_TEMPERATURE,
    "operating": ClaimCategory.OPERATING_TEMPERATURE,
}

# Pressures that are properties of a fluid, not operating conditions of equipment
_PROPERTY_PRESSURE_RE = re.compile(r"vapou?r\s*pressure|\brvp\b|pressure\s*drop|differential\s*pressure|\bdp\b", re.I)
_TPA_RE = re.compile(r"\b(?:ton(?:ne)?s?|tones|mt|t)\s*/\s*(?:annum|year|yr|a)\b|\bmmtpa\b|\bmtpa\b|\btpa\b", re.I)
_GENERIC_PROPERTY_RE = re.compile(
    r"smoke\s*point|freez(?:e|ing)\s*point|cloud\s*point|aniline\s*point|diesel\s*index|cetane|octane|"
    r"\be\.?o\.?n\b|\bron\b|\bmon\b|paraffins?|naphthenes?|aromatics?|olefins?|light\s*ends|mercaptans?|"
    r"conradson|carbon\s*residue|characteri[sz]ation|\bccr\b|\bash\b|\bbs&w\b|sediments?|"
    r"sulfur|sulphur|\bsalt\b|\bwax\b|vanadium|nickel|\biron\b|sodium|\bwater\b|h2s|"
    r"asphaltenes?|metals?|nitrogen|\bn\s*2\b|\bacid\b|\bph\b|colou?r|dryness|volatility|"
    r"\bapi\b|smoke",
    re.I,
)

# (regex on property/header text, predicate, default unit) -- order matters
_PROPERTY_RULES: list[tuple[re.Pattern, ClaimCategory | None, str | None]] = [
    (re.compile(r"\bpress", re.I), None, None),                 # resolved with condition
    (re.compile(r"\btemp", re.I), None, None),                  # resolved with condition
    (_TPA_RE, ClaimCategory.CAPACITY, "TPA"),
    (re.compile(r"\b(?:kg\s*/\s*hr?|t\s*/\s*hr?|mt\s*/\s*hr?|tph|kg/h)\b", re.I), ClaimCategory.MASS_FLOW_RATE, "kg/h"),
    (re.compile(r"(?:m\s*3|m³|cu\.?\s*m)\s*/?\s*hr?\b", re.I), ClaimCategory.VOLUMETRIC_FLOW_RATE, "m3/h"),
    (re.compile(r"sp\.?\s*gr|specific\s+gravity", re.I), ClaimCategory.SPECIFIC_GRAVITY, ""),
    (re.compile(r"\bdensity\b", re.I), ClaimCategory.DENSITY, "kg/m3"),
    (re.compile(r"\bviscosity\b|\bkv\b", re.I), ClaimCategory.VISCOSITY, "cSt"),
    (re.compile(r"pour\s*point", re.I), ClaimCategory.POUR_POINT, "°C"),
    (re.compile(r"flash\s*point", re.I), ClaimCategory.FLASH_POINT, "°C"),
    (_GENERIC_PROPERTY_RE, ClaimCategory.GENERIC_PROPERTY, ""),
    (re.compile(r"\btbp\b|cut\s*range|boiling\s*range", re.I), ClaimCategory.CUT_RANGE, "°C"),
    (re.compile(r"\byield\b|%\s*cut|cut\s*on", re.I), ClaimCategory.YIELD, "%"),
    (re.compile(r"\bpower\b|\bkw\b|\bmw\b", re.I), ClaimCategory.POWER, "kW"),
    (re.compile(r"\bspeed\b|\brpm\b", re.I), ClaimCategory.SPEED, "rpm"),
    (re.compile(r"\bdiameter\b|\bdia\b", re.I), ClaimCategory.DIAMETER, "mm"),
    (re.compile(r"\bcapacity\b|\bduty\b|\brate\b", re.I), ClaimCategory.CAPACITY, ""),
    (re.compile(r"\bmm\b|thickness", re.I), ClaimCategory.THICKNESS, "mm"),
]

_UNIT_RE = re.compile(
    r"(kg\s*/\s*cm\s*2\s*[ga]?|kg/cm²\s*[ga]?|bar[ga]?|psi[ga]?|kpa|mpa|mmwc|mmhg|"
    r"°\s*c|deg\.?\s*c|℃|\(c\)|(?<=\d)\s*0\s*c\b|°\s*f|deg\.?\s*f|"
    r"kg\s*/\s*hr?|t\s*/\s*hr?|mt\s*/\s*hr?|tph|m\s*3\s*/?\s*hr?|m³\s*/?\s*hr?|nm3/hr?|mmtpa|mtpa|tpa|"
    r"wt\s*%|vol\s*%|%\s*wt|%\s*vol|%|ppm|ppb|ptb|cst|\bcp\b|\bkv\b|mg/l|kw|mw|rpm|\bmm\b|°api)",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"\d")
_SKIP_VALUES = {"", "-", "–", "nil", "na", "n/a", "none", "n.a.", "--", "nd", "n.d."}
_TEXT_VALUE_PREDICATES = {ClaimCategory.CUT_RANGE, ClaimCategory.MATERIAL, ClaimCategory.MATERIAL_GRADE}
_NUMERIC_LEAD_RE = re.compile(r"^\s*[<>≤≥~±+\-*]*\s*\d")

# "@ 20 °C", "at 15/4 °C" -- only numeric reference conditions
_CONDITION_RE = re.compile(r"\s*(?:@|\bat\b)\s*(?=[<>~]?\s*[+\-]?\d)([^,;|]*)", re.IGNORECASE)
_VALUE_UNIT_RE = re.compile(
    r"^\s*(?P<val>[<>≤≥~±]?\s*[+\-]?\d[\d.,]*(?:\s*[-–/+]\s*\d[\d.,]*)*\+?)\s*"
    r"(?P<unit>[A-Za-z°%µ][A-Za-z0-9°%/²³.\- ]{0,12})?\s*$"
)
_STRIP_PROPERTY_WORDS_RE = re.compile(
    r"\b(?:temperature|temp\.?|pressure|press\.?|sp\.?\s*gr\.?|sp\.?\s*gravity|specific\s+gravity|gravity|"
    r"density|viscosity|flow|rate|capacity|duty|yield|cut\s*range|range|tbp|power|speed|diameter|thickness|"
    r"operating|oper\.?|content|total)\b\.?",
    re.I,
)
_BASIS_RE = re.compile(r"\b(vol|volume|wt|weight|mol|mole)\s*\.?\s*%", re.I)
_CONDITION_WORD_RE = re.compile(
    r"\b(?:minimum|min\.?|normal|nor\.?|maximum|max\.?|design|mech\.?|mechanical|test|relief|set)\b", re.I,
)
_FOOTNOTE_CHARS = "*†‡"

_GENERIC_LABELS = {
    "specification", "specifications", "value", "values", "consumption", "data", "unit", "units",
    "remarks", "remark", "description", "quantity", "qty", "parameter", "parameters", "temperature",
    "pressure", "range", "limit", "limits", "requirement", "requirements", "condition", "conditions",
    "design", "normal", "min", "max", "minimum", "maximum", "typical", "guaranteed", "actual", "rating",
    "ratings", "property", "properties", "details", "particulars", "nos", "no", "sl no", "sr no", "item",
    "case", "mode", "operation", "total", "figure", "figures", "source", "location", "type", "size",
}
_SUBJECT_HEADERS = {
    "utility", "utilities", "stream", "streams", "product", "products", "service", "services", "item", "items",
    "equipment", "fluid", "fluids", "feed", "feeds", "feed stocks", "feedstock", "feedstocks", "material",
    "materials", "chemical", "chemicals", "tag", "tag no", "tag no.", "medium", "line",
}


# --------------------------------------------------------------------------- #
# Small text helpers
# --------------------------------------------------------------------------- #
def _norm_cond(text: str) -> str:
    """'@37.8 0 C' -> '@ 37.8 °C'; 'at 15/4 °C' -> '@ 15/4 °C'."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"(\d)\s*(?:0\s*C|°\s*C|deg\.?\s*C|℃|º\s*C|C)(?![A-Za-z])", r"\1 °C", t, flags=re.I)
    t = re.sub(r"(\d)\s*(?:0\s*F|°\s*F|deg\.?\s*F|℉)(?![A-Za-z])", r"\1 °F", t, flags=re.I)
    t = re.sub(r"^\s*at\s+", "@ ", t, flags=re.I)
    t = re.sub(r"@\s*", "@ ", t)
    return t.strip(" ,;:")


def _split_condition(text: str) -> tuple[str, str]:
    """'Viscosity Cst @37.8 0 C' -> ('Viscosity Cst', '@ 37.8 °C')."""
    m = _CONDITION_RE.search(text or "")
    if not m:
        return (text or "").strip(), ""
    return (text[:m.start()] + " " + text[m.end():]).strip(), _norm_cond("@ " + m.group(1))


def _strip_units(text: str, keep_basis: bool = False) -> str:
    """Remove unit tokens; with ``keep_basis`` the vol/wt word of 'Vol %' survives ('Vol % cut' -> 'Vol cut')."""
    t = text or ""
    if keep_basis:
        t = _BASIS_RE.sub(r"\1 ", t)
    t = _UNIT_RE.sub(" ", t)
    t = re.sub(r"\(\s*\)|\[\s*\]", " ", t)
    return re.sub(r"\s+", " ", t).strip(" ,;:-–/.")


def _has_tag(text: str) -> bool:
    m = TAG_SCAN_RE.search(text or "")
    return bool(m and parse_tag(m.group(0)))


def _property_remainder(text: str, keep_basis: bool = False) -> str:
    """Text left when units, property and condition words are removed ('L.P Steam (Kg/hr)' -> 'L.P Steam')."""
    t, _ = _split_condition(text)
    t = _strip_units(t, keep_basis=keep_basis)
    t = _STRIP_PROPERTY_WORDS_RE.sub(" ", t)
    t = _CONDITION_WORD_RE.sub(" ", t)
    t = re.sub(r"[,;:()\[\]]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–/.")
    return t if re.search(r"[A-Za-z]", t) else ""


def _col_qualifier(header: str) -> str:
    """Column-axis qualifier: 'BH mode operation Crude inlet temperature, °C' -> 'BH mode operation Crude inlet'."""
    rest, cond = _split_condition(header)
    remainder = _property_remainder(rest, keep_basis=True)
    return _join_qualifier(remainder, cond)


def _row_qualifier(prop_text: str, predicate: ClaimCategory, dist_method: str = "") -> str:
    """Row-axis qualifier: reference condition, distillation point or (generic) property name."""
    if is_distillation_point(prop_text):
        point = re.sub(r"\s+", "", prop_text.strip().rstrip(":.")).upper() if "%" in prop_text else prop_text.strip().upper()
        point = re.sub(r"(\d)([A-Z])", r"\1 \2", point)
        return _join_qualifier(dist_method, point)
    rest, cond = _split_condition(prop_text)
    if predicate == ClaimCategory.GENERIC_PROPERTY:
        name = _strip_units(rest)
        name = re.sub(r"[,;:()\[\]]+", " ", name)
        name = re.sub(r"\s+", " ", name).strip(" -–/.")
        return _join_qualifier(name, cond)
    return cond


def _join_qualifier(*parts: str) -> str:
    out: list[str] = []
    for p in parts:
        p = re.sub(r"\s+", " ", p or "").strip(" ,;:/")
        if p and p.lower() not in {o.lower() for o in out}:
            out.append(p)
    return " / ".join(out)


def _is_generic_label(text: str) -> bool:
    t = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return not t or t in _GENERIC_LABELS or t.rstrip("s") in _GENERIC_LABELS


def _is_subject_header(text: str) -> bool:
    t = re.sub(r"[^a-z. ]+", " ", (text or "").lower()).strip()
    return t in _SUBJECT_HEADERS


def split_value_unit(value: str) -> tuple[str, str]:
    """'9.0 cst' -> ('9.0', 'cSt'); '367647' -> ('367647', ''); '7-9' -> ('7-9', '')."""
    m = _VALUE_UNIT_RE.match(value or "")
    if not m:
        return (value or "").strip(), ""
    unit = (m.group("unit") or "").strip()
    if "/" in unit:
        unit = re.sub(r"\s+", "", unit)          # "Kg/Cm 2" -> "Kg/Cm2"
    if unit.lower() == "kv":
        unit = "kV"                              # emit() turns this into cSt for viscosity rows
    return re.sub(r"\s+", "", m.group("val")), (normalize_unit(unit) if unit else "")


def clean_value(raw: str) -> tuple[str, str, str]:
    """'9.0 cst @ 20°C' -> ('9.0', 'cSt', '@ 20 °C'); '*21100' -> ('21100', '', ''); '3257 (*3606)' -> ('3257', '', '')."""
    v = (raw or "").strip().strip(_FOOTNOTE_CHARS + " ").replace("\\", "/")
    cond = ""
    m = re.search(r"\s*(?:@|\bat\b)\s*(.+)$", v, flags=re.I)
    if m and re.search(r"\d", m.group(1)):
        cond = _norm_cond("@ " + m.group(1))
        v = v[:m.start()].strip()
    v = re.sub(r"\s*\([^)]*\)\s*$", "", v).strip().rstrip(_FOOTNOTE_CHARS).strip()
    val, unit = split_value_unit(v)
    return val, unit, cond


def _unit_from(text: str) -> str:
    # Drop reference conditions ("Sp. Gravity @ 15 °C", "RVP @ 100 °F, psi") before reading the unit
    text, _ = _split_condition(text or "")
    m = _UNIT_RE.search(text or "")
    if not m:
        return ""
    u = m.group(1).strip()
    u = re.sub(r"\s+", "", u)
    if u.lower() in ("(c)", "c", "0c"):
        u = "°C"
    if u.lower() == "kv":
        u = "kV"
    return normalize_unit(u)


def _condition_from(text: str) -> str | None:
    t = (text or "").strip().lower()
    for k, v in _CONDITION_WORDS.items():
        if re.search(rf"\b{re.escape(k)}\b", t):
            return v
    return None


def _predicate_for(prop_text: str, condition: str | None) -> tuple[ClaimCategory | None, str]:
    """Map a property/header text (+ optional condition column) to a predicate and unit."""
    text = prop_text or ""
    unit = _unit_from(text)
    if is_distillation_point(text):
        return ClaimCategory.DISTILLATION_TEMPERATURE, "°C"   # the "%" is the recovered volume, not the unit
    if _PROPERTY_PRESSURE_RE.search(text):
        return ClaimCategory.GENERIC_PROPERTY, unit
    cond = condition or _condition_from(text) or "operating"
    if re.search(r"\bpress", text, re.I):
        return _PRESSURE_BY_COND.get(cond, ClaimCategory.OPERATING_PRESSURE), unit or "kg/cm2"
    if re.search(r"\btemp", text, re.I):
        return _TEMPERATURE_BY_COND.get(cond, ClaimCategory.OPERATING_TEMPERATURE), unit or "°C"
    for rx, pred, default_unit in _PROPERTY_RULES[2:]:
        if rx.search(text):
            if unit.lower() == "kv":
                unit = "cSt" if pred == ClaimCategory.VISCOSITY else "kV"
            return pred, unit or (default_unit or "")
    return None, unit


def _is_property_text(text: str) -> bool:
    return _predicate_for(text, None)[0] is not None


def _header_rows(table: ParsedTable) -> int:
    rows = {c.row for c in table.cells if c.is_header}
    if rows:
        return max(rows) + 1
    return 1


def _grid(table: ParsedTable) -> list[list[str]]:
    if table.grid:
        return [[(c or "").strip() for c in row] for row in table.grid]
    n_rows, n_cols = table.num_rows, table.num_cols
    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for c in table.cells:
        for r in range(c.row, min(c.row + c.rowspan, n_rows)):
            for k in range(c.col, min(c.col + c.colspan, n_cols)):
                grid[r][k] = c.content.strip()
    return grid


def _good_value(v: str, predicate: ClaimCategory | None = None) -> bool:
    v = (v or "").strip()
    if v.lower() in _SKIP_VALUES or not _NUMERIC_RE.search(v) or len(v) > 40:
        return False
    if predicate in _TEXT_VALUE_PREDICATES:
        return True
    return bool(_NUMERIC_LEAD_RE.match(v))


def _plausible(predicate: ClaimCategory, value: str, unit: str) -> bool:
    """Reject values that cannot be what the predicate says (OCR damage: '10.56' as a gravity)."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", (value or "").replace(",", ""))
    if not nums:
        return True
    try:
        x = float(nums[0])
    except ValueError:
        return True
    if predicate == ClaimCategory.SPECIFIC_GRAVITY:
        return 0.3 <= x <= 1.3
    if predicate == ClaimCategory.YIELD and unit in ("", "%"):
        return 0.0 <= x <= 100.0
    if predicate in (ClaimCategory.FLASH_POINT, ClaimCategory.POUR_POINT):
        return -120.0 <= x <= 500.0
    if predicate == ClaimCategory.DISTILLATION_TEMPERATURE:
        return -50.0 <= x <= 900.0
    return True


def _clean_subject(cell: str) -> str:
    """Prefer the asset tag when a cell mixes tag and description: '11-E-01 (crude/ HN)' -> '11-E-01'."""
    s = re.sub(r"\s+", " ", (cell or "")).strip().strip(_FOOTNOTE_CHARS + " ").strip(" :")
    m = TAG_SCAN_RE.search(s)
    if m and parse_tag(m.group(0)) and len(m.group(0).strip()) < len(s):
        s = m.group(0).strip()
    return s


def _is_bad_subject(subject: str, tag_col: bool = False) -> bool:
    s = (subject or "").strip()
    if not s or len(s) > 80:
        return True
    if is_numeric_like(s) or is_distillation_point(s) or is_astm_label(s) or _is_generic_label(s):
        return True
    return _is_property_text(s) and not tag_col and len(s) > 40


def extract_table_claims(
    table: ParsedTable,
    chunk_id: str,
    document_id: str,
    section: str = "",
    subject_hint: str | None = None,
    classification: str | None = None,
    context: TableContext | None = None,
    inherit_from: ParsedTable | None = None,
) -> list[EngineeringClaim]:
    """Map one parsed table to claims. Returns [] when the layout is not recognised.

    ``context`` (from src.table_context) supplies the table's label / operating case
    and, for a page-split continuation, the table whose header row to inherit
    (``inherit_from``).  ``subject_hint`` (the chunk's parent heading) is the
    fallback subject for property-row tables when no context is available.
    """
    grid = _grid(table)
    if not grid or table.num_cols < 2:
        return []

    # ---- continuation: inherit the header row of the table this one continues ----
    n_hdr: int | None = None
    if (inherit_from is not None and context is not None
            and context.continuation_of == inherit_from.table_id):
        base = _grid(inherit_from)
        if base and len(base[0]) == len(grid[0]):
            n_base_hdr = max(1, min(_header_rows(inherit_from), len(base) - 1))
            grid = base[:n_base_hdr] + grid
            n_hdr = n_base_hdr
    if len(grid) < 2:
        return []
    if n_hdr is None:
        n_hdr = min(_header_rows(table), len(grid) - 1)

    n_cols = len(grid[0])
    headers: list[str] = []
    for col in range(n_cols):
        parts = []
        for r in range(n_hdr):
            t = grid[r][col] if col < len(grid[r]) else ""
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        headers.append(" ".join(parts).strip())
    body = grid[n_hdr:]

    # A "header" row whose cells are numbers is data: the table has no header row.
    if any(is_numeric_like(h) for h in headers[1:] if h) and not any(
            h and not is_numeric_like(h) and not _is_property_text(h) for h in headers[1:]):
        headers = ["" for _ in range(n_cols)]
        body = grid

    claims: list[EngineeringClaim] = []
    label = clean_label(context.qualifier_label) if context and context.qualifier_label else ""
    caption = clean_label(table.caption or "")
    if caption and caption.lower() != label.lower():
        # A detected caption ("BH Mode Atmos-side Exchangers") is the most specific context there is
        label = _join_qualifier(label, caption)
    fallback_subject = label or clean_label(subject_hint or "")

    header_line = " | ".join(h for h in headers if h)
    tid_tail = re.sub(r"[^A-Za-z0-9]", "", table.table_id)[-8:]

    def emit(subject: str, predicate: ClaimCategory, value: str, unit: str, row: list[str],
             prop_text: str, qualifier_parts: list[str], with_header: bool = False,
             tag_col: bool = False) -> None:
        subject = _clean_subject(subject)
        if _is_bad_subject(subject, tag_col) or not _good_value(value, predicate):
            return
        val, val_unit, cond = clean_value(value)
        if not val:
            return
        if val_unit:
            unit = val_unit          # unit written inside the cell wins ("9.0 cst")
        if unit.lower() == "kv" and predicate == ClaimCategory.VISCOSITY:
            unit = "cSt"             # "2.876 KV @ 40 °C": kinematic viscosity
        if not _plausible(predicate, val, unit):
            return
        parts = [p for p in qualifier_parts if p and p.strip().lower() != subject.lower()]
        qualifier = _join_qualifier(*parts, cond)
        evidence = " | ".join(c for c in row if c)
        if with_header and header_line:
            evidence = f"{header_line}\n{evidence}"
        claims.append(EngineeringClaim(
            claim_id=f"{chunk_id}_{tid_tail}_t{len(claims)}",
            subject=subject,
            predicate=predicate,
            predicate_raw=f"table:{prop_text.strip()[:60]}",
            value=val,
            unit=unit,
            unit_normalized=unit,
            qualifier=qualifier,
            parameter_role=parameter_role_for(prop_text, *qualifier_parts),
            location=location_for(prop_text, *qualifier_parts),
            scenario=scenario_for(*qualifier_parts, prop_text, label) or (context.scenario if context is not None else ""),
            pressure_basis=pressure_basis(val_unit) or basis_from_text(f"{prop_text} {value}"),
            value_numeric=leading_number(val),
            evidence=evidence,
            page=table.page,
            section=section,
            document_id=document_id,
            chunk_id=chunk_id,
            confidence=TABLE_CONFIDENCE,
            is_from_table=True,
            table_id=table.table_id,
            source="table",
        ))

    def first_col(row: list[str]) -> str:
        return row[0] if row else ""

    tag_col = next((i for i, h in enumerate(headers) if re.search(r"\btag\b|\bequipment\b", h, re.I)), None)
    prop_cols = [i for i, h in enumerate(headers)
                 if i != (tag_col if tag_col is not None else 0) and _is_property_text(h)]
    # A cell that carries an asset tag names a subject even if its description mentions a unit
    # ("11-E-04A/B (crude/TPA)")
    first_col_props = sum(1 for r in body if r and _is_property_text(first_col(r)) and not _has_tag(first_col(r)))
    condition_cols = [i for i, h in enumerate(headers) if i > 0 and _condition_from(h)]
    subject_cols = [i for i, h in enumerate(headers)
                    if i > 0 and h and not _is_property_text(h) and not _is_generic_label(h) and not is_numeric_like(h)]
    generic_value_cols = [i for i, h in enumerate(headers)
                          if i > 0 and (not h or _is_generic_label(h) or is_numeric_like(h))]

    # ---- Layout B: a "Tag" column or property headers across the top -------------
    if prop_cols and (tag_col is not None or first_col_props < len(body) / 2):
        subj_col = tag_col if tag_col is not None else 0
        # "MW" next to Moles/hr, ° API or Component is molecular weight, not megawatts
        composition_table = any(re.search(r"moles|mol\.?\s*wt|molecular|\bapi\b|component|composition", h, re.I)
                                for h in headers)
        for row in body:
            if subj_col >= len(row):
                continue
            subject = row[subj_col]
            if not subject or is_distillation_point(subject) or is_astm_label(subject):
                continue
            if _is_property_text(subject) and tag_col is None and len(subject) > 40:
                continue
            # A row labelled by a location ("Inlet", "Outlet", "Shell side") describes the table's
            # subject at that location, not an entity called "Inlet"
            row_location = ""
            if _LOCATION_RE.fullmatch(subject.strip().rstrip(":")) and fallback_subject:
                row_location = subject.strip().rstrip(":")
                subject = fallback_subject
            for col in prop_cols:
                if col >= len(row):
                    continue
                pred, unit = _predicate_for(headers[col], None)
                if pred is None:
                    continue
                if composition_table and re.fullmatch(r"\s*mw\s*", headers[col], re.I):
                    pred, unit = ClaimCategory.MOLECULAR_WEIGHT, ""
                emit(subject, pred, row[col], unit, row, headers[col],
                     [label, _col_qualifier(headers[col]), row_location], tag_col=tag_col is not None)
        return claims

    # ---- Layout D: subject rows x one value column, property named in the row -----
    if (headers and _is_subject_header(headers[0]) and generic_value_cols
            and first_col_props >= max(1, len(body) // 2) and not subject_cols):
        for row in body:
            if not row or not _is_property_text(first_col(row)):
                continue
            subject = _property_remainder(first_col(row))
            pred, unit = _predicate_for(first_col(row), None)
            if pred is None or not subject:
                continue
            for col in generic_value_cols:
                if col >= len(row):
                    continue
                emit(subject, pred, row[col], unit, row, first_col(row),
                     [label, _row_qualifier(first_col(row), pred)], with_header=True)
        return claims

    # ---- Layout A: property rows x condition/value columns (subject = label) ------
    value_cols = condition_cols or (generic_value_cols if not subject_cols else [])
    if value_cols and first_col_props >= 1:
        subject = fallback_subject
        if not subject:
            return []
        prev_label = ""
        for row in body:
            if not row:
                continue
            prop = first_col(row) or prev_label
            if not _is_property_text(prop):
                continue
            prev_label = prop
            for col in value_cols:
                if col >= len(row):
                    continue
                pred, unit = _predicate_for(prop, _condition_from(headers[col]) if headers[col] else None)
                if pred is None:
                    continue
                emit(subject, pred, row[col], unit, row, f"{prop} / {headers[col]}" if headers[col] else prop,
                     [_row_qualifier(prop, pred)], with_header=True)
        return claims

    # ---- Layout C: property rows x subject columns ----------------------------------
    if first_col_props >= max(2, len(body) // 3) and subject_cols:
        prev_label = ""
        dist_method = ""
        for row in body:
            if not row:
                continue
            prop = first_col(row) or prev_label
            if not prop:
                continue
            prev_label = prop
            if is_astm_label(prop) and not is_distillation_point(prop):
                dist_method = re.sub(r"\(.*?\)", "", prop).strip(" :")
                dist_method = re.sub(r"\s+", " ", dist_method)
                if not any(_good_value(row[c]) for c in subject_cols if c < len(row)):
                    continue
            pred, unit = _predicate_for(prop, None)
            if pred is None:
                continue
            for col in subject_cols:
                if col >= len(row):
                    continue
                emit(headers[col], pred, row[col], unit, row, prop,
                     [label, _row_qualifier(prop, pred, dist_method)], with_header=True)
        return claims

    return claims

"""Deterministic table-to-claims mapping.

Engineering tables carry most of a manual's numbers.  Instead of asking the
LLM to read pipe-separated grids, this module maps parsed table cells to
EngineeringClaim objects:

  * Layout B  - subject rows x property columns
        Tag No | Description | Operating Temp (C) | Operating Pressure (Kg/cm2g)
        Product | TBP cut range degC | Temperature degC | Pressure, Kg/cm2 A
  * Layout A  - property rows x condition columns (subject = enclosing heading)
        <blank> | Minimum | Normal | Maximum | Mech. Design
        Pressure (Kg/cm2) | 3.5 | 4.0 | 5.0 | 6.5
  * Layout C  - property rows x subject columns
        Crude | Basrah | Bombay high
        Kg/hr | 367647 | 367647

Evidence is the row's cells joined with " | " (which is exactly how the table
appears in the chunk text), page is the table page, ``is_from_table=True`` and
``table_id`` are set, ``source='table'``.  The unit validator still applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from schemas.claims import ClaimCategory, EngineeringClaim
from schemas.parsed_document import ParsedTable
from src.validator import normalize_unit

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

# (regex on property/header text, predicate, default unit)
_PROPERTY_RULES: list[tuple[re.Pattern, ClaimCategory | None, str | None]] = [
    (re.compile(r"\bpress", re.I), None, None),                 # resolved with condition
    (re.compile(r"\btemp", re.I), None, None),                  # resolved with condition
    (re.compile(r"\b(?:kg\s*/\s*hr?|t\s*/\s*hr?|mt\s*/\s*hr?|tph|kg/h)\b", re.I), ClaimCategory.MASS_FLOW_RATE, "kg/h"),
    (re.compile(r"(?:m\s*3|m³|cu\.?\s*m)\s*/\s*hr?", re.I), ClaimCategory.VOLUMETRIC_FLOW_RATE, "m3/h"),
    (re.compile(r"\bmmtpa\b|\bmtpa\b|\btpa\b", re.I), ClaimCategory.CAPACITY, "MTPA"),
    (re.compile(r"sp\.?\s*gr|specific\s+gravity", re.I), ClaimCategory.SPECIFIC_GRAVITY, ""),
    (re.compile(r"\bdensity\b", re.I), ClaimCategory.DENSITY, "kg/m3"),
    (re.compile(r"\bviscosity\b", re.I), ClaimCategory.VISCOSITY, "cSt"),
    (re.compile(r"pour\s*point", re.I), ClaimCategory.POUR_POINT, "°C"),
    (re.compile(r"flash\s*point", re.I), ClaimCategory.FLASH_POINT, "°C"),
    (re.compile(r"\btbp\b|cut\s*range|boiling\s*range", re.I), ClaimCategory.CUT_RANGE, "°C"),
    (re.compile(r"\byield\b|%\s*cut|cut\s*on", re.I), ClaimCategory.YIELD, "%"),
    (re.compile(r"\bapi\b", re.I), ClaimCategory.GENERIC_PROPERTY, "°API"),
    (re.compile(r"\bpower\b|\bkw\b|\bmw\b", re.I), ClaimCategory.POWER, "kW"),
    (re.compile(r"\bspeed\b|\brpm\b", re.I), ClaimCategory.SPEED, "rpm"),
    (re.compile(r"\bdiameter\b|\bdia\b", re.I), ClaimCategory.DIAMETER, "mm"),
    (re.compile(r"\bcapacity\b|\bduty\b", re.I), ClaimCategory.CAPACITY, ""),
    (re.compile(r"sulfur|sulphur|salt|wax|vanadium|nickel|iron|water|sediment|carbon\s*residue|"
                r"characterization|h2s|rvp|asphaltene|metals|nitrogen|acid", re.I), ClaimCategory.GENERIC_PROPERTY, ""),
    (re.compile(r"\bmm\b|thickness", re.I), ClaimCategory.THICKNESS, "mm"),
]

_UNIT_RE = re.compile(
    r"(kg\s*/\s*cm\s*2\s*[ga]?|kg/cm²\s*[ga]?|bar[ga]?|psi[ga]?|kpa|mpa|mmwc|mmhg|"
    r"°\s*c|deg\.?\s*c|℃|\(c\)|°\s*f|deg\.?\s*f|"
    r"kg\s*/\s*hr?|t\s*/\s*hr?|mt\s*/\s*hr?|tph|m\s*3\s*/\s*hr?|m³\s*/\s*hr?|nm3/hr?|mmtpa|mtpa|tpa|"
    r"wt\s*%|vol\s*%|%|ppm|ppb|ptb|cst|mg/l|kw|mw|rpm|mm|°api)",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"\d")
_SKIP_VALUES = {"", "-", "–", "nil", "na", "n/a", "none", "n.a.", "--"}


@dataclass
class _Header:
    col: int
    text: str
    condition: str | None
    predicate: ClaimCategory | None
    unit: str


_CONDITION_RE = re.compile(r"\s*(?:@|\bat\b)\s*[^,;|]*", re.IGNORECASE)
_VALUE_UNIT_RE = re.compile(r"^\s*(?P<val>[+\-]?\d[\d.,]*(?:\s*[-–/+]\s*\d[\d.,]*)*\+?)\s*(?P<unit>[A-Za-z°%µ][A-Za-z0-9°%/²³.\- ]{0,12})?\s*$")


def split_value_unit(value: str) -> tuple[str, str]:
    """'9.0 cst' -> ('9.0', 'cSt'); '367647' -> ('367647', ''); '7-9' -> ('7-9', '')."""
    m = _VALUE_UNIT_RE.match(value or "")
    if not m:
        return (value or "").strip(), ""
    unit = (m.group("unit") or "").strip()
    return m.group("val").strip(), (normalize_unit(unit) if unit else "")


def _unit_from(text: str) -> str:
    # Drop reference conditions ("Sp. Gravity @ 15 °C", "RVP @ 100 °F, psi") before reading the unit
    text = _CONDITION_RE.sub(" ", text or "")
    m = _UNIT_RE.search(text or "")
    if not m:
        return ""
    u = m.group(1).strip()
    u = re.sub(r"\s+", "", u)
    if u.lower() in ("(c)", "c"):
        u = "°C"
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
    cond = condition or _condition_from(text) or "operating"
    if re.search(r"\bpress", text, re.I):
        return _PRESSURE_BY_COND.get(cond, ClaimCategory.OPERATING_PRESSURE), unit or "kg/cm2"
    if re.search(r"\btemp", text, re.I):
        return _TEMPERATURE_BY_COND.get(cond, ClaimCategory.OPERATING_TEMPERATURE), unit or "°C"
    for rx, pred, default_unit in _PROPERTY_RULES[2:]:
        if rx.search(text):
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


def _good_value(v: str) -> bool:
    v = (v or "").strip()
    return v.lower() not in _SKIP_VALUES and bool(_NUMERIC_RE.search(v)) and len(v) <= 40


def extract_table_claims(
    table: ParsedTable,
    chunk_id: str,
    document_id: str,
    section: str = "",
    subject_hint: str | None = None,
    classification: str | None = None,
) -> list[EngineeringClaim]:
    """Map one parsed table to claims. Returns [] when the layout is not recognised."""
    grid = _grid(table)
    if len(grid) < 2 or table.num_cols < 2:
        return []
    n_hdr = min(_header_rows(table), len(grid) - 1)
    headers: list[str] = []
    for col in range(table.num_cols):
        parts = []
        for r in range(n_hdr):
            t = grid[r][col] if col < len(grid[r]) else ""
            if t and (not parts or parts[-1] != t):
                parts.append(t)
        headers.append(" ".join(parts).strip())
    body = grid[n_hdr:]
    claims: list[EngineeringClaim] = []
    caption_subject = (table.caption or "").strip() or None

    header_line = " | ".join(h for h in headers if h)
    tid_tail = re.sub(r"[^A-Za-z0-9]", "", table.table_id)[-8:]

    def emit(subject: str, predicate: ClaimCategory, value: str, unit: str, row: list[str],
             prop_text: str, with_header: bool = False) -> None:
        subject = subject.strip(" :")
        if not subject or not _good_value(value):
            return
        val, val_unit = split_value_unit(value)
        if val_unit:
            unit = val_unit          # unit written inside the cell wins ("9.0 cst")
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

    # ---- Layout B: a "Tag" column or property headers across the top -------------
    tag_col = next((i for i, h in enumerate(headers) if re.search(r"\btag\b|\bequipment\b", h, re.I)), None)
    prop_cols = [i for i, h in enumerate(headers) if i != (tag_col if tag_col is not None else 0) and _is_property_text(h)]
    first_col_props = sum(1 for r in body if r and _is_property_text(r[0]))
    condition_cols = [i for i, h in enumerate(headers) if i > 0 and _condition_from(h)]

    if prop_cols and (tag_col is not None or first_col_props < len(body) / 2):
        subj_col = tag_col if tag_col is not None else 0
        for row in body:
            if subj_col >= len(row):
                continue
            subject = row[subj_col]
            if not subject or _is_property_text(subject) and tag_col is None and len(row[subj_col]) > 40:
                continue
            for col in prop_cols:
                if col >= len(row):
                    continue
                pred, unit = _predicate_for(headers[col], None)
                if pred is None:
                    continue
                emit(subject, pred, row[col], unit, row, headers[col])
        return claims

    # ---- Layout A: property rows x condition columns (subject = heading/caption) -----
    if condition_cols and first_col_props >= 1:
        subject = subject_hint or caption_subject
        if not subject:
            return []
        for row in body:
            if not row or not _is_property_text(row[0]):
                continue
            for col in condition_cols:
                if col >= len(row):
                    continue
                pred, unit = _predicate_for(row[0], _condition_from(headers[col]))
                if pred is None:
                    continue
                emit(subject, pred, row[col], unit, row, f"{row[0]} / {headers[col]}", with_header=True)
        return claims

    # ---- Layout C: property rows x subject columns ----------------------------------
    subject_cols = [i for i, h in enumerate(headers) if i > 0 and h and not _is_property_text(h)]
    if first_col_props >= max(2, len(body) // 3) and subject_cols:
        for row in body:
            if not row or not _is_property_text(row[0]):
                continue
            pred, unit = _predicate_for(row[0], None)
            if pred is None:
                continue
            for col in subject_cols:
                if col >= len(row):
                    continue
                emit(headers[col], pred, row[col], unit, row, row[0], with_header=True)
        return claims

    return claims

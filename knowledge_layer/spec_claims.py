"""Deterministic claims from specification blocks and prose sentences.

Equipment chapters state operating data as a two-column block that the layout
parser emits as separate short lines, labels first, values second::

    Normal flow rate            482 m3/hr
    Minimum flow rate     ->    219 m3/hr        (parsed as 5 label lines then 5 value lines)
    Suction Pressure            2.0 kg/cm2 A
    Discharge Pressure          24.45 kg/cm2 A
    Differential Head           367.3 meters

and prose sentences state design/rated values::

    The rated capacity of the pumps is 482 m3/h. However, the pump can be operated
    at a design limit of 520 m3/h.
    CDU / VDU-II have a design capacity to process 3.0 MMTPA of crude oil. The crude
    processing capacity was enhanced to 3.2 MMTPA by installing Pre-Flash Drum in 1996.

This module maps both to :class:`EngineeringClaim` objects with the *context
dimensions* filled in (parameter_role, location, pressure_basis, temporal
status), so "normal 482" / "minimum 219" / "design limit 520" and "design 3.0" /
"enhanced 3.2" are different facts rather than contradictions.  Everything is
string rules; confidence 0.75; ``source='rule'``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from knowledge_layer.schemas.claims import ClaimCategory, EngineeringClaim, leading_number
from knowledge_layer.schemas.normalized_document import ContentType, NormalizedElement
from knowledge_layer.entity_identity import TAG_SCAN_RE, parse_tag
from knowledge_layer.schemas.claims import UnitFamily
from knowledge_layer.table_claims import _condition_from, _predicate_for, basis_from_text, clean_value
from knowledge_layer.table_context import clean_label
from knowledge_layer.validator import _classify_unit, normalize_unit, pressure_basis

_GENERIC_HEADINGS = {
    "description", "operating conditions", "operating condition", "mechanical design", "material of construction",
    "general", "introduction", "design data", "design conditions", "specification", "specifications",
    "operation procedure", "operating procedure", "procedure", "notes", "note", "details", "data",
}
_GENERIC_EQUIPMENT_NOUNS = re.compile(
    r"^(?:the\s+)?(?:pumps?|columns?|heaters?|furnaces?|vessels?|drums?|exchangers?|units?|systems?|ejectors?|"
    r"compressors?|towers?|equipments?|motors?|turbines?|desalters?|strippers?|condensers?|coolers?|reboilers?)$",
    re.I,
)
_INSTRUMENT_PREFIX_RE = re.compile(r"^[PTFLAHZ][A-Z]{1,3}$")

SPEC_CONFIDENCE = 0.8
PROSE_CONFIDENCE = 0.7

_VALUE_LINE_RE = re.compile(
    r"^\s*[<>~≤≥]?\s*[+\-]?\d[\d.,]*(?:\s*[-–/]\s*\d[\d.,]*)?\s*"
    r"(?:[A-Za-z°%µ][A-Za-z0-9°%/²³.()\- ]{0,20})?\s*$"
)
_LABEL_WORDS_RE = re.compile(
    r"pressure|temp|flow|head|npsh|capacity|power|speed|viscosity|gravity|density|rate|duty|level|"
    r"efficiency|rpm|voltage|current|diameter|length|weight|volume|thickness|area|height|set\s*point|"
    r"range|limit|load|frequency|rating|size|point|content|consumption|dosing|quantity", re.I,
)
_INLINE_PAIR_RE = re.compile(r"^\s*(?P<label>[A-Za-z][^:|=]{2,60}?)\s*[:=]\s*(?P<value>[<>~]?\s*[+\-]?\d[\d.,]*.{0,25})$")
_LOCATION_RE = re.compile(
    r"\b(suction|discharge|inlet|outlet|top|bottom|overhead|flash\s*zone|shell|tube|reflux|stripping|"
    r"skin|arch|stack|pumparound|draw|casing|bearing|seal)\b", re.I,
)
# Words that qualify a value rather than name the thing it is about ("maintain positive pressure",
# "raise the transfer temperature"): they become part of the label, the subject comes from context.
_QUALIFIER_WORDS = re.compile(
    r"^(?:positive|negative|slight|small|high|low|back|steady|constant|stable|required|desired|normal|"
    r"transfer|coil|outlet|inlet|system|the system|unit|process|operating|design|rated|minimum|maximum|"
    r"total|overall|initial|final|fuel gas|steam|vacuum|column top|column bottom)$", re.I,
)
_ROLE_WORDS = {
    "design": "design", "rated": "rated", "normal": "normal", "operating": "operating", "minimum": "minimum",
    "min": "minimum", "maximum": "maximum", "max": "maximum", "mechanical design": "mechanical_design",
    "test": "test", "relief": "relief", "set": "relief", "alarm": "alarm", "trip": "trip",
    "available": "", "required": "required", "guaranteed": "rated", "actual": "operating",
}
_MODE_BY_SECTION = [
    (re.compile(r"emergency", re.I), "emergency"),
    (re.compile(r"start\s*-?\s*up|commission", re.I), "startup"),
    (re.compile(r"shut\s*-?\s*down", re.I), "shutdown"),
    (re.compile(r"temporary", re.I), "temporary"),
    (re.compile(r"upset|deviation", re.I), "upset"),
    (re.compile(r"normal operation", re.I), "normal_operation"),
]
_SCENARIO_RE = re.compile(
    r"\b(basrah|bombay high|\bbh\b|\bpg\b|kuwait|kirkuk|arabian|arab (?:light|heavy)|sko|atf|design case|check case|"
    r"bh mode|pg mode)\b", re.I,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _is_value_line(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t) <= 40 and bool(_VALUE_LINE_RE.match(t))


def _is_label_line(text: str) -> bool:
    t = (text or "").strip().rstrip(":")
    if not t or len(t) > 60 or " | " in t or not t[0].isalpha() or t.endswith("."):
        return False
    if re.search(r"\d", t) and not re.search(r"npsh|\bph\b|h2s|so2|co2|nox", t, re.I):
        return False
    return bool(_LABEL_WORDS_RE.search(t)) and len(t.split()) <= 7


def _role_from(label: str) -> str:
    cond = _condition_from(label)
    if cond:
        return {"design": "design", "minimum": "minimum", "normal": "normal", "maximum": "maximum",
                "operating": "operating", "test": "test", "relief": "relief"}.get(cond, cond)
    t = (label or "").lower()
    for word, role in _ROLE_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", t):
            return role
    return ""


def _location_from(text: str) -> str:
    m = _LOCATION_RE.search(text or "")
    return re.sub(r"\s+", "_", m.group(1).lower()) if m else ""


def operating_mode_for(section_path: str) -> str:
    for rx, mode in _MODE_BY_SECTION:
        if rx.search(section_path or ""):
            return mode
    return ""


def scenario_for(text: str) -> str:
    m = _SCENARIO_RE.search(text or "")
    if not m:
        return ""
    s = m.group(1)
    return {"bh": "BH mode", "pg": "PG mode"}.get(s.lower(), s.strip())


def subject_from_headings(section_path: str, parent_heading: str = "") -> str:
    """The asset the section is about: the tag in the nearest heading, else the heading itself."""
    candidates = [parent_heading] + [p for p in reversed((section_path or "").split(" > "))]
    for cand in candidates:
        if not cand:
            continue
        m = TAG_SCAN_RE.search(cand)
        if m and parse_tag(m.group(0)):
            return re.sub(r"\s+", "", m.group(0)).replace("-", "-")
    for cand in candidates:
        c = clean_label(cand or "")
        if not c or re.match(r"^chapter\s+\d+", c, re.I) or len(c) > 80:
            continue
        if c.lower().strip(" :") in _GENERIC_HEADINGS:
            continue
        return c
    return ""


def _predicate_from_unit(unit: str, role: str) -> ClaimCategory | None:
    """Fallback when the label has no property word ('design limit of 520 m3/h')."""
    fam = _classify_unit(unit)
    if fam == UnitFamily.PRESSURE:
        return {"design": ClaimCategory.DESIGN_PRESSURE, "maximum": ClaimCategory.MAXIMUM_PRESSURE,
                "minimum": ClaimCategory.MINIMUM_PRESSURE, "normal": ClaimCategory.NORMAL_PRESSURE}.get(
            role, ClaimCategory.OPERATING_PRESSURE)
    if fam == UnitFamily.TEMPERATURE:
        return {"design": ClaimCategory.DESIGN_TEMPERATURE, "maximum": ClaimCategory.MAXIMUM_TEMPERATURE,
                "minimum": ClaimCategory.MINIMUM_TEMPERATURE, "normal": ClaimCategory.NORMAL_TEMPERATURE}.get(
            role, ClaimCategory.OPERATING_TEMPERATURE)
    if fam == UnitFamily.VOLUMETRIC_FLOW:
        return ClaimCategory.VOLUMETRIC_FLOW_RATE
    if fam == UnitFamily.MASS_FLOW:
        return ClaimCategory.CAPACITY if unit.upper() in ("MMTPA", "TMTPA", "MTPA", "TPA") else ClaimCategory.MASS_FLOW_RATE
    if fam == UnitFamily.POWER:
        return ClaimCategory.POWER
    if fam == UnitFamily.SPEED:
        return ClaimCategory.SPEED
    return None


def _predicate_and_unit(label: str) -> tuple[ClaimCategory, str, str]:
    """(predicate, default unit, qualifier) for a spec label."""
    pred, unit = _predicate_for(label, None)
    if pred is None:
        low = label.lower()
        if re.search(r"\bhead\b", low):
            return ClaimCategory.GENERIC_PROPERTY, "m", "Differential head" if "diff" in low else "Head"
        if "npsh" in low:
            return ClaimCategory.GENERIC_PROPERTY, "m", label.strip(" :")
        if re.search(r"\bset\s*point\b|\bsetpoint\b", low):
            return ClaimCategory.SETPOINT, unit, ""
        if re.search(r"\balarm\b", low):
            return ClaimCategory.ALARM_LIMIT, unit, ""
        if re.search(r"\btrip\b", low):
            return ClaimCategory.TRIP_LIMIT, unit, ""
        if re.search(r"\blevel\b", low):
            return ClaimCategory.GENERIC_PROPERTY, unit or "%", label.strip(" :")
        return ClaimCategory.GENERIC_PROPERTY, unit, label.strip(" :")
    qualifier = label.strip(" :") if pred == ClaimCategory.GENERIC_PROPERTY else ""
    return pred, unit, qualifier


def _make_claim(
    claim_id: str, subject: str, label: str, raw_value: str, evidence: str, page: int, section: str,
    document_id: str, chunk_id: str, confidence: float, role_hint: str = "", temporal: str = "current",
    qualifier_extra: str = "", predicate_override: ClaimCategory | None = None,
) -> EngineeringClaim | None:
    val, val_unit, cond = clean_value(raw_value)
    if not val or leading_number(val) is None:
        return None
    pred, default_unit, qualifier = _predicate_and_unit(label)
    if predicate_override is not None:
        pred = predicate_override
    unit_raw = val_unit or default_unit or ""
    unit = normalize_unit(unit_raw)
    role = role_hint or _role_from(label)
    if pred == ClaimCategory.GENERIC_PROPERTY and not re.search(r"head|npsh|level|\bph\b", label, re.I):
        by_unit = _predicate_from_unit(unit, role)
        if by_unit is not None:
            pred, qualifier = by_unit, ""
    fam = _classify_unit(unit)
    if pred == ClaimCategory.CAPACITY and re.search(r"flow", label, re.I) and fam in (
            UnitFamily.VOLUMETRIC_FLOW, UnitFamily.MASS_FLOW):
        pred = ClaimCategory.VOLUMETRIC_FLOW_RATE if fam == UnitFamily.VOLUMETRIC_FLOW else ClaimCategory.MASS_FLOW_RATE
    if pred == ClaimCategory.GENERIC_PROPERTY and unit.lower() in ("m", "meters") and re.search(r"head|npsh", label, re.I):
        unit = "m"
    basis = basis_from_text(raw_value) or basis_from_text(label) or pressure_basis(unit_raw)
    parts = [q for q in (qualifier, qualifier_extra, cond) if q]
    return EngineeringClaim(
        claim_id=claim_id,
        subject=subject,
        predicate=pred,
        predicate_raw=f"spec:{label.strip()[:60]}",
        value=val,
        unit=unit,
        unit_normalized=unit,
        qualifier=" / ".join(dict.fromkeys(parts)),
        parameter_role=role,
        location=_location_from(label),
        operating_mode=operating_mode_for(section),
        scenario=scenario_for(label) or scenario_for(section),
        pressure_basis=basis,
        temporal_status=temporal,
        value_numeric=leading_number(val),
        evidence=evidence,
        page=page,
        section=section,
        document_id=document_id,
        chunk_id=chunk_id,
        confidence=confidence,
        source="rule",
    )


# --------------------------------------------------------------------------- #
# Specification blocks
# --------------------------------------------------------------------------- #
@dataclass
class SpecPair:
    label: str
    value: str
    page: int
    evidence: str
    element_ids: tuple[str, ...]


def detect_spec_pairs(elements: list[NormalizedElement]) -> list[SpecPair]:
    """Label/value pairs from vertical blocks (labels then values), alternating lines or 'Label: value'."""
    pairs: list[SpecPair] = []
    run: list[NormalizedElement] = []

    def pair_up(labels: list[NormalizedElement], values: list[NormalizedElement]) -> None:
        """A group of label lines followed by a group of value lines: pair in order."""
        if not labels or not values:
            return
        if len(labels) == len(values):
            for lab, val in zip(labels, values):
                pairs.append(SpecPair(lab.content.strip(), val.content.strip(), val.page,
                                      f"{lab.content.strip()} | {val.content.strip()}",
                                      (lab.element_id, val.element_id)))
        elif len(values) == 1:
            lab, val = labels[-1], values[0]
            pairs.append(SpecPair(lab.content.strip(), val.content.strip(), val.page,
                                  f"{lab.content.strip()} | {val.content.strip()}",
                                  (lab.element_id, val.element_id)))

    def flush() -> None:
        """Split the run into (labels..., values...) segments and pair each segment."""
        nonlocal run
        if len(run) >= 2:
            labels: list[NormalizedElement] = []
            values: list[NormalizedElement] = []
            for e in run:
                if _is_value_line(e.content):
                    values.append(e)
                else:
                    if values:                    # a label after values closes the segment
                        pair_up(labels, values)
                        labels, values = [], []
                    labels.append(e)
            pair_up(labels, values)
        run = []

    for e in elements:
        text = (e.content or "").strip()
        if e.content_type in (ContentType.PARAGRAPH, ContentType.LIST_ITEM, ContentType.NOTE) and len(text) <= 60:
            m = _INLINE_PAIR_RE.match(text)
            if m and _is_label_line(m.group("label")) and _is_value_line(m.group("value")):
                flush()
                pairs.append(SpecPair(m.group("label").strip(), m.group("value").strip(), e.page, text, (e.element_id,)))
                continue
            if _is_label_line(text) or _is_value_line(text):
                run.append(e)
                continue
        flush()
    flush()
    return pairs


def extract_spec_claims(
    elements: list[NormalizedElement],
    chunk_id: str,
    document_id: str,
    section_path: str,
    parent_heading: str = "",
) -> list[EngineeringClaim]:
    """Claims from label/value specification blocks in a chunk."""
    pairs = detect_spec_pairs(elements)
    if not pairs:
        return []
    subject = subject_from_headings(section_path, parent_heading)
    if not subject:
        return []
    claims: list[EngineeringClaim] = []
    for pair in pairs:
        claim = _make_claim(
            f"{chunk_id}_s{len(claims)}", subject, pair.label, pair.value, pair.evidence, pair.page,
            section_path, document_id, chunk_id, SPEC_CONFIDENCE,
        )
        if claim is not None:
            claims.append(claim)
    return claims


# --------------------------------------------------------------------------- #
# Prose sentences
# --------------------------------------------------------------------------- #
_NUM = r"(?P<val>\d[\d,]*(?:\.\d+)?)"
_UNIT = r"(?P<unit>(?:kg\s*/\s*cm\s*2\s*[ga]?|kg/cm²\s*[ga]?|bar[ga]?|psi[ga]?|kpa|mpa|mmwc|mmhg|"\
        r"°\s*c|deg\.?\s*c|℃|m\s*3\s*/\s*hr?|m³\s*/\s*hr?|kg\s*/\s*hr?|t\s*/\s*hr?|mt\s*/\s*hr?|tph|"\
        r"mmtpa|tmtpa|mtpa|tpa|nm3\s*/\s*hr?|lph|kw|mw|rpm|%|ppm|cst|\bm\b|meters?|mm))"
_PROP = r"(?P<prop>capacity|flow(?:\s*rate)?|pressure|temperature|limit|throughput|duty|speed|power|level|rate)"
_ROLE = r"(?P<role>rated|design|normal|maximum|minimum|operating|enhanced|installed|guaranteed)"

_PROSE_PATTERNS: list[re.Pattern] = [
    # "The rated capacity of the pumps is 482 m3/h", "design pressure of P-101 is 15 barg"
    re.compile(rf"\b{_ROLE}\s+{_PROP}\s+(?:of\s+(?P<subj>(?:the\s+)?[^,.;]{{2,50}}?)\s+)?(?:is|are|was|were|=|:)\s+(?:about\s+|approximately\s+|approx\.?\s+)?{_NUM}\s*{_UNIT}", re.I),
    # "operated at a design limit of 520 m3/h", "with a maximum discharge pressure of 15 kg/cm2 g"
    re.compile(rf"\b(?:at|with|to)\s+a\s+{_ROLE}\s+(?P<loc>suction|discharge|inlet|outlet)?\s*{_PROP}\s+of\s+{_NUM}\s*{_UNIT}", re.I),
    # "design capacity to process 3.0 MMTPA", "capacity of 3.0 MMTPA"
    re.compile(rf"\b{_ROLE}\s+{_PROP}\s+(?:to\s+process\s+|of\s+)(?:about\s+)?{_NUM}\s*{_UNIT}", re.I),
    # "capacity was enhanced to 3.2 MMTPA"
    re.compile(rf"\b{_PROP}\s+(?:was|is|has been)\s+(?P<role>enhanced|increased|augmented|revamped|reduced|derated)\s+to\s+{_NUM}\s*{_UNIT}", re.I),
    # "raise the feed crude pressure to 24 kg/cm2 g", "maintain column pressure at 1.0 kg/cm2"
    re.compile(rf"\b(?:raise|raising|reduce|maintain|maintaining|control|controlling|keep|keeping|bring)\s+(?:the\s+)?(?P<subj>[^,.;]{{2,40}}?)\s+{_PROP}\s+(?:to|at|of)\s+(?:about\s+)?{_NUM}\s*{_UNIT}", re.I),
]
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(\[])|\n+")


def extract_prose_claims(
    chunk_text: str,
    chunk_id: str,
    document_id: str,
    section_path: str,
    page: int,
    parent_heading: str = "",
) -> list[EngineeringClaim]:
    """Numeric design/rated/operating statements in prose sentences."""
    claims: list[EngineeringClaim] = []
    seen: set[tuple[str, str, str, str]] = set()
    section_subject = subject_from_headings(section_path, parent_heading)
    last_equipment_tag = ""          # most recent non-instrument tag seen in this chunk
    for sent in _SENT_SPLIT_RE.split(chunk_text or ""):
        sent = sent.strip()
        if not sent or " | " in sent or sent.startswith("#") or len(sent) > 600:
            continue
        sent_tags = [t.group(0) for t in TAG_SCAN_RE.finditer(sent) if parse_tag(t.group(0))]
        equipment_tags = [
            t for t in sent_tags
            if parse_tag(t).prefix == "PM" or not _INSTRUMENT_PREFIX_RE.match(parse_tag(t).prefix)
        ]
        if equipment_tags:
            last_equipment_tag = re.sub(r"\s+", "", equipment_tags[0])
        for rx in _PROSE_PATTERNS:
            for m in rx.finditer(sent):
                gd = m.groupdict()
                prop = re.sub(r"\s+", " ", gd.get("prop", "")).lower()
                role_word = (gd.get("role") or "").lower()
                role = {"enhanced": "rated", "increased": "rated", "augmented": "rated", "revamped": "rated",
                        "installed": "rated", "guaranteed": "rated", "reduced": "rated", "derated": "rated"}.get(
                    role_word, _ROLE_WORDS.get(role_word, role_word))
                qualifier_extra = role_word if role_word in ("enhanced", "increased", "augmented", "revamped", "reduced", "derated") else ""
                temporal = "current" if qualifier_extra else ("design" if role == "design" else "current")
                # subject: equipment tag in the sentence; a generic noun ("the pump") inherits the last
                # equipment tag of the chunk; otherwise the sentence's own subject phrase, else the section's asset
                subj_phrase = re.sub(r"^(?:the|a|an)\s+", "", (gd.get("subj") or "").strip(), flags=re.I)
                section_is_tag = bool(section_subject) and parse_tag(section_subject) is not None
                qualifier_word = ""
                if subj_phrase and _QUALIFIER_WORDS.match(subj_phrase):
                    qualifier_word, subj_phrase = subj_phrase.lower(), ""
                if equipment_tags:
                    subject = re.sub(r"\s+", "", equipment_tags[0])
                elif not subj_phrase and qualifier_word:
                    subject = last_equipment_tag or section_subject
                elif subj_phrase and _GENERIC_EQUIPMENT_NOUNS.match(subj_phrase):
                    # "the pump" / "column": the tag named just before, else the section's tagged asset,
                    # else the noun itself (never an unrelated section title)
                    subject = last_equipment_tag or (section_subject if section_is_tag else subj_phrase.lower())
                elif subj_phrase and 1 <= len(subj_phrase.split()) <= 6:
                    subject = clean_label(subj_phrase)
                else:
                    subject = last_equipment_tag or section_subject
                if not subject or len(subject) < 2:
                    continue
                label = f"{role_word} {prop}".strip() if role_word else prop
                if gd.get("loc"):
                    label = f"{gd['loc']} {label}"
                elif subj_phrase and _LOCATION_RE.search(subj_phrase):
                    label = f"{_LOCATION_RE.search(subj_phrase).group(1)} {label}"
                if qualifier_word:
                    label = f"{qualifier_word} {label}"
                    qualifier_extra = " / ".join(q for q in (qualifier_extra, qualifier_word) if q)
                key = (subject.lower(), label.lower(), gd["val"], gd["unit"].lower())
                if key in seen:
                    continue
                seen.add(key)
                claim = _make_claim(
                    f"{chunk_id}_p{len(claims)}", subject, label, f"{gd['val']} {gd['unit']}", sent, page,
                    section_path, document_id, chunk_id, PROSE_CONFIDENCE, role_hint=role, temporal=temporal,
                    qualifier_extra=qualifier_extra,
                )
                if claim is not None:
                    claims.append(claim)
    return claims

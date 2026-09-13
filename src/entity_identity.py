"""Canonical identity for refinery assets.

An equipment/instrument tag such as ``11-V-02`` names one real-world asset no
matter which manual mentions it.  This module turns the many spellings that
appear in documents ("11-V-02", "11 V 02", "11-v-02", "10-P-01A/B", "E-201A",
"TIC-1001") into one canonical key and derives a *global* entity UID from it
(no document component).  Entities without a recognisable tag keep a
document-scoped UID until the resolver links them.

Tag grammar (all separators unified to "-", case-insensitive)::

    [<plant>-]<PREFIX>-<number>[<suffix>]

    plant   1-3 digits (unit / plant number, e.g. 10, 11, 12)
    PREFIX  1-4 letters (P, V, E, C, CP, TIC, FCV, ...)
    number  1-5 digits
    suffix  A, B, ... optionally as a train list "A/B", "A/B/C", "A&B", "A,B"

"A/B" style suffixes denote parallel trains: the canonical entity is the
parent tag (``10-P-01``) with children ``10-P-01A`` and ``10-P-01B`` linked by
HAS_TRAIN.  A single-letter suffix (``E-201A``) is a child of ``E-201``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# Letter groups that look like tag prefixes but are standards, labels or
# document furniture.  A match with one of these is never a tag.
NON_TAG_PREFIXES: frozenset[str] = frozenset({
    "ISO", "API", "ASTM", "ASME", "ANSI", "IS", "BS", "EN", "DIN", "NFPA", "OISD", "IBR", "IEC",
    "SOP", "DOC", "REV", "PAGE", "PG", "CH", "SEC", "FIG", "TAB", "NO", "STEP", "PART", "VOL",
    "ITEM", "SL", "SR", "REF", "NOTE", "TEL", "PH", "FAX", "EXT", "ROOM", "PO", "WO", "PTW",
    "MOC", "PSM", "HSE", "PPM", "RPM", "PSI", "KPA", "MPA", "BAR", "KG", "KW", "MW", "HP", "AM",
})
# NOTE: "PM" (pump-motor, 11-PM-01A/B) and "PG" (pressure gauge, 11-PG-103) are real prefixes in this
# corpus; a match that carries a plant number ("11-...") is never checked against NON_TAG_PREFIXES.

# Default scan/validation patterns (configurable via PipelineConfig.identity.tag_patterns).
DEFAULT_TAG_PATTERNS: tuple[str, ...] = (
    r"^(?:(?P<plant>\d{1,3})-)?(?P<prefix>[A-Z]{1,4})-(?P<number>\d{1,5})(?P<suffix>[A-Z](?:/[A-Z])*)?$",
)

# Loose pattern for scanning free text for candidate tags.
TAG_SCAN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{1,3}\s*[-–—_]\s*)?[A-Za-z]{1,4}\s*[-–—_]\s*\d{1,5}"
    r"(?:\s*[A-Za-z](?:\s*[/&,]\s*[A-Za-z])*)?(?![A-Za-z0-9])"
)

_DASHES = "–—−‐‑‒_"


@dataclass
class TagIdentity:
    """Canonical identity derived from an equipment/instrument tag."""
    canonical: str                      # e.g. "10-P-01" (parent for train lists) or "E-201A"
    plant: str | None                   # "10" or None
    prefix: str                         # "P"
    number: str                         # "01"
    suffixes: list[str] = field(default_factory=list)   # ["A", "B"]
    parent: str | None = None           # "E-201" for "E-201A"; None when no suffix
    children: list[str] = field(default_factory=list)   # ["10-P-01A", "10-P-01B"] for "A/B"
    scope: str | None = None            # unit slug used when the tag had no plant prefix

    @property
    def is_train_list(self) -> bool:
        return len(self.suffixes) > 1


def _clean(name: str) -> str:
    text = (name or "").strip().upper()
    for d in _DASHES:
        text = text.replace(d, "-")
    text = re.sub(r"\s*-\s*", "-", text)          # spaces around dashes
    text = re.sub(r"\s*/\s*", "/", text)          # spaces around slashes
    text = re.sub(r"\s*[&,]\s*", "/", text)       # A&B, A,B -> A/B
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .,:;()[]{}\"'")
    # "12-P-04 A/B" / "E 201 A" -> suffix letters attach to the number
    text = re.sub(r"(\d) ([A-Z](?:/[A-Z])*)$", r"\1\2", text)
    return text


def _unit_slug(unit: str | None) -> str | None:
    if not unit:
        return None
    slug = re.sub(r"[^A-Z0-9]+", "-", unit.upper()).strip("-")
    return slug or None


def parse_tag(
    name: str,
    patterns: tuple[str, ...] | list[str] = DEFAULT_TAG_PATTERNS,
) -> TagIdentity | None:
    """Parse a tag string into its parts. Returns None when it is not a tag."""
    text = _clean(name)
    if not text or len(text) > 32:
        return None
    # Space-separated variants ("11 V 02", "P 101") -> dashed
    dashed = re.sub(r"(?<=[A-Z0-9]) (?=[A-Z0-9])", "-", text)
    for pat in patterns:
        m = re.match(pat, dashed)
        if not m:
            continue
        prefix = m.group("prefix")
        plant = m.group("plant")
        if prefix in NON_TAG_PREFIXES and not plant:
            return None
        number = m.group("number")
        suffix = m.group("suffix") or ""
        suffixes = [s for s in suffix.split("/") if s]
        base = f"{plant}-{prefix}-{number}" if plant else f"{prefix}-{number}"
        if len(suffixes) > 1:
            canonical, parent = base, None
            children = [f"{base}{s}" for s in suffixes]
        elif len(suffixes) == 1:
            canonical, parent, children = f"{base}{suffixes[0]}", base, []
        else:
            canonical, parent, children = base, None, []
        return TagIdentity(
            canonical=canonical, plant=plant, prefix=prefix, number=number,
            suffixes=suffixes, parent=parent, children=children,
        )
    return None


def normalize_tag(
    name: str,
    plant: str | None = None,
    unit: str | None = None,
    unit_scoping: bool = True,
    patterns: tuple[str, ...] | list[str] = DEFAULT_TAG_PATTERNS,
) -> str | None:
    """Return the canonical tag key for ``name`` or None if it is not a tag.

    ``plant``/``unit`` come from the document profile.  When the tag carries no
    plant-number prefix and ``unit_scoping`` is on, the unit slug is prefixed
    ("CDU-II/P-101") so identically numbered tags in different units stay apart.
    """
    ident = identify_tag(name, plant=plant, unit=unit, unit_scoping=unit_scoping, patterns=patterns)
    return ident.canonical if ident else None


def identify_tag(
    name: str,
    plant: str | None = None,
    unit: str | None = None,
    unit_scoping: bool = True,
    patterns: tuple[str, ...] | list[str] = DEFAULT_TAG_PATTERNS,
) -> TagIdentity | None:
    """Like :func:`normalize_tag` but returns the full :class:`TagIdentity`."""
    ident = parse_tag(name, patterns)
    if ident is None:
        return None
    if ident.plant is None and unit_scoping:
        scope = _unit_slug(unit) or _unit_slug(plant)
        if scope:
            ident.scope = scope
            ident.canonical = f"{scope}/{ident.canonical}"
            if ident.parent:
                ident.parent = f"{scope}/{ident.parent}"
            ident.children = [f"{scope}/{c}" for c in ident.children]
    return ident


def entity_uid(canonical_tag: str) -> str:
    """Global UID for a canonical tag: md5 of the tag only (no document part)."""
    return hashlib.md5(canonical_tag.strip().lower().encode()).hexdigest()[:16]


def document_scoped_uid(name: str, document_id: str) -> str:
    """UID for an entity that has no recognised tag (per document until resolved)."""
    key = f"{name.strip().lower()}|{document_id}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def find_tags(text: str) -> list[str]:
    """Scan free text for candidate tags; returns canonical (unscoped) tag keys."""
    found: list[str] = []
    seen: set[str] = set()
    for m in TAG_SCAN_RE.finditer(text or ""):
        ident = parse_tag(m.group(0))
        if ident and ident.canonical not in seen:
            seen.add(ident.canonical)
            found.append(ident.canonical)
    return found

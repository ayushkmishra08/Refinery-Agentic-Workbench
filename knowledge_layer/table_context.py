"""Table context: which label a table sits under, and page-split continuation links.

Operating manuals put the meaning of a table in the text *around* it, not in it:

    Heavy Naphtha (HN) Product:                     <- label (Docling: list item, not heading)
    Property | Basrah | Bombay High                 <- table: crude columns, property rows
    ...
    ASTM D-86 (VOL %) | Temperature | Temperature   <- continuation of the table above,
    IBP | 213.5 | 175                                  split by a page break

Without that context every cut's gravity/viscosity lands on the bare subject
"Basrah" and reads as a contradiction.  This module walks the normalized
elements in reading order and derives, for every table:

  * label            nearest specific label: a heading, or a short list item /
                     paragraph ("Kerosene Product:", "Crude Distillation Column
                     Material Balance for Kuwait crude SKO operation") that precedes
                     the table.  When several labels queue up before several tables
                     (Docling groups consecutive labels into one list), labels are
                     handed to the tables first-in-first-out.
  * heading          the enclosing numbered section heading; for enumerated
                     sub-labels ("i.) SKO operation") it is prepended to the label.
  * continuation_of  the previous table with a real header row when this table
                     has none (its first row is data, or ASTM/distillation points).

Everything here is deterministic and cheap (string rules only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from knowledge_layer.schemas.normalized_document import ContentType, NormalizedDocument
from knowledge_layer.schemas.parsed_document import ParsedTable

MAX_LABEL_CHARS = 120
MAX_LABEL_WORDS = 18

_FOOTNOTE_RE = re.compile(r"^\s*[*†‡]+")
_FIGURE_RE = re.compile(r"^\s*\[(?:figure|image|picture|table)\b", re.I)
_NUMBERED_RE = re.compile(r"^\s*\d+(?:\.\d+)+\.?\s+\S")
_ENUM_RE = re.compile(r"^\s*\(?(?:[ivx]{1,4}|[a-h]|\d{1,2})\s*[.)]\)?\s+\S", re.I)
_LEAD_NUMBER_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|\(?(?:[ivx]{1,4}|[a-h]|\d{1,2})\s*[.)]\)?)\s+", re.I)
_AS_PER_RE = re.compile(r"\(\s*as\s+per[^)]*\)", re.I)
_DIST_POINT_RE = re.compile(
    r"^\s*(?:IBP|FBP|EP|\d{1,3}(?:\.\d+)?\s*%(?:\s*(?:vol|volume|recovered|distillate|dist\.?|evaporated|off))?)\s*[:.]?\s*$",
    re.I,
)
_ASTM_RE = re.compile(r"\bASTM\b|\bD\s*-?\s*86\b|\bD\s*-?\s*1160\b|\bdistillation\b", re.I)
_NUMERIC_LIKE_RE = re.compile(
    r"^\s*[<>≤≥~±+\-*]*\s*\d[\d.,]*(?:\s*[-–/+]\s*\d[\d.,]*)*\s*[A-Za-z°%µ/²³. ]{0,10}\s*$"
)
_STOPWORDS = {
    "of", "for", "and", "the", "at", "in", "on", "to", "with", "without", "a", "an", "by", "from",
    "or", "per", "vs", "&", "/", "-", "crude", "operation", "case", "mode", "production", "feed",
}


_SCENARIO_RE = re.compile(
    r"\b(basrah|bombay\s+high|kuwait|kirkuk|arabian|arab\s+(?:light|heavy)|sko|atf|design\s+case|check\s+case|"
    r"bh\s+mode|pg\s+mode|bh\s+case|pg\s+case)\b|\b(bh|pg)\b(?=\s+(?:mode|case|operation|crude))", re.I,
)


def scenario_for(*texts: str) -> str:
    """Operating scenario named in a label / heading / caption: 'BH Mode Atmos-side Exchangers' -> 'BH mode'."""
    for t in texts:
        m = _SCENARIO_RE.search(t or "")
        if m:
            s = (m.group(1) or m.group(2) or "").strip()
            low = s.lower()
            if low in ("bh", "bh mode", "bh case"):
                return "BH mode"
            if low in ("pg", "pg mode", "pg case"):
                return "PG mode"
            return re.sub(r"\s+", " ", s)
    return ""


@dataclass
class TableContext:
    table_id: str
    label: str = ""                     # cleaned label, e.g. "Heavy Naphtha (HN) Product"
    heading: str = ""                   # cleaned enclosing numbered heading
    section_path: str = ""
    page: int = 0
    continuation_of: str | None = None  # table_id of the table whose header row this one continues
    raw_label: str = ""                 # label text exactly as parsed
    scenario: str = ""                  # operating case in force for this table ("BH mode", "Basrah", ...)

    @property
    def qualifier_label(self) -> str:
        """Label used inside claim qualifiers (label, else heading)."""
        return self.label or self.heading


def clean_label(text: str) -> str:
    """'3.3.1.2 Naphtha Stabilizer Material Balance for Basrah crude:' -> 'Naphtha Stabilizer ... crude'."""
    t = (text or "").strip()
    t = _AS_PER_RE.sub("", t)
    t = _LEAD_NUMBER_RE.sub("", t)
    t = re.sub(r"\s*:\s*\(", " (", t)            # "Diesel (DSL): (during ATF regulation)", "FCCU FEED : (LVGO..."
    t = re.sub(r"\s+", " ", t).strip(" :;-–\t")
    return t


def is_distillation_point(text: str) -> bool:
    return bool(_DIST_POINT_RE.match(text or ""))


def is_astm_label(text: str) -> bool:
    return bool(_ASTM_RE.search(text or ""))


def is_numeric_like(text: str) -> bool:
    return bool(_NUMERIC_LIKE_RE.match(text or ""))


def is_label_text(text: str, content_type: ContentType | None = None) -> bool:
    """Short title-like text that names what the following table is about."""
    t = (text or "").strip()
    if not t or " | " in t or _FOOTNOTE_RE.match(t) or _FIGURE_RE.match(t):
        return False
    if len(t) > MAX_LABEL_CHARS or len(t.split()) > MAX_LABEL_WORDS:
        return False
    if content_type == ContentType.HEADING:
        return True
    if t.endswith(":") or _NUMBERED_RE.match(t) or _ENUM_RE.match(t):
        return True
    if t.endswith(".") or t[0].islower():
        return False
    words = re.findall(r"[A-Za-z0-9][\w\-/&.]*", t)      # drop punctuation-only tokens, leading brackets
    significant = [w for w in words if w.lower() not in _STOPWORDS]
    if not significant:
        return False
    capitalised = sum(1 for w in significant if w[0].isupper() or w[0].isdigit())
    return capitalised / len(significant) >= 0.5


def has_header_row(table: ParsedTable) -> bool:
    """True when the first grid row looks like column headers (text, not data)."""
    grid = table.grid or []
    if not grid:
        return False
    row0 = [(c or "").strip() for c in grid[0]]
    if len(row0) < 2:
        return False
    if is_distillation_point(row0[0]) or is_astm_label(row0[0]):
        return False
    rest = [c for c in row0[1:] if c]
    if not rest:
        return False
    return not all(is_numeric_like(c) for c in rest)


def build_table_contexts(
    normalized: NormalizedDocument,
    tables_by_id: dict[str, ParsedTable] | None = None,
) -> dict[str, TableContext]:
    """Derive a TableContext for every table element of the normalized document."""
    tables_by_id = tables_by_id or {}
    contexts: dict[str, TableContext] = {}

    queue: list[str] = []                 # labels waiting for a table (FIFO)
    queue_kinds: list[ContentType] = []   # element kind of each queued label
    last_label = ""                       # label handed to the previous table

    def push(text: str, kind: ContentType) -> None:
        # A run of list items ("LP Steam:", "MP steam:", ...) names the tables one by one;
        # the section heading / intro line before them is not a per-table label any more.
        if kind == ContentType.LIST_ITEM and queue and any(k != ContentType.LIST_ITEM for k in queue_kinds):
            keep = [(q, k) for q, k in zip(queue, queue_kinds) if k == ContentType.LIST_ITEM]
            queue[:] = [q for q, _ in keep]
            queue_kinds[:] = [k for _, k in keep]
        queue.append(text)
        queue_kinds.append(kind)

    def reset(text: str, kind: ContentType) -> None:
        queue[:] = [text]
        queue_kinds[:] = [kind]

    def pop() -> str:
        queue_kinds.pop(0)
        return queue.pop(0)
    numbered_heading = ""                 # last "3.3.1.1 ..." style heading (raw)
    last_root_table: str | None = None    # previous table that had its own header row
    last_root_cols: int | None = None
    current_scenario = ""                 # operating case named by the latest heading/caption/label
    last_table_id: str | None = None      # for a caption that follows its table

    for elem in normalized.elements:
        text = (elem.content or "").strip()
        ctype = elem.content_type

        # Scenario scope: a short heading / caption / label naming a case ("BH Mode Atmos-side
        # Exchangers", "PG mode", "Basrah crude") applies to the tables that follow it within the
        # numbered section; a caption right after a table also applies to that table.
        if ctype != ContentType.TABLE and len(text) <= MAX_LABEL_CHARS:
            sc = scenario_for(text)
            if sc:
                current_scenario = sc
                if ctype == ContentType.FIGURE_CAPTION and last_table_id and not contexts[last_table_id].scenario:
                    contexts[last_table_id].scenario = sc
        if ctype != ContentType.TABLE and ctype != ContentType.FIGURE_CAPTION:
            last_table_id = None

        if ctype == ContentType.TABLE and elem.table_id:
            tbl = tables_by_id.get(elem.table_id)
            ctx = TableContext(table_id=elem.table_id, section_path=elem.section_path or "", page=elem.page)
            last_table_id = elem.table_id
            continuation = False
            # Only tables with subject columns (>= 3 columns) can continue a previous table:
            # a two-column key/value table without a header row is simply a new table under
            # the next queued label, not a page-split fragment.
            if (tbl is not None and last_root_table is not None and tbl.num_cols >= 3
                    and not has_header_row(tbl) and tbl.num_cols == last_root_cols):
                continuation = True
            if continuation:
                ctx.continuation_of = last_root_table
                ctx.raw_label = last_label
            else:
                if queue:
                    ctx.raw_label = pop()
                else:
                    ctx.raw_label = last_label or (elem.parent_heading or "")
                last_label = ctx.raw_label
                last_root_table = elem.table_id
                last_root_cols = tbl.num_cols if tbl is not None else None
            ctx.label = clean_label(ctx.raw_label)
            ctx.heading = clean_label(numbered_heading or elem.parent_heading or "")
            # "i.) SKO operation" under "3.3.1.1 Atmospheric Distillation column: (Basrah Crude)"
            if (ctx.label and ctx.heading and _ENUM_RE.match(ctx.raw_label)
                    and ctx.heading.lower() not in ctx.label.lower()):
                ctx.label = f"{ctx.heading} / {ctx.label}"
            ctx.scenario = scenario_for(ctx.label, tbl.caption if tbl is not None else "", ctx.heading) or current_scenario
            if continuation and ctx.continuation_of in contexts and not ctx.scenario:
                ctx.scenario = contexts[ctx.continuation_of].scenario
            contexts[elem.table_id] = ctx
            continue

        if ctype == ContentType.HEADING:
            if _NUMBERED_RE.match(text):
                numbered_heading = text
                if not scenario_for(text):
                    current_scenario = ""    # a new numbered section ends the scenario scope
            if is_label_text(text, ctype):
                reset(text, ctype)       # a heading starts a new label scope
                last_label = ""
            continue

        if ctype in (ContentType.LIST_ITEM, ContentType.PARAGRAPH, ContentType.NOTE, ContentType.FIGURE_CAPTION):
            if _NUMBERED_RE.match(text) and len(text) <= MAX_LABEL_CHARS:
                numbered_heading = text  # "3.3.1.2 Naphtha Stabilizer ..." parsed as a paragraph
                reset(text, ContentType.HEADING)
                last_label = ""
                if not scenario_for(text):
                    current_scenario = ""
            elif is_label_text(text, ctype):
                push(text, ctype)
            continue

    return contexts

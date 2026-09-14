"""Document structure reconstruction and deterministic block detection.

Operating manuals carry their own structure, but the layout parser flattens
it: every section header comes back at one level, the chapter title is
re-printed as a heading on every page, the table of contents is an ordinary
table and procedures are plain list items.  This module rebuilds that
structure with string rules only (no LLM):

  * :func:`parse_toc`              chapters (number, title, page range, revision)
                                   from the "CHAPTER No | TITLE | FROM PAGE NO" table(s)
  * :func:`chapter_page_map`       page -> chapter number from the repeating page-header
                                   box (table cells *or* loose text lines) and the TOC ranges
  * :func:`running_title_texts`    heading texts that are the chapter title re-printed on
                                   every page (dropped by the normalizer)
  * :func:`detect_procedures`      ordered instruction blocks (startup, shutdown,
                                   change-over, commissioning, ...) with typed steps
  * :func:`extract_cross_references` "refer Chapter 34" / "see Section 6.1.6.4"
  * :func:`extract_document_references` P&ID / drawing / standing-instruction / standard numbers
  * :func:`parse_standing_instructions` the standing-instruction register tables

Everything here is deterministic and cheap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from knowledge_layer.schemas.document_profile import CrossReference, ReferencedDocument, StandingInstruction
from knowledge_layer.schemas.normalized_document import (
    ChapterNode,
    ContentType,
    NormalizedElement,
    ProcedureBlock,
    ProcedureStepRecord,
    ProcedureType,
)
from knowledge_layer.schemas.parsed_document import ElementType, ParsedDocument, ParsedTable

# --------------------------------------------------------------------------- #
# Table of contents
# --------------------------------------------------------------------------- #
_TOC_HEADER_RE = re.compile(r"\bchapter\b.*\btitle\b|\btitle\b.*\bpage\b|\bcontents\b", re.I)
_TOC_PAGE_HEADER_RE = re.compile(r"\bpage\b", re.I)
_TOC_TITLE_CUT_RE = re.compile(
    r"\s+(?:Section\s+[A-Z]\s*:|[a-z]\.\s+\S|[a-z]\)\s+\S|:\s*[a-z]\)|\(?[ivx]{1,4}\)\s)", re.I,
)
_ADMIN_CHAPTER_RE = re.compile(
    r"administrative|table of contents|document control|record of revision|list of abbreviation|"
    r"copy holders|certificate|standing instruction|preface|foreword", re.I,
)
_CHAPTER_NO_RE = re.compile(r"chapter\s*no\.?\s*:?\s*(\d{1,3})\b", re.I)


def clean_toc_title(title: str) -> str:
    """'Plant Chemicals a. Withdrawal management b. Max Storage' -> 'Plant Chemicals'."""
    t = re.sub(r"\s+", " ", (title or "")).strip()
    # a wrapped tail of the previous row ("Employed'-(PSM/FR/2.9) Sampling ...")
    t = re.sub(r"^[A-Za-z]*'[^\s(]*\([^)]*\)\s*", "", t)
    m = _TOC_TITLE_CUT_RE.search(t)
    if m and m.start() >= 4:
        t = t[:m.start()]
    return t.strip(" :;-–.")


def parse_toc(parsed_doc: ParsedDocument, exclude_table_ids: set[str] | None = None) -> list[ChapterNode]:
    """Chapters from TOC tables ('CHAPTER No | TITLE | FROM PAGE NO | LATEST REV NO. | REV DATE')."""
    exclude_table_ids = exclude_table_ids or set()
    chapters: dict[int, ChapterNode] = {}
    for table in parsed_doc.tables:
        if table.table_id in exclude_table_ids or not table.grid or table.num_cols < 3:
            continue
        header = " ".join(table.grid[0]).lower()
        if not (_TOC_HEADER_RE.search(header) and _TOC_PAGE_HEADER_RE.search(header)):
            continue
        cols = [c.lower() for c in table.grid[0]]
        num_col = next((i for i, c in enumerate(cols) if "chapter" in c or c.strip() in ("no", "no.", "sl no", "sr no")), 0)
        title_col = next((i for i, c in enumerate(cols) if "title" in c or "description" in c), 1)
        page_col = next((i for i, c in enumerate(cols) if "page" in c), None)
        rev_col = next((i for i, c in enumerate(cols) if "rev" in c and "date" not in c), None)
        date_col = next((i for i, c in enumerate(cols) if "date" in c), None)
        for row in table.grid[1:]:
            if num_col >= len(row) or title_col >= len(row):
                continue
            num_txt = row[num_col].strip().rstrip(".")
            if not num_txt.isdigit():
                continue
            number = int(num_txt)
            title = clean_toc_title(row[title_col])
            if not title:
                continue
            page_start = 0
            if page_col is not None and page_col < len(row):
                m = re.search(r"\d{1,4}", row[page_col])
                page_start = int(m.group(0)) if m else 0
            node = ChapterNode(
                number=number, title=title, page_start=page_start, source="toc",
                revision=(row[rev_col].strip() if rev_col is not None and rev_col < len(row) else ""),
                revision_date=(row[date_col].strip() if date_col is not None and date_col < len(row) else ""),
                is_administrative=bool(_ADMIN_CHAPTER_RE.search(title)),
            )
            chapters.setdefault(number, node)
    ordered = [chapters[n] for n in sorted(chapters)]
    for i, ch in enumerate(ordered):
        if i + 1 < len(ordered) and ordered[i + 1].page_start:
            ch.page_end = max(ch.page_start, ordered[i + 1].page_start - 1)
        else:
            ch.page_end = parsed_doc.total_pages or None
    return ordered


def chapter_page_map(
    parsed_doc: ParsedDocument,
    template_table_ids: set[str] | None = None,
    toc_chapters: list[ChapterNode] | None = None,
) -> dict[int, int]:
    """page -> chapter number.

    Precedence: the page-header box printed on the page itself (a template table
    cell or a loose 'Chapter No: 16' text line), then the TOC page ranges.
    """
    result: dict[int, int] = {}
    template_table_ids = template_table_ids or set()
    for table in parsed_doc.tables:
        if template_table_ids and table.table_id not in template_table_ids:
            continue
        text = " ".join(c.content for c in table.cells)
        m = _CHAPTER_NO_RE.search(text)
        if m:
            result.setdefault(table.page, int(m.group(1)))
    for page in parsed_doc.pages:
        if page.page_number in result:
            continue
        for el in page.elements:
            if el.element_type in (ElementType.TEXT, ElementType.HEADING, ElementType.PAGE_HEADER) and len(el.content) < 60:
                m = _CHAPTER_NO_RE.search(el.content)
                if m:
                    result[page.page_number] = int(m.group(1))
                    break
    if toc_chapters:
        for ch in toc_chapters:
            if not ch.page_start:
                continue
            end = ch.page_end or parsed_doc.total_pages or ch.page_start
            for pg in range(ch.page_start, end + 1):
                result.setdefault(pg, ch.number)
    return result


# --------------------------------------------------------------------------- #
# Running titles (chapter title repeated as a heading on every page)
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" :.-")


def running_title_texts(
    parsed_doc: ParsedDocument,
    toc_chapters: list[ChapterNode] | None = None,
    min_pages: int = 3,
    uppercase_min_pages: int = 5,
) -> set[str]:
    """Normalised heading texts that are page furniture rather than section titles.

    A heading is a running title when it (a) matches a TOC chapter title, or
    (b) is printed in capitals on >= ``uppercase_min_pages`` pages, or (c) is a
    fragment (prefix/suffix) of another running title.  Short repeated labels
    such as "Actions:" / "Note:" are *not* running titles.
    """
    pages_by_text: dict[str, set[int]] = {}
    raw_by_text: dict[str, str] = {}
    for page in parsed_doc.pages:
        for el in page.elements:
            if el.element_type == ElementType.HEADING and el.content.strip():
                key = _norm(el.content)
                pages_by_text.setdefault(key, set()).add(page.page_number)
                raw_by_text.setdefault(key, el.content.strip())
    toc_titles = [_norm(ch.title) for ch in (toc_chapters or []) if ch.title]
    running: set[str] = set()
    for key, pages in pages_by_text.items():
        if len(pages) < min_pages or len(key) < 8:
            continue
        raw = raw_by_text[key]
        matches_toc = any(
            SequenceMatcher(None, key, t).ratio() >= 0.8 or (len(key.split()) >= 3 and key in t) for t in toc_titles
        )
        is_caps = raw.upper() == raw and len(key.split()) >= 2
        if matches_toc or (is_caps and len(pages) >= uppercase_min_pages):
            running.add(key)
    # fragments of a running title ("MAJOR EQUIPMENT DESCRIPTION AND", "OPERATING PROCEDURES")
    for key, pages in pages_by_text.items():
        if key in running or len(pages) < min_pages or len(key) < 8:
            continue
        if any(key != r and (r.startswith(key) or r.endswith(key)) for r in running):
            running.add(key)
    return running


# --------------------------------------------------------------------------- #
# Procedures
# --------------------------------------------------------------------------- #
IMPERATIVE_VERBS: frozenset[str] = frozenset("""
ensure check open close start stop take isolate drain commission charge line inform confirm switch place
increase reduce maintain keep verify remove install put bring adjust monitor record observe stabilize stabilise
establish cut shut trip reset purge flush vent bleed box blind deblind de-blind connect disconnect raise lower fill
empty depressurize depressurise pressurize pressurise warm cool light fire back release divert route run operate
engage disengage allow wait hold continue proceed collect transfer throttle crack note never do avoid use wear
obtain get make replace tighten loosen follow refer carry apply prepare arrange hand provide give attend inspect test
clean lubricate energize energise de-energize normalize normalise regulate set hook blow steam circulate build pull
push restore resume lift position seal tag lock clear complete review watch sample analyze analyse introduce
withdraw display
""".split())
_LEAD_ADVERBS = frozenset({"slowly", "gradually", "then", "first", "finally", "initially", "immediately", "now",
                           "also", "simultaneously", "carefully", "always", "do", "don't", "please"})

_PROC_KEYWORDS: list[tuple[re.Pattern, ProcedureType]] = [
    (re.compile(r"emergency|\besd\b", re.I), ProcedureType.EMERGENCY),
    (re.compile(r"change\s*-?\s*over|switch\s*-?\s*over", re.I), ProcedureType.CHANGEOVER),
    (re.compile(r"shut\s*-?\s*down", re.I), ProcedureType.SHUTDOWN),
    (re.compile(r"start\s*-?\s*up|starting|bringing .* on stream|light(?:ing)?\s*up", re.I), ProcedureType.STARTUP),
    (re.compile(r"commission|pre-?commission|preparatory|water flush|tightness|purging", re.I), ProcedureType.COMMISSIONING),
    (re.compile(r"isolat|blind|de-?blind|lock\s*out|loto", re.I), ProcedureType.ISOLATION),
    (re.compile(r"sampl", re.I), ProcedureType.SAMPLING),
    (re.compile(r"upset|deviation|stabili[sz]ation|corrective|\bactions?\b|steps to avoid", re.I), ProcedureType.UPSET_RESPONSE),
    (re.compile(r"safety|permit|confined|hot work|\bppe\b|spill", re.I), ProcedureType.SAFETY),
    (re.compile(r"maintenance|inspection|cleaning|washing|overhaul", re.I), ProcedureType.MAINTENANCE),
    (re.compile(r"check\s*-?\s*list", re.I), ProcedureType.CHECKLIST),
    (re.compile(r"normal operation|operation procedure|operating procedure|procedure", re.I), ProcedureType.NORMAL_OPERATION),
]
_LABEL_END_RE = re.compile(r":\s*(?:\([^)]*\))?\s*$")
_STEP_MARKER_RE = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|\(?[a-z][.)]|\(?[ivx]{1,4}[.)]|[-•▪■●○◦¾*✓✔Øø‣⁃>]|9\s)\s*", re.I)
_FIRST_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_PROC_HEADING_RE = re.compile(
    r"procedure|start\s*-?\s*up|shut\s*-?\s*down|commission|change\s*-?\s*over|check\s*-?\s*list|"
    r"emergency|isolation|\bsteps\b|activities|\bactions\b|precaution|preparation|sequence", re.I,
)


def is_imperative(text: str) -> bool:
    """True when the line reads as an instruction ('Ensure all the utilities ... are open')."""
    t = _STEP_MARKER_RE.sub("", (text or "").strip())
    m = _FIRST_WORD_RE.search(t)
    if not m:
        return False
    first = m.group(0).lower()
    if first in IMPERATIVE_VERBS:
        return True
    # "Slowly open ...", "Never put ...", "Then start ..."
    if first in _LEAD_ADVERBS:
        rest = _FIRST_WORD_RE.search(t[m.end():])
        return bool(rest and rest.group(0).lower() in IMPERATIVE_VERBS)
    return False


def classify_procedure(title: str, section_path: str) -> ProcedureType:
    for rx, ptype in _PROC_KEYWORDS:
        if rx.search(title or ""):
            return ptype
    for rx, ptype in _PROC_KEYWORDS:
        if rx.search(section_path or ""):
            return ptype
    return ProcedureType.GENERIC


def _procedure_label(text: str) -> bool:
    """'Pump Change over Procedure: (motor to turbine)', 'Operation procedure:', '1. Commissioning:'."""
    t = (text or "").strip()
    if not t or len(t) > 120 or " | " in t:
        return False
    if _LABEL_END_RE.search(t):
        return True
    return bool(_PROC_HEADING_RE.search(t)) and len(t.split()) <= 14


@dataclass
class _Run:
    items: list[NormalizedElement] = field(default_factory=list)
    label: NormalizedElement | None = None
    heading: str = ""


def detect_procedures(
    elements: list[NormalizedElement],
    document_id: str,
    min_steps: int = 3,
    min_imperative_fraction: float = 0.5,
) -> list[ProcedureBlock]:
    """Find ordered instruction blocks and mark their elements in place.

    A run of consecutive list items (short imperative paragraphs are tolerated
    inside the run) is a procedure when it has >= ``min_steps`` items and either
    most items are imperative sentences or the line/heading introducing it
    names a procedure.  Matching elements get ``content_type=PROCEDURE_STEP``,
    ``procedure_id`` and ``step_number``; the introducing line gets step 0.
    """
    from knowledge_layer.entity_identity import find_tags

    procedures: list[ProcedureBlock] = []
    run = _Run()
    last_heading = ""
    prev_short: NormalizedElement | None = None   # candidate label line just before a run

    def flush() -> None:
        nonlocal run
        items = run.items
        if len(items) >= min_steps:
            imperative = sum(1 for e in items if is_imperative(e.content))
            frac = imperative / len(items)
            label_text = run.label.content.strip() if run.label is not None else ""
            heading_is_proc = bool(_PROC_HEADING_RE.search(label_text + " " + run.heading))
            reasons: list[str] = []
            if frac >= min_imperative_fraction:
                reasons.append(f"{imperative}/{len(items)} imperative steps")
            if heading_is_proc and frac >= 0.25:
                reasons.append("introduced by a procedure heading/label")
            if reasons:
                pid = f"{document_id}_proc_{len(procedures) + 1:03d}"
                title = re.sub(r"\s+", " ", (label_text or run.heading or "Procedure")).strip(" :")
                first = items[0]
                block = ProcedureBlock(
                    procedure_id=pid, title=title,
                    procedure_type=classify_procedure(title, first.section_path),
                    chapter_number=first.chapter_number, section_id=first.section_id,
                    section_path=first.section_path, page_start=first.page, page_end=items[-1].page,
                    label_element_id=(run.label.element_id if run.label is not None else None),
                    detection_reasons=reasons,
                )
                tags: list[str] = []
                if run.label is not None:
                    run.label.procedure_id = pid
                    run.label.step_number = 0
                    tags += find_tags(run.label.content)
                for i, e in enumerate(items, start=1):
                    e.content_type = ContentType.PROCEDURE_STEP
                    e.procedure_id = pid
                    e.step_number = i
                    step_tags = find_tags(e.content)
                    tags += step_tags
                    block.steps.append(ProcedureStepRecord(
                        sequence=i, text=e.content.strip(), page=e.page, element_id=e.element_id, tags=step_tags,
                    ))
                block.applies_to = list(dict.fromkeys(tags))
                procedures.append(block)
        run = _Run()

    for el in elements:
        ctype = el.content_type
        if ctype == ContentType.HEADING:
            flush()
            last_heading = el.content.strip()
            prev_short = el if _procedure_label(el.content) else None
            continue
        if ctype == ContentType.TABLE:
            flush()
            prev_short = None
            continue
        if ctype == ContentType.LIST_ITEM:
            if not run.items:
                run.label = prev_short if (prev_short is not None and _procedure_label(prev_short.content)) else None
                run.heading = last_heading
            run.items.append(el)
            continue
        # paragraph / note inside a run: keep short imperative lines as steps, otherwise end the run
        text = el.content.strip()
        if run.items and ctype in (ContentType.PARAGRAPH, ContentType.NOTE) and len(text) <= 300 and is_imperative(text):
            run.items.append(el)
            continue
        if run.items and ctype == ContentType.NOTE:
            continue                      # "Note: ..." between steps does not break the procedure
        flush()
        prev_short = el if (ctype in (ContentType.PARAGRAPH, ContentType.NOTE) and len(text) <= 120) else None
    flush()
    return procedures


# --------------------------------------------------------------------------- #
# Cross references and document references
# --------------------------------------------------------------------------- #
_XREF_RE = re.compile(
    r"\b(?P<kind>Chapter|Section|Annexure|Appendix)\s*[-–:]?\s*(?P<num>\d{1,3}(?:\.\d{1,3})*)\b(?!\s*of\s+\d)", re.I,
)
_XREF_CONTEXT_RE = re.compile(
    r"\b(?:refer(?:red)?(?:\s+to)?|see|as\s+per|given\s+in|described\s+in|detailed\s+in|covered\s+in|"
    r"explained\s+in|incorporated\s+in|mentioned\s+in|listed\s+in|discussed\s+in|per|in|of|under|vide|"
    r"maintained\s+in|recorded\s+in|available\s+in|enclosed\s+in|attached\s+in)\s*$", re.I,
)


def extract_cross_references(elements: list[NormalizedElement]) -> list[CrossReference]:
    """'The startup checklist is given in Chapter 34' -> CrossReference(chapter, 34)."""
    refs: list[CrossReference] = []
    seen: set[tuple[str, str, str, int]] = set()
    for el in elements:
        if el.content_type == ContentType.HEADING:
            continue
        text = el.content
        for m in _XREF_RE.finditer(text):
            before = text[max(0, m.start() - 40):m.start()]
            if not _XREF_CONTEXT_RE.search(before) and not re.search(
                    r"\b(?:refer|see|as per|given|described|incorporated|manual)\b", before, re.I):
                continue
            kind = m.group("kind").lower()
            num = m.group("num")
            key = (el.section_path, kind, num, el.page)
            if key in seen:
                continue
            seen.add(key)
            start = max(0, m.start() - 80)
            end = min(len(text), m.end() + 40)
            refs.append(CrossReference(
                source_section=el.section_path, source_page=el.page, target_kind=kind, target_number=num,
                evidence=re.sub(r"\s+", " ", text[start:end]).strip(),
            ))
    return refs


_DOC_REF_PATTERNS: list[tuple[re.Pattern, str]] = [
    # P&ID 10-1100-E-203 Rev.5 / P&ID No. 10-1100-E-204
    (re.compile(r"P\s*&\s*ID(?:'s)?\s*(?:No\.?\s*)?[:\-]?\s*(?P<num>\d{1,3}\s*-\s*\d{3,4}\s*-\s*[A-Z]{1,2}\s*-\s*\d{2,4}(?:\s*-\s*\d{1,2})?)(?:\s*Rev\.?\s*(?P<rev>\d+))?", re.I), "P&ID"),
    (re.compile(r"\bPFD\s*(?:No\.?\s*)?[:\-]?\s*(?P<num>\d{1,3}\s*-\s*\d{3,4}\s*-\s*[A-Z]{1,2}\s*-\s*\d{2,4})(?:\s*Rev\.?\s*(?P<rev>\d+))?", re.I), "PFD"),
    (re.compile(r"\b(?:Dwg|Drawing)\.?\s*(?:No\.?\s*)?[:\-]?\s*(?P<num>[A-Z0-9]{1,4}(?:\s*-\s*[A-Z0-9]{1,6}){2,5})", re.I), "Drawing"),
    # Standing instructions: ADM/OPRN/PRODN/SI/008, OPRN/PROD/SI/011, ADM/OPRN/PROD N/SI/002 (OCR split)
    (re.compile(r"(?P<num>[A-Z]{2,5}(?:\s*/\s*[A-Z&]{2,6}(?:\s[A-Z]{1,2})?){0,3}\s*/\s*SI\s*/\s*\d{1,4})", re.I), "Standing Instruction"),
    (re.compile(r"\bSOP\s*(?:No\.?\s*)?[:\-]?\s*(?P<num>[A-Z0-9]{2,6}(?:\s*[-/]\s*[A-Z0-9]{1,6}){1,5})", re.I), "SOP"),
    # Standards: IS-4576, OISD-STD-118, ASTM D-86, API 610
    (re.compile(r"\b(?P<num>(?:IS|OISD|IBR|ASME|ANSI|NFPA)\s*[-:]?\s*(?:STD\s*[-:]?\s*)?[A-Z]?\d{2,5}(?:\s*-\s*\d{1,4})?(?:\s*Part\s*[-–]?\s*\d+)?)", re.I), "Standard"),
    (re.compile(r"\b(?P<num>(?:ASTM|API)\s*[-:]?\s*[A-Z]?\s*-?\s*\d{2,5}(?:\s*-\s*\d{1,4})?)", re.I), "Standard"),
    # PSM forms: PSM/FR/2.9
    (re.compile(r"\b(?P<num>PSM\s*/\s*[A-Z]{1,4}\s*/\s*\d+(?:\.\d+)?)", re.I), "PSM form"),
]


def _norm_ref(num: str) -> str:
    n = re.sub(r"\s*([-/])\s*", r"\1", re.sub(r"\s+", " ", num.strip())).upper()
    if "/SI/" in n:                       # register numbers are OCR-split ("PROD N/SI/002"): no spaces at all
        n = re.sub(r"\s+", "", n)
    return n


def extract_document_references(elements: list[NormalizedElement]) -> list[ReferencedDocument]:
    """Document/drawing/standard references that carry an actual identifier."""
    refs: dict[str, ReferencedDocument] = {}
    for el in elements:
        if el.content_type == ContentType.TABLE and re.search(r"abbreviation", el.section_path, re.I):
            continue
        text = el.content
        for rx, kind in _DOC_REF_PATTERNS:
            for m in rx.finditer(text):
                num = _norm_ref(m.group("num"))
                if len(num) < 5:
                    continue
                rev = m.groupdict().get("rev") or ""
                key = f"{kind}:{num}"
                if key in refs:
                    continue
                start, end = max(0, m.start() - 60), min(len(text), m.end() + 60)
                label = f"{kind} {num}" if kind in ("P&ID", "PFD", "Drawing", "SOP") else num
                refs[key] = ReferencedDocument(
                    reference_text=label + (f" Rev.{rev}" if rev else ""),
                    document_type=kind, document_number=num, page=el.page,
                    evidence=re.sub(r"\s+", " ", text[start:end]).strip(),
                )
    return list(refs.values())


# --------------------------------------------------------------------------- #
# Standing instruction register
# --------------------------------------------------------------------------- #
_SI_NUM_RE = re.compile(r"[A-Z]{2,5}(?:\s*/\s*[A-Z&]{2,6}(?:\s[A-Z]{1,2})?){0,3}\s*/\s*SI\s*/\s*\d{1,4}", re.I)
_SI_CHAPTER_RE = re.compile(r"chapter\s*[-–:.]*\s*(\d{1,3})", re.I)


def parse_standing_instructions(tables: list[ParsedTable]) -> list[StandingInstruction]:
    """Rows of the 'STANDING INSTRUCTION NO. | TITLE | DATE | ...' register tables."""
    out: list[StandingInstruction] = []
    seen: set[str] = set()
    header_cols: dict[str, int] | None = None
    for table in tables:
        grid = table.grid or []
        if not grid or table.num_cols < 3:
            continue
        head = [c.lower() for c in grid[0]]
        rows = grid
        if any("standing instruction" in c for c in head):
            header_cols = {
                "num": next((i for i, c in enumerate(head) if "no" in c and "instruction" in c), 1),
                "title": next((i for i, c in enumerate(head) if "title" in c), 2),
                "date": next((i for i, c in enumerate(head) if "date" in c), 3),
                "status": next((i for i, c in enumerate(head) if "expired" in c or "incorporated" in c or "status" in c), len(head) - 1),
            }
            rows = grid[1:]
        elif header_cols is None or not any(_SI_NUM_RE.search(c) for c in grid[0]):
            continue                       # not a continuation of a register table
        cols = header_cols
        for row in rows:
            cells = [re.sub(r"\s+", " ", c).strip() for c in row]
            num_cell = next((c for c in cells if _SI_NUM_RE.search(c)), None)
            if num_cell is None:
                continue
            number = _norm_ref(_SI_NUM_RE.search(num_cell).group(0))
            if number in seen:
                continue
            seen.add(number)
            title = cells[cols["title"]] if cols["title"] < len(cells) else ""
            date = cells[cols["date"]] if cols["date"] < len(cells) else ""
            remark = cells[cols["status"]] if cols["status"] < len(cells) else ""
            status = "in_use"
            if re.search(r"expired", remark, re.I):
                status = "expired"
            elif re.search(r"invalid|cancel", remark, re.I):
                status = "cancelled"
            elif re.search(r"incorporated", remark, re.I):
                status = "incorporated"
            m = _SI_CHAPTER_RE.search(remark)
            out.append(StandingInstruction(
                number=number, title=title, issue_date=date, status=status,
                incorporated_in_chapter=(m.group(1) if m else ""), remark=remark, page=table.page,
            ))
    return out

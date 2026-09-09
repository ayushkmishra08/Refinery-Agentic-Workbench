"""Deterministic relationship extraction for routing / containment / control statements.

Operating manuals express most topology as terse routing lists that a 7B model
under a JSON grammar tends to flatten into nouns:

    Kerosene to
    Storage
    Diesel Header
    FO blend to HFO header
    To DHDS upstream of 11-E-23
    The CDU also comprises the Naphtha Stabilizer section and the Water Wash section.
    The VDU is designed to process RCO from CDU.
    11-P-01 A/B takes suction from 11-V-02 and discharges to 11-E-05.

Rules fire on sentences and list items and emit ExtractedRelationship objects
with ``source='rule'``, confidence 0.75 and the exact sentence as evidence.
Subject and object must be a recognised tag (entity_identity) or a name that
matches an entity extracted from the same chunk or a glossary term
(``known_names``).  When only one side is known the relationship is still
emitted with confidence 0.6 (``predicate_raw='rule:partial:...'``) so the
validator/grounding step decides, instead of silently dropping information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from schemas.knowledge import ExtractedRelationship, RelationshipType
from src.entity_identity import TAG_SCAN_RE, parse_tag

RULE_CONFIDENCE = 0.75
PARTIAL_CONFIDENCE = 0.6

_MARKER_RE = re.compile(r"^\s*(?:\(?[a-z0-9]{1,2}\)|[-•▪■●○◦¾*]|\d+\.)\s*", re.IGNORECASE)
_HEADER_RE = re.compile(r"^\s*#{1,6}\s*")
_FIGURE_RE = re.compile(r"^\s*\[(Figure|Table|Context)", re.IGNORECASE)

# A noun phrase: up to 6 tokens, starting with a capital/digit, never crossing a
# sentence boundary or swallowing conjunctions/verbs/prepositions.
_W = r"(?!(?i:and|or|is|are|was|were|takes|take|from|to|of|by|with|for|in|on|at|as|via|given|provided|used|routed|mixed)\b)[A-Za-z0-9/&\-']+"
_NP = rf"(?:[Tt]he\s+)?(?=[A-Z0-9]){_W}(?:\s+{_W}){{0,5}}?"
_TAG = TAG_SCAN_RE.pattern

_SOURCE_TO_RE = re.compile(rf"^(?P<src>{_NP})\s+to\s*:?\s*$")
_INLINE_TO_RE = re.compile(rf"^(?P<src>{_NP})\s+to\s+(?P<dst>[A-Za-z0-9][^.;]{{1,80}}?)\.?\s*$")
_LEADING_TO_RE = re.compile(r"^\s*[Tt]o\s+(?P<dst>[A-Za-z0-9][^.;]{1,80}?)\.?\s*$")
_SENTENCE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|gets|get|shall|should|will|must|can|given|provided|used|mixed|designed)\b"
)

_COMPRISES_RE = re.compile(
    rf"(?P<whole>{_NP})\s+(?:also\s+)?(?:comprises|consists of|is comprised of|includes|contains)\s+(?P<parts>[^.]{{3,300}})\.",
)
_DESIGNED_FROM_RE = re.compile(
    rf"(?P<unit>{_NP})\s+is designed to process\s+(?P<feed>[A-Za-z0-9\- ]{{2,40}}?)\s+from\s+(?P<src>{_NP})[.,]",
)
_MOVED_FROM_TO_RE = re.compile(
    rf"(?P<feed>{_NP})\s+(?:is|are)\s+(?:pumped|fed|sent|routed|transferred|charged)\s+from\s+"
    rf"(?P<src>(?:the\s+)?[A-Za-z0-9\- ]{{2,60}}?)\s+to\s+(?P<dst>{_NP})[.,]",
)
_ROUTED_TO_RE = re.compile(rf"(?P<x>{_NP})\s+(?:is|are)\s+(?:routed|sent|directed|diverted)\s+to\s+(?P<y>{_NP})[.,;]")
_FEEDS_RE = re.compile(rf"(?P<x>{_NP})\s+feeds\s+(?:the\s+)?(?P<y>{_NP})[.,;]")
_SUCTION_RE = re.compile(
    rf"(?P<x>{_NP})\s+takes?\s+suction\s+from\s+(?:the\s+)?(?P<y>{_NP})"
    rf"(?:\s+and\s+discharges?\s+(?:in)?to\s+(?:the\s+)?(?P<z>{_NP}))?(?=[.,;]|\s+and\b)"
)
_DISCHARGES_RE = re.compile(rf"(?P<x>{_NP})\s+discharges?\s+(?:in)?to\s+(?:the\s+)?(?P<y>{_NP})[.,;]")
_PROTECTED_RE = re.compile(rf"(?P<x>{_NP})\s+(?:is|are)\s+protected\s+by\s+(?:the\s+)?(?P<y>{_NP})[.,;]")
_CONTROLLED_RE = re.compile(rf"(?P<x>{_NP})\s+(?:is|are)\s+controlled\s+by\s+(?:the\s+)?(?P<y>{_NP})[.,;]")
_STREAM_RE = re.compile(
    rf"(?P<x>{_NP})\s+(?:is\s+|are\s+|located\s+|lies\s+)?(?P<dir>up\s?stream|down\s?stream)\s+of\s+(?P<y>{_TAG})"
    rf"(?:\s+and\s+(?P<dir2>up\s?stream|down\s?stream)\s+of\s+(?P<y2>{_TAG}))?",
)

# Destinations that are too generic to be an entity on their own.
_GENERIC = {"storage", "slop", "tanks", "tank", "product", "products", "feed", "storage tanks", "line", "unit", "header"}
_QUALIFIER_SPLIT_RE = re.compile(r"\s+(?:as|via|for|along with|when|through|after|before|with|in case)\s+", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-/]*")


@dataclass
class _Line:
    raw: str
    text: str
    is_heading: bool


def _clean_line(raw: str) -> _Line:
    text = raw.strip()
    is_heading = bool(_HEADER_RE.match(text))
    text = _HEADER_RE.sub("", text)
    text = _MARKER_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _Line(raw=raw.strip(), text=text, is_heading=is_heading)


def _strip_article(name: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", (name or "").strip(), flags=re.IGNORECASE).strip(" .,:;")


def _clean_destination(dst: str) -> str | None:
    """'FCCU-I/FCCU-II as hot feed' -> 'FCCU-I/FCCU-II'; '... hook-up to VRCFP.' -> 'VRCFP'."""
    d = dst.strip().rstrip(".").strip()
    if " to " in d:
        d = d.rsplit(" to ", 1)[1]
    d = re.split(r"\s+(?:up\s?stream|down\s?stream)\s+of\s+", d, flags=re.IGNORECASE)[0]
    d = _QUALIFIER_SPLIT_RE.split(d)[0]
    d = re.sub(r"\s*\((?:[^()]{0,40})\)\s*$", "", d)
    d = _strip_article(d)
    if len(d) < 3 or len(d.split()) > 8 or d.endswith("-"):
        return None
    if d.lower() in _GENERIC or not re.search(r"[A-Z0-9]", d):
        return None
    return d


class _Known:
    """Membership test against tags, chunk entities and glossary terms (fuzzy)."""

    def __init__(self, names: Iterable[str]):
        self.names = {n.strip().lower() for n in names if n and n.strip()}
        self.tokens = {n: set(_TOKEN_RE.findall(n)) for n in self.names}

    def __call__(self, name: str) -> bool:
        if not name:
            return False
        if parse_tag(name) is not None:
            return True
        n = name.strip().lower()
        if n in self.names:
            return True
        n_tok = set(_TOKEN_RE.findall(n))
        for k, k_tok in self.tokens.items():
            if not k_tok or not n_tok:
                continue
            if n in k or k in n:
                return True
            inter = len(n_tok & k_tok)
            if inter / max(len(n_tok), 1) >= 0.8 or inter / max(len(k_tok), 1) >= 0.8:
                return True
            if SequenceMatcher(None, n, k).ratio() >= 0.9:
                return True
        return False


def extract_rule_relationships(
    chunk_text: str,
    chunk_id: str,
    document_id: str,
    page: int,
    section: str = "",
    known_names: Iterable[str] = (),
) -> list[ExtractedRelationship]:
    """Apply routing/containment/control rules to a chunk's text."""
    known = _Known(known_names)
    rels: list[ExtractedRelationship] = []
    seen: set[tuple[str, str, str]] = set()

    def add(subject: str, predicate: RelationshipType, obj: str, evidence: str) -> None:
        subject, obj = _strip_article(subject), _strip_article(obj)
        if not subject or not obj or subject.lower() == obj.lower():
            return
        if len(subject.split()) > 8 or len(obj.split()) > 8:
            return
        s_known, o_known = known(subject), known(obj)
        if not s_known and not o_known:
            return
        key = (subject.lower(), predicate.value, obj.lower())
        if key in seen:
            return
        seen.add(key)
        partial = not (s_known and o_known)
        rels.append(ExtractedRelationship(
            relationship_id=f"{chunk_id}_rr{len(rels)}",
            subject=subject, predicate=predicate,
            predicate_raw=f"rule:{'partial:' if partial else ''}{predicate.value}",
            object=obj, evidence=evidence.strip(), page=page, section=section,
            document_id=document_id, chunk_id=chunk_id,
            confidence=PARTIAL_CONFIDENCE if partial else RULE_CONFIDENCE,
            source="rule",
        ))

    body = chunk_text
    if body.startswith("[Context from previous section:]"):
        parts = body.split("\n---\n", 1)
        body = parts[1] if len(parts) == 2 else body

    lines = [_clean_line(ln) for ln in body.split("\n") if ln.strip()]

    # ---- routing lists: "X to" heading followed by destinations, or "X to Y" lines
    current_source: str | None = None
    current_source_line = ""
    for ln in lines:
        text = ln.text
        if not text or _FIGURE_RE.match(text):
            current_source = None
            continue
        words = len(text.split())

        m = _SOURCE_TO_RE.match(text)
        if m and words <= 8:
            current_source = _strip_article(m.group("src"))
            current_source_line = ln.raw
            continue

        m = _INLINE_TO_RE.match(text)
        if m and words <= 12 and not text.lower().startswith("to ") and not _SENTENCE_VERB_RE.search(text):
            dst = _clean_destination(m.group("dst"))
            src = _strip_article(m.group("src"))
            if dst and src and len(src.split()) <= 6:
                add(src, RelationshipType.FEEDS, dst, ln.raw)
            continue

        if ln.is_heading and not current_source:
            continue
        if ln.is_heading and words > 8:
            current_source = None
            continue

        if current_source:
            if words > 10 or text.endswith(":") or _SENTENCE_VERB_RE.search(text) or text[0].isdigit():
                current_source = None
                continue
            m = _LEADING_TO_RE.match(text)
            dst = _clean_destination(m.group("dst") if m else text)
            if dst:
                add(current_source, RelationshipType.FEEDS, dst, f"{current_source_line} {ln.raw}")

    # ---- sentence-level patterns
    flat = re.sub(r"\s+", " ", body)
    for m in _COMPRISES_RE.finditer(flat):
        whole = _strip_article(m.group("whole"))
        for part in re.split(r",\s*|\s+and\s+", m.group("parts")):
            part = _strip_article(part)
            if 2 <= len(part) <= 60:
                add(part, RelationshipType.PART_OF, whole, m.group(0))
    for m in _DESIGNED_FROM_RE.finditer(flat):
        unit = _strip_article(m.group("unit"))
        add(unit, RelationshipType.RECEIVES_FROM, _strip_article(m.group("src")), m.group(0))
        add(unit, RelationshipType.HAS_FEED, _strip_article(m.group("feed")), m.group(0))
    for m in _MOVED_FROM_TO_RE.finditer(flat):
        add(_strip_article(m.group("src")), RelationshipType.FEEDS, _strip_article(m.group("dst")), m.group(0))
    for m in _ROUTED_TO_RE.finditer(flat):
        add(m.group("x"), RelationshipType.DISCHARGES_TO, m.group("y"), m.group(0))
    for m in _FEEDS_RE.finditer(flat):
        add(m.group("x"), RelationshipType.FEEDS, m.group("y"), m.group(0))
    for m in _SUCTION_RE.finditer(flat):
        add(m.group("x"), RelationshipType.SUCTION_FROM, m.group("y"), m.group(0))
        if m.group("z"):
            add(m.group("x"), RelationshipType.DISCHARGES_TO, m.group("z"), m.group(0))
    for m in _DISCHARGES_RE.finditer(flat):
        add(m.group("x"), RelationshipType.DISCHARGES_TO, m.group("y"), m.group(0))
    for m in _PROTECTED_RE.finditer(flat):
        add(m.group("x"), RelationshipType.PROTECTED_BY, m.group("y"), m.group(0))
    for m in _CONTROLLED_RE.finditer(flat):
        add(m.group("x"), RelationshipType.CONTROLLED_BY, m.group("y"), m.group(0))
    for m in _STREAM_RE.finditer(flat):
        x, y = m.group("x"), m.group("y")
        if parse_tag(y) is not None:
            pred = RelationshipType.UPSTREAM_OF if "up" in m.group("dir").lower() else RelationshipType.DOWNSTREAM_OF
            add(x, pred, y, m.group(0))
        if m.group("y2") and parse_tag(m.group("y2")) is not None:
            pred2 = RelationshipType.UPSTREAM_OF if "up" in m.group("dir2").lower() else RelationshipType.DOWNSTREAM_OF
            add(x, pred2, m.group("y2"), m.group(0))

    return rels

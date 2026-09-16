"""Near-miss matching for equipment tags and names.

An engineer at a console types the tag they remember, and the one they remember is often not
the one in the manual: ``12-3-01`` for ``12-P-01``, ``11-E-1`` for ``11-E-01``, ``crude
charging pump`` for ``Crude Charge Pump``. Refusing those with "which equipment do you mean?"
is the wrong answer when the manual contains exactly one plausible candidate.

A tag is decomposed into unit / class / number / train, and each part is scored separately,
because the parts fail in different ways. The unit prefix is almost never wrong — it is the
section of the plant the engineer is standing in. The number is usually right. The class
letter is what gets dropped, OCR'd into a digit, or guessed. So a candidate that agrees on
unit and number, and only disagrees on the class letter, is a strong match; one that agrees
only on the class letter is not.

When the caller says what class of equipment the sentence was about ("pump 12-3-01"), a
candidate of that class is preferred — this is what makes the suggestion read as "a pump with
a similar name and the same job" rather than "some tag that looks like yours".
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from workbench.core.knowledge import EntityRecord

# 11-P-01A, 11 P 01, 11P01, 12-3-01, 11-STV-001, 120-T-01.
# A separator is required unless the class field is letters ("11P01"), otherwise a bare number
# such as the 1105 in PIC-1105 would parse as a three-field tag.
TAG_LIKE_RE = re.compile(
    r"\b(?P<unit>\d{1,3})(?:\s*[-–/ ]\s*(?P<cls>[A-Za-z]{1,4}|\d{1,2})\s*[-–/ ]\s*|(?P<cls2>[A-Za-z]{1,4}))"
    r"(?P<num>\d{1,5})\s*(?P<train>[A-Za-z](?:\s*/\s*[A-Za-z])*)?\b"
)
# English that can sit between two numbers in a sentence ("8.0 to 9.0", "2 of 3") but never
# names a class of equipment
NOT_A_CLASS = {"TO", "AND", "OR", "AT", "IN", "OF", "BY", "ON", "PER", "VS", "THRU", "UPTO"}
# PIC-1105, TRC-2133: a loop tag, with no unit prefix
BARE_TAG_RE = re.compile(r"\b(?P<cls>[A-Za-z]{2,4})\s*[-–]\s*(?P<num>\d{2,5})(?P<train>[A-Za-z])?\b")

# class letters that mean the same family of equipment; a mention using either should match the other
CLASS_FAMILIES: dict[str, set[str]] = {
    "P": {"P", "PM", "PT", "PMP"},
    "E": {"E", "EX", "HE"},
    "C": {"C", "COL", "T"},
    "F": {"F", "H", "FUR"},
    "V": {"V", "D", "VES"},
    "J": {"J", "EJ"},
    "T": {"T", "TK"},
}
# equipment word in the sentence -> the entity_type the index uses
TYPE_HINTS: list[tuple[str, str]] = [
    (r"\bpumps?\b", "Pump"), (r"\b(exchangers?|coolers?|condensers?|reboilers?)\b", "Exchanger"),
    (r"\b(columns?|towers?)\b", "Column"), (r"\b(heaters?|furnaces?)\b", "Heater"),
    (r"\b(vessels?|drums?|accumulators?)\b", "Vessel"), (r"\btanks?\b", "Tank"),
    (r"\b(valves?|psvs?)\b", "ControlValve"), (r"\b(controllers?|instruments?|transmitters?)\b", "Instrument"),
    (r"\bejectors?\b", "Equipment"), (r"\bdesalters?\b", "Desalter"), (r"\bstrippers?\b", "Stripper"),
]
# the class letter an entity_type normally carries, for the "same job" part of the score
TYPE_CLASS: dict[str, str] = {
    "Pump": "P", "Exchanger": "E", "Column": "C", "Stripper": "C", "Heater": "F",
    "Vessel": "V", "Drum": "V", "Tank": "T", "Desalter": "V",
}

ACCEPT_SCORE = 0.72          # at or above this a candidate may be adopted without asking
CLEAR_SCORE = 0.85           # ... and at or above this a tie is broken by how often each is documented
MARGIN = 0.08                # between ACCEPT_SCORE and CLEAR_SCORE the runner-up must be this far behind
OFFER_SCORE = 0.45           # below this a candidate is not even offered as an option


@dataclass(frozen=True)
class TagParts:
    unit: str = ""
    cls: str = ""
    number: str = ""
    train: str = ""
    raw: str = ""

    @property
    def number_int(self) -> int | None:
        try:
            return int(self.number)
        except (TypeError, ValueError):
            return None

    def normalized(self) -> str:
        return f"{self.unit}{self.cls.upper()}{self.number.lstrip('0') or '0'}{self.train.upper().replace('/', '')}"


@dataclass
class TagSuggestion:
    entity: EntityRecord
    score: float
    reasons: list[str]

    @property
    def label(self) -> str:
        return self.entity.name or self.entity.canonical_tag or self.entity.entity_uid

    def describe(self) -> str:
        """One line an engineer can accept or reject: what it is and the single best reason."""
        what = (self.entity.entity_type or "equipment").lower()
        tag = self.entity.canonical_tag or ""
        name = (self.entity.name or "").split(" (")[0]
        head = f"{tag} ({name})" if tag and name and name != tag else (tag or name)
        article = "an" if what[:1] in "aeiou" else "a"
        return f"{head} — {article} {what}" + (f", {self.reasons[0]}" if self.reasons else "")


def parse_tag(text: str) -> TagParts | None:
    """Decompose the first tag-like token in ``text``, or None when there is none.

    A loop tag that opens with letters (PIC-1105) is read first, so its number is not mistaken
    for a unit-class-number triple.
    """
    t = text or ""
    if (m := BARE_TAG_RE.match(t.strip())) is not None:
        return TagParts(cls=m.group("cls").upper(), number=m.group("num"),
                        train=(m.group("train") or "").upper(), raw=m.group(0))
    for m in TAG_LIKE_RE.finditer(t):
        cls = (m.group("cls") or m.group("cls2") or "").upper()
        if cls in NOT_A_CLASS:
            continue
        return TagParts(unit=m.group("unit"), cls=cls, number=m.group("num"),
                        train=(m.group("train") or "").upper().replace(" ", ""), raw=m.group(0))
    if (m := BARE_TAG_RE.search(t)) is not None:
        return TagParts(cls=m.group("cls").upper(), number=m.group("num"),
                        train=(m.group("train") or "").upper(), raw=m.group(0))
    return None


def looks_like_tag(text: str) -> bool:
    """True for a mention shaped like an equipment tag — digits and separators, not a phrase."""
    t = (text or "").strip()
    return bool(t) and len(t.split()) <= 3 and parse_tag(t) is not None and any(ch.isdigit() for ch in t)


def type_hint(text: str) -> str | None:
    """The equipment class the sentence names around the tag ("pump 12-3-01" -> Pump)."""
    for rx, etype in TYPE_HINTS:
        if re.search(rx, text or "", re.IGNORECASE):
            return etype
    return None


def _class_score(want: str, have: str) -> tuple[float, str]:
    """How close two class letters are, and why."""
    if not want or not have:
        return 0.10, ""
    want, have = want.upper(), have.upper()
    if want == have:
        return 1.0, ""
    for fam in CLASS_FAMILIES.values():
        if want in fam and have in fam:
            return 0.85, f"{want} and {have} are the same equipment family"
    if not want.isalpha():
        # "12-3-01": the class position holds a digit, so the engineer's tag names no class at
        # all. Nothing is known about the class, so it neither helps nor hurts.
        return 0.45, f"the middle field '{want}' is not an equipment letter"
    if want[0] == have[0]:
        return 0.6, ""
    return difflib.SequenceMatcher(None, want, have).ratio() * 0.5, ""


def score_tag(mention: TagParts, candidate: TagParts, *, expect_type: str | None = None,
              entity_type: str | None = None) -> tuple[float, list[str]]:
    """Similarity of two decomposed tags in [0, 1], with the reasons that produced it."""
    reasons: list[str] = []
    parts: list[tuple[float, float]] = []                 # (weight, score)

    if mention.unit and candidate.unit:
        if mention.unit == candidate.unit:
            parts.append((0.30, 1.0))
            reasons.append(f"same unit prefix {candidate.unit}")
        elif abs(int(mention.unit) - int(candidate.unit)) <= 1:
            parts.append((0.30, 0.45))
        else:
            parts.append((0.30, 0.0))
    mi, ci = mention.number_int, candidate.number_int
    if mi is not None and ci is not None:
        if mi == ci:
            parts.append((0.35, 1.0))
            reasons.append(f"same item number {candidate.number}")
        elif abs(mi - ci) <= 1:
            parts.append((0.35, 0.35))
        else:
            parts.append((0.35, 0.0))
    cs, why = _class_score(mention.cls, candidate.cls)
    parts.append((0.25, cs))
    if why:
        reasons.append(why)
    elif cs == 1.0 and mention.cls:
        reasons.append(f"same equipment letter {candidate.cls}")
    parts.append((0.10, difflib.SequenceMatcher(None, mention.normalized(), candidate.normalized()).ratio()))

    score = sum(w * s for w, s in parts) / sum(w for w, _ in parts)
    if expect_type and entity_type:
        # a proportional nudge, not a flat one: adding a constant saturates every strong
        # candidate at 1.0 and throws away the ordering the tag fields just established
        if expect_type == entity_type:
            score += (1.0 - score) * 0.35
            reasons.append(f"it is a {entity_type.lower()}, as the question said")
        elif TYPE_CLASS.get(expect_type) and TYPE_CLASS.get(expect_type) != candidate.cls:
            score *= 0.90
    return round(score, 3), reasons


def suggest_tags(mention: str, candidates: list[EntityRecord], *, expect_type: str | None = None,
                 limit: int = 4, min_score: float = OFFER_SCORE) -> list[TagSuggestion]:
    """Rank documented entities by how plausibly they are the tag the engineer meant."""
    parsed = parse_tag(mention)
    if parsed is None:
        return []
    out: list[TagSuggestion] = []
    for e in candidates:
        ct = e.canonical_tag or ""
        cparsed = parse_tag(ct) or parse_tag(e.name or "")
        if cparsed is None:
            continue
        score, reasons = score_tag(parsed, cparsed, expect_type=expect_type, entity_type=e.entity_type)
        if score >= min_score:
            out.append(TagSuggestion(entity=e, score=score, reasons=reasons))
    out.sort(key=lambda s: (-s.score, -(s.entity.mention_count or 0)))
    return _dedupe_trains(out)[:limit]


def _dedupe_trains(suggestions: list[TagSuggestion]) -> list[TagSuggestion]:
    """Collapse the A/B trains of one item (11-P-01, 11-P-01A, 11-P-01B) to their best entry.

    Offering an engineer three spellings of the same pump reads as three candidates and makes
    a clear match look like an ambiguous one.
    """
    seen: dict[str, TagSuggestion] = {}
    out: list[TagSuggestion] = []
    for s in suggestions:
        parts = parse_tag(s.entity.canonical_tag or s.entity.name or "")
        base = f"{parts.unit}-{parts.cls}-{parts.number.lstrip('0') or '0'}" if parts else s.entity.entity_uid
        if base in seen:
            continue
        seen[base] = s
        out.append(s)
    return out


def suggest_names(mention: str, candidates: list[EntityRecord], *, limit: int = 4,
                  min_score: float = 0.62) -> list[TagSuggestion]:
    """Rank documented entities by name similarity — for a misspelt name rather than a tag."""
    key = re.sub(r"[^a-z0-9 ]+", " ", (mention or "").lower()).strip()
    if not key:
        return []
    out: list[TagSuggestion] = []
    for e in candidates:
        names = [e.name or "", (e.name or "").split(" (")[0], *(e.aliases or [])]
        best = 0.0
        for n in names:
            n_key = re.sub(r"[^a-z0-9 ]+", " ", n.lower()).strip()
            if not n_key:
                continue
            best = max(best, difflib.SequenceMatcher(None, key, n_key).ratio())
        if best >= min_score:
            out.append(TagSuggestion(entity=e, score=round(best, 3), reasons=["the documented name reads almost the same"]))
    out.sort(key=lambda s: (-s.score, -(s.entity.mention_count or 0)))
    return out[:limit]


def adopt(suggestions: list[TagSuggestion], *, accept_score: float = ACCEPT_SCORE,
          margin: float = MARGIN, clear_score: float = CLEAR_SCORE) -> TagSuggestion | None:
    """The one suggestion confident enough to answer about without asking first, if any.

    Confidence is not the top score alone. Two candidates a hair apart usually mean the
    question is genuinely ambiguous, and asking is cheaper than answering about the wrong
    pump. The exception is a near-perfect match: when both candidates score above
    ``clear_score``, the tag is only a keystroke away from both, and the one the manual
    actually talks about is the one the engineer means.
    """
    if not suggestions:
        return None
    top = suggestions[0]
    if top.score < accept_score:
        return None
    if len(suggestions) == 1 or (top.score - suggestions[1].score) >= margin:
        return top
    runner_up = suggestions[1]
    if top.score >= 0.995 and runner_up.score < 0.995:
        return top                          # every field of the tag agrees; only the runner-up is approximate
    if top.score >= clear_score and (top.entity.mention_count or 0) >= 2 * (runner_up.entity.mention_count or 0) + 1:
        return top
    return None

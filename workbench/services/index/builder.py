"""Build a DocumentIndex from knowledge-layer objects, deterministically.

Inputs are the knowledge layer's own pydantic models (NormalizedDocument, Chunk, ParsedTable,
DocumentProfile, DocumentGlossary). Claims, rule relationships and tag identities come from
the knowledge layer's deterministic modules, so values are identical to what the pipeline
writes to Neo4j. Entity names/aliases are harvested from text patterns and equipment tables
(chapters like "List of Plant Equipment" and "Instrumentation Tags Description").
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from knowledge_layer.chunker import Chunk
from knowledge_layer.entity_identity import document_scoped_uid, entity_uid, find_tags, parse_tag
from knowledge_layer.rule_relations import extract_rule_relationships
from knowledge_layer.schemas.claims import EngineeringClaim
from knowledge_layer.schemas.document_profile import DocumentProfile
from knowledge_layer.schemas.glossary import DocumentGlossary
from knowledge_layer.schemas.normalized_document import NormalizedDocument
from knowledge_layer.schemas.parsed_document import ParsedTable
from knowledge_layer.spec_claims import extract_prose_claims, extract_spec_claims
from knowledge_layer.table_claims import extract_table_claims
from knowledge_layer.table_context import build_table_contexts
from workbench.core.knowledge import (
    ChunkRecord,
    ClaimRecord,
    CrossReferenceRecord,
    DocumentInfo,
    DocumentReferenceRecord,
    EntityRecord,
    GlossaryRecord,
    ProcedureRecord,
    RelationRecord,
    SectionRecord,
    StandingInstructionRecord,
    StepRecord,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- tag prefix -> type
PREFIX_TYPES: dict[str, str] = {
    "P": "Pump", "PM": "Pump", "PT": "Pump", "PS": "Pump",
    "C": "Column", "T": "Tank", "V": "Vessel", "D": "Drum", "E": "Exchanger", "F": "Heater", "H": "Heater",
    "K": "Compressor", "B": "Blower", "EJ": "Ejector", "EM": "Motor", "M": "Motor", "A": "Analyzer", "AC": "AirCooler",
    "FIL": "Filter", "R": "Reactor", "S": "Separator", "X": "Package",
    "PSV": "ReliefValve", "PRV": "ReliefValve", "RV": "ReliefValve", "SV": "SafetyValve",
    "PI": "Instrument", "PG": "Instrument", "PT_": "Instrument", "PDI": "Instrument", "PDT": "Instrument",
    "PIC": "Controller", "PRC": "Controller", "PC": "Controller", "PDIC": "Controller", "PDRC": "Controller",
    "TI": "Instrument", "TG": "Instrument", "TT": "Instrument", "TIC": "Controller", "TRC": "Controller", "TC": "Controller",
    "TDI": "Instrument", "TDIC": "Controller", "TR": "Instrument", "TE": "Instrument",
    "FI": "Instrument", "FT": "Instrument", "FG": "Instrument", "FIC": "Controller", "FRC": "Controller", "FC": "Controller", "FQ": "Instrument",
    "LI": "Instrument", "LG": "Instrument", "LT": "Instrument", "LIC": "Controller", "LRC": "Controller", "LC": "Controller",
    "PV": "ControlValve", "FV": "ControlValve", "TV": "ControlValve", "LV": "ControlValve", "HV": "HandValve", "XV": "OnOffValve",
    "PSL": "Switch", "PSH": "Switch", "PSLL": "Switch", "PSHH": "Switch", "LSL": "Switch", "LSH": "Switch", "LSLL": "Switch", "LSHH": "Switch",
    "TSH": "Switch", "TSHH": "Switch", "FSL": "Switch", "FSLL": "Switch", "HS": "HandSwitch", "XA": "Alarm", "UA": "Alarm",
    "AI": "Analyzer", "AT": "Analyzer", "AIC": "Controller", "BE": "Instrument", "BS": "Switch", "ZS": "Switch",
}
INSTRUMENT_TYPES = {"Instrument", "Controller", "ControlValve", "Switch", "Alarm", "Analyzer", "HandSwitch", "OnOffValve", "HandValve"}
CONTROL_PREFIX_RE = re.compile(r"^(?:P|T|F|L|A|PD|TD)(?:IC|RC|C|DIC|DRC)$")

# equipment class words used for named (untagged) equipment and alias harvesting
EQUIPMENT_WORDS = (
    "pump", "column", "tower", "heater", "furnace", "drum", "vessel", "exchanger", "desalter", "compressor", "stripper",
    "tank", "cooler", "condenser", "ejector", "reboiler", "accumulator", "separator", "filter", "fan", "blower", "stabilizer",
    "preheater", "aph", "air preheater", "flash drum", "reflux drum", "knock out drum", "ko drum", "boot", "header", "stack",
)
NAMED_EQUIPMENT_RE = re.compile(
    r"\b((?:(?:crude|atmospheric|atmos|vacuum|main|stabilizer|stabiliser|naphtha|kerosene|kero|diesel|rco|reduced crude|"
    r"pre-?flash|preflash|feed|charge|booster|reflux|overhead|bottom|bottoms|sour water|caustic|water wash|tempered water|"
    r"desalter|desalted|slop|flushing oil|fuel oil|fuel gas|steam|bfw|boiler feed water|lp|hp|mp|ejector|hot well|vgo|hvgo|lvgo|"
    r"slop wax|quench|pump ?around|pa|circulating reflux|cr|top|side)\s+){0,3}"
    r"(?:pump|column|tower|heater|furnace|drum|vessel|exchanger|desalter|compressor|stripper|tank|cooler|condenser|ejector|"
    r"reboiler|accumulator|separator|air preheater|preheater|stabilizer|stabiliser)s?)\b",
    re.IGNORECASE,
)
TAG_TOKEN = r"\d{1,3}\s*-\s*[A-Z]{1,4}\s*-\s*\d{1,5}(?:\s*[A-Z](?:\s*/\s*[A-Z])*)?"
NAME_PAREN_TAG_RE = re.compile(rf"([A-Za-z][A-Za-z0-9/&' \-]{{3,60}}?)\s*\(\s*({TAG_TOKEN})\s*\)")
TAG_PAREN_NAME_RE = re.compile(rf"({TAG_TOKEN})\s*\(\s*([A-Za-z][A-Za-z0-9/&' \-]{{3,60}}?)\s*\)")
_EQ_ALT = "|".join(w for w in EQUIPMENT_WORDS if " " not in w)
_STOP_ALT = r"(?:of|the|a|an|and|or|to|at|by|in|from|for|with|on|into|via|through|is|are|be|as|that|which|when|then)"
NAME_THEN_TAG_RE = re.compile(rf"\b((?:(?!{_STOP_ALT}\b)[A-Za-z\-/]+\s+){{0,3}}(?:{_EQ_ALT}))s?\s+({TAG_TOKEN})", re.IGNORECASE)
TAG_THEN_NAME_RE = re.compile(rf"({TAG_TOKEN})\s*[,:\-]?\s+(?!{_STOP_ALT}\b)((?:(?!{_STOP_ALT}\b)[A-Za-z\-/]+\s+){{0,3}}(?:{_EQ_ALT}))s?\b", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:!?])\s+|\n+")
GENERIC_NAME_WORDS = {"the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "by", "with", "from", "at", "is", "are", "be", "this", "that",
                      "each", "all", "any", "new", "old", "same", "other", "one", "two", "spare", "standby", "running", "into", "via", "through",
                      "as", "which", "when", "then", "also", "its", "their", "respective", "existing", "new", "corresponding"}
BAD_NAME_WORDS = {"cascaded", "cooled", "heated", "routed", "pumped", "sent", "taken", "provided", "connected", "line", "lines", "outlet", "inlet",
                  "discharge", "suction", "bypass", "upstream", "downstream", "which", "where", "while", "before", "after", "during", "using", "used"}
# one-word names that denote a specific piece of equipment in a crude unit (a class word like "pump" never does)
SPECIFIC_SINGLE_WORDS = {"desalter", "stabilizer", "stabiliser", "preflash", "pre-flash", "aph", "prefractionator", "deaerator", "flare"}
VERB_LIKE = {"enter", "enters", "entering", "maintain", "maintains", "check", "checks", "open", "opens", "close", "closes", "start", "starts",
             "stop", "stops", "ensure", "keep", "keeps", "take", "takes", "route", "routes", "isolate", "commission", "provide", "provides",
             "install", "put", "run", "runs", "operate", "operates", "leaves", "leaving", "feeds", "joins", "goes", "flows", "returns", "return",
             "recycle", "recycles", "collect", "collects", "drain", "drains", "vent", "vents", "bypass", "bypasses", "protect", "protects",
             "see", "refer", "note", "confirm", "observe", "adjust", "increase", "reduce", "decrease", "raise", "lower", "switch", "line"}
TYPE_GROUPS = {
    "Column": "column", "Stripper": "column", "Vessel": "vessel", "Drum": "vessel", "Desalter": "vessel", "Separator": "vessel", "Tank": "tank",
    "Heater": "heater", "Pump": "pump", "Exchanger": "exchanger", "Cooler": "exchanger", "Condenser": "exchanger", "Reboiler": "exchanger",
    "Compressor": "compressor", "Ejector": "ejector", "Filter": "filter", "AirCooler": "exchanger", "Blower": "fan",
}
DESC_HEADER_RE = re.compile(r"descr|service|name|equipment|item|duty|function", re.IGNORECASE)


def clean_alias_phrase(text: str) -> str:
    """Trim a harvested equipment name to the meaningful noun phrase."""
    t = re.sub(r"\s+", " ", text.replace("’", "'")).strip(" -:,;.()")
    words = t.split()
    # drop leading stop/generic words and anything before the last stopword
    cut = 0
    for i, w in enumerate(words):
        if w.lower() in GENERIC_NAME_WORDS or w.lower() in BAD_NAME_WORDS:
            cut = i + 1
    words = words[cut:]
    while words and words[-1].lower() in GENERIC_NAME_WORDS:
        words.pop()
    return " ".join(words)


def norm_alias(text: str) -> str:
    t = text.lower().replace("–", "-").replace("—", "-")
    t = re.sub(r"[^a-z0-9/ \-]+", " ", t)
    t = re.sub(r"\s*-\s*", "-", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"s$", "", t) if len(t) > 4 and not t.endswith("ss") else t
    return t


def canonical_tag(text: str) -> str | None:
    ident = parse_tag(re.sub(r"\s*-\s*", "-", text.strip()).replace(" ", ""))
    return ident.canonical if ident else None


# --------------------------------------------------------------------------- index model
class DocumentIndex(BaseModel):
    info: DocumentInfo
    entities: dict[str, EntityRecord] = Field(default_factory=dict)
    alias_index: dict[str, list[str]] = Field(default_factory=dict)      # norm alias -> [uid]
    claims: list[ClaimRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
    procedures: dict[str, ProcedureRecord] = Field(default_factory=dict)
    chunks: dict[str, ChunkRecord] = Field(default_factory=dict)
    chunk_order: list[str] = Field(default_factory=list)
    chunk_entities: dict[str, list[str]] = Field(default_factory=dict)   # chunk_id -> [uid]
    sections: dict[str, SectionRecord] = Field(default_factory=dict)
    glossary: list[GlossaryRecord] = Field(default_factory=list)
    document_references: list[DocumentReferenceRecord] = Field(default_factory=list)
    standing_instructions: list[StandingInstructionRecord] = Field(default_factory=list)
    cross_references: list[CrossReferenceRecord] = Field(default_factory=list)
    profile: dict = Field(default_factory=dict)
    chapters: list[dict] = Field(default_factory=list)
    build_stats: dict = Field(default_factory=dict)


@dataclass
class _EntityDraft:
    uid: str
    name: str
    canonical_tag: str | None
    entity_type: str | None
    aliases: set[str] = field(default_factory=set)
    pages: set[int] = field(default_factory=set)
    mentions: int = 0
    description: str | None = None
    name_votes: Counter = field(default_factory=Counter)


class IndexBuilder:
    def __init__(self, document_id: str, normalized: NormalizedDocument, chunks: list[Chunk],
                 tables: list[ParsedTable] | None, profile: DocumentProfile | None, glossary: DocumentGlossary | None,
                 table_classes: dict[str, str] | None = None, origin: str = "knowledge_layer", authority_rank: int = 60) -> None:
        self.doc_id = document_id
        self.normalized = normalized
        self.chunks = chunks
        self.tables_by_id = {t.table_id: t for t in (tables or [])}
        self.profile = profile
        self.glossary = glossary
        self.table_classes = table_classes or {}
        self.origin = origin
        self.authority_rank = authority_rank
        self.drafts: dict[str, _EntityDraft] = {}
        self.alias_index: dict[str, set[str]] = defaultdict(set)
        self.stats: Counter = Counter()

    # ------------------------------------------------------------------ entities
    def _type_for_tag(self, canonical: str) -> str | None:
        ident = parse_tag(canonical)
        if not ident:
            return None
        return PREFIX_TYPES.get(ident.prefix.upper(), "Equipment")

    def _tag_entity(self, tag_text: str, page: int | None = None, mention: bool = True) -> _EntityDraft | None:
        canonical = canonical_tag(tag_text)
        if not canonical:
            return None
        uid = entity_uid(canonical)
        d = self.drafts.get(uid)
        if d is None:
            d = _EntityDraft(uid=uid, name=canonical, canonical_tag=canonical, entity_type=self._type_for_tag(canonical))
            self.drafts[uid] = d
            ident = parse_tag(canonical)
            aliases = {canonical, canonical.replace("-", " "), canonical.replace("-", "")}
            if ident:
                aliases.add(f"{ident.prefix}-{ident.number}" + ("".join(ident.suffixes) if len(ident.suffixes) == 1 else ""))
                for child in ident.children:
                    aliases.add(child)
                if ident.suffixes and len(ident.suffixes) > 1:
                    aliases.add(canonical + "".join(f"/{s}" for s in ident.suffixes))
                    aliases.add(canonical + ident.suffixes[0] + "/" + "/".join(ident.suffixes[1:]))
            for a in aliases:
                self.alias_index[norm_alias(a)].add(uid)
                d.aliases.add(a)
        if page:
            d.pages.add(int(page))
        if mention:
            d.mentions += 1
        return d

    def _name_entity(self, name: str, page: int | None = None, entity_type: str | None = None) -> _EntityDraft | None:
        key = norm_alias(name)
        if not key or len(key) < 4 or key in GENERIC_NAME_WORDS:
            return None
        uid = document_scoped_uid(key, self.doc_id)
        d = self.drafts.get(uid)
        if d is None:
            d = _EntityDraft(uid=uid, name=name.strip(), canonical_tag=None, entity_type=entity_type or self._type_for_name(name))
            self.drafts[uid] = d
            d.aliases.add(name.strip())
            self.alias_index[key].add(uid)
        if page:
            d.pages.add(int(page))
        d.mentions += 1
        d.name_votes[name.strip()] += 1
        return d

    @staticmethod
    def _type_for_name(name: str) -> str | None:
        n = name.lower()
        for word, typ in (("pump", "Pump"), ("column", "Column"), ("tower", "Column"), ("heater", "Heater"), ("furnace", "Heater"),
                          ("desalter", "Desalter"), ("drum", "Drum"), ("vessel", "Vessel"), ("exchanger", "Exchanger"),
                          ("compressor", "Compressor"), ("stripper", "Stripper"), ("tank", "Tank"), ("cooler", "Cooler"),
                          ("condenser", "Condenser"), ("ejector", "Ejector"), ("reboiler", "Reboiler"), ("stabili", "Column"),
                          ("preheater", "Exchanger"), ("separator", "Separator"), ("filter", "Filter")):
            if word in n:
                return typ
        return None

    def _link_alias(self, tag_text: str, name: str, page: int | None) -> None:
        """Name <-> tag co-occurrence: make the name an alias of the tagged entity."""
        d = self._tag_entity(tag_text, page, mention=False)
        if d is None:
            return
        clean = clean_alias_phrase(name)
        words = clean.lower().split()
        if not words or len(words) > 5:
            return
        if len(words) == 1 and words[0] not in SPECIFIC_SINGLE_WORDS:
            return
        if any(w in BAD_NAME_WORDS or w in VERB_LIKE for w in words):
            return
        if any(w.endswith("ed") and w not in ("feed", "reduced", "bed", "fired") for w in words):
            return
        if not (words[-1].rstrip("s") in EQUIPMENT_WORDS or words[-1] in EQUIPMENT_WORDS):
            return
        # the phrase's equipment class must agree with the tag prefix class ("overhead condenser 11-C-01" is not the column)
        name_type = self._type_for_name(clean)
        tag_group = TYPE_GROUPS.get(d.entity_type or "")
        name_group = TYPE_GROUPS.get(name_type or "")
        if tag_group and name_group and tag_group != name_group:
            return
        d.aliases.add(clean)
        d.name_votes[clean.lower()] += 1
        self.alias_index[norm_alias(clean)].add(d.uid)
        self.stats["aliases_linked"] += 1

    def _harvest_text(self, text: str, page: int | None) -> list[str]:
        """Find tags and named equipment in text; return uids mentioned."""
        uids: list[str] = []
        for tag in find_tags(text):
            d = self._tag_entity(tag, page)
            if d:
                uids.append(d.uid)
        for rx, tag_group, name_group in ((NAME_PAREN_TAG_RE, 2, 1), (TAG_PAREN_NAME_RE, 1, 2), (NAME_THEN_TAG_RE, 2, 1), (TAG_THEN_NAME_RE, 1, 2)):
            for m in rx.finditer(text):
                self._link_alias(m.group(tag_group), m.group(name_group), page)
        for m in NAMED_EQUIPMENT_RE.finditer(text):
            phrase = m.group(1)
            words = phrase.lower().split()
            if len(words) >= 2 or words[0] in SPECIFIC_SINGLE_WORDS:   # single class words ("pump") are not entities
                d = self._name_entity(phrase, page)
                if d:
                    uids.append(d.uid)
        return list(dict.fromkeys(uids))

    def _harvest_equipment_tables(self) -> None:
        """Equipment / instrument list tables: a tag column plus a description column."""
        for table in self.tables_by_id.values():
            grid = table.grid or []
            if not grid or table.num_cols < 2:
                continue
            cols = list(zip(*[row + [""] * (table.num_cols - len(row)) for row in grid]))
            tag_col = None
            best = 0
            for ci, col in enumerate(cols):
                n = sum(1 for cell in col if canonical_tag(cell or "") is not None)
                if n > best and n >= max(2, len(col) // 3):
                    best, tag_col = n, ci
            if tag_col is None:
                continue
            text_cols = [ci for ci, col in enumerate(cols) if ci != tag_col and sum(1 for c in col if c and len(c) > 6 and not re.fullmatch(r"[\d.,\s%/-]+", c)) >= len(col) // 3]
            if not text_cols:
                continue
            header = [(c or "") for c in grid[0]] if grid else []
            by_header = [ci for ci in text_cols if ci < len(header) and DESC_HEADER_RE.search(header[ci])]
            if by_header:
                desc_col = by_header[0]
            else:
                # a section/category column repeats values; the description column is the most distinct one
                def distinct_ratio(ci: int) -> float:
                    vals = [c for c in cols[ci] if c]
                    return len(set(vals)) / max(1, len(vals))
                desc_col = max(text_cols, key=distinct_ratio)
                if distinct_ratio(desc_col) < 0.6:
                    continue
            for row in grid:
                if len(row) <= max(tag_col, desc_col):
                    continue
                tag, desc = row[tag_col], row[desc_col]
                if canonical_tag(tag or "") is None or not desc or len(desc) < 4:
                    continue
                d = self._tag_entity(tag, table.page, mention=False)
                if d is None:
                    continue
                desc_clean = re.sub(r"\s+", " ", desc).strip(" -:,;.")
                desc_type = self._type_for_name(desc_clean)
                tag_group = TYPE_GROUPS.get(d.entity_type or "")
                if tag_group and desc_type and TYPE_GROUPS.get(desc_type) and TYPE_GROUPS.get(desc_type) != tag_group:
                    continue
                if 3 < len(desc_clean) <= 80 and len(desc_clean.split()) <= 8 and desc_clean.lower() not in GENERIC_NAME_WORDS:
                    d.aliases.add(desc_clean)
                    d.name_votes[desc_clean.lower()] += 3          # table descriptions are the best names
                    self.alias_index[norm_alias(desc_clean)].add(d.uid)
                if not d.description:
                    d.description = desc_clean[:200]
                self.stats["table_entity_rows"] += 1

    # ------------------------------------------------------------------ merge names into tags
    def _merge_named_into_tagged(self, chunk_entities: dict[str, list[str]]) -> dict[str, str]:
        """An untagged name entity whose alias points at exactly one tagged entity is merged into it.

        "Atmospheric Column" (57 mentions) + alias link "ATMOS DISTILLATION COLUMN (11-C-01)" -> one entity.
        Ambiguous names (alias shared by several tags) stay separate and surface as candidates.
        """
        redirect: dict[str, str] = {}
        for uid, d in list(self.drafts.items()):
            if d.canonical_tag:
                continue
            key = norm_alias(d.name)
            tagged = [u for u in self.alias_index.get(key, set()) if u != uid and self.drafts[u].canonical_tag]
            if not tagged:
                continue
            if len(tagged) > 1:
                # several tags carry this name: merge only when one clearly dominates (>= 3x the mentions of the runner-up)
                ranked = sorted(tagged, key=lambda u: -self.drafts[u].mentions)
                top, second = self.drafts[ranked[0]].mentions, self.drafts[ranked[1]].mentions
                if top < 3 or top < 3 * max(second, 1):
                    continue
                tagged = [ranked[0]]
            target = self.drafts[tagged[0]]
            target.aliases.update(d.aliases)
            target.pages.update(d.pages)
            target.mentions += d.mentions
            for n, v in d.name_votes.items():
                target.name_votes[n] += v
            redirect[uid] = target.uid
            del self.drafts[uid]
            self.stats["merged_named_entities"] += 1
        # second pass: an untagged multi-word name that is the tail of exactly one tagged entity's alias
        # ("feed pump" <- "crude feed pump (11-PM-01)") merges into that entity when the types agree
        tagged_tails: dict[str, set[str]] = defaultdict(set)
        for uid, d in self.drafts.items():
            if not d.canonical_tag:
                continue
            for a in d.aliases:
                words = norm_alias(a).split()
                for i in range(1, len(words)):
                    tail = " ".join(words[i:])
                    if len(tail.split()) >= 2:
                        tagged_tails[tail].add(uid)
        for uid, d in list(self.drafts.items()):
            if d.canonical_tag or uid in redirect:
                continue
            key = norm_alias(d.name)
            owners = tagged_tails.get(key, set())
            if len(owners) != 1:
                continue
            target = self.drafts[next(iter(owners))]
            if TYPE_GROUPS.get(d.entity_type or "") and TYPE_GROUPS.get(target.entity_type or "") and TYPE_GROUPS[d.entity_type] != TYPE_GROUPS[target.entity_type]:
                continue
            target.aliases.update(d.aliases)
            target.pages.update(d.pages)
            target.mentions += d.mentions                         # aliases and mentions merge; the name votes do not ("feed pump" must not rename "crude feed pump")
            redirect[uid] = target.uid
            del self.drafts[uid]
            self.stats["merged_tail_entities"] += 1
        if redirect:
            for key, uids in self.alias_index.items():
                self.alias_index[key] = {redirect.get(u, u) for u in uids}
            for cid, uids in chunk_entities.items():
                chunk_entities[cid] = list(dict.fromkeys(redirect.get(u, u) for u in uids))
        return redirect

    # ------------------------------------------------------------------ claims
    def _build_claims(self) -> list[EngineeringClaim]:
        claims: list[EngineeringClaim] = []
        contexts = build_table_contexts(self.normalized, self.tables_by_id or None) if self.tables_by_id else {}
        for c in self.chunks:
            if not c.is_engineering:
                continue
            try:
                claims.extend(extract_spec_claims(c.elements, c.chunk_id, self.doc_id, c.section_path, c.parent_heading))
            except Exception as exc:  # defensive: one bad chunk must not kill the index
                logger.debug("spec claims failed for %s: %s", c.chunk_id, exc)
            try:
                claims.extend(extract_prose_claims(c.text, c.chunk_id, self.doc_id, c.section_path, c.page_start, c.parent_heading))
            except Exception as exc:
                logger.debug("prose claims failed for %s: %s", c.chunk_id, exc)
            for tid in dict.fromkeys(c.table_ids):
                tbl = self.tables_by_id.get(tid)
                if tbl is None:
                    continue
                ctx = contexts.get(tid)
                inherit = self.tables_by_id.get(ctx.continuation_of) if ctx is not None and ctx.continuation_of else None
                try:
                    claims.extend(extract_table_claims(
                        tbl, c.chunk_id, self.doc_id, c.section_path, subject_hint=c.parent_heading or None,
                        classification=self.table_classes.get(tid), context=ctx, inherit_from=inherit,
                    ))
                except Exception as exc:
                    logger.debug("table claims failed for %s/%s: %s", c.chunk_id, tid, exc)
        return claims

    def _resolve_subject(self, subject: str, page: int | None) -> str | None:
        tag = canonical_tag(subject)
        if tag:
            d = self._tag_entity(tag, page, mention=False)
            return d.uid if d else None
        key = norm_alias(subject)
        hits = self.alias_index.get(key)
        if hits:
            return sorted(hits)[0]
        d = self._name_entity(subject, page)
        return d.uid if d else None

    # ------------------------------------------------------------------ relations
    def _build_relations(self, chunk_records: dict[str, ChunkRecord]) -> list[RelationRecord]:
        known: set[str] = set()
        for d in self.drafts.values():
            known.update(a for a in d.aliases if len(a) >= 3)
        if self.glossary:
            for e in self.glossary.entries:
                known.add(e.term)
                if e.full_form:
                    known.add(e.full_form)
        out: list[RelationRecord] = []
        for c in self.chunks:
            if not c.is_engineering:
                continue
            names = set(known)
            names.update(find_tags(c.text))
            try:
                rels = extract_rule_relationships(c.text, c.chunk_id, self.doc_id, c.page_start, c.section_path, names)
            except Exception as exc:
                logger.debug("rule relations failed for %s: %s", c.chunk_id, exc)
                continue
            for r in rels:
                s_uid = self._resolve_subject(r.subject, r.page)
                o_uid = self._resolve_subject(r.object, r.page)
                if not s_uid or not o_uid or s_uid == o_uid:
                    continue
                out.append(RelationRecord(
                    source_uid=s_uid, source_name=r.subject, target_uid=o_uid, target_name=r.object,
                    rel_type=r.predicate.value, document_id=self.doc_id, page=r.page, chunk_id=r.chunk_id,
                    evidence=r.evidence, source="rule", confidence=r.confidence,
                ))
        out.extend(self._instrument_relations(chunk_records))
        out.extend(self._description_relations())
        return out

    def _description_relations(self) -> list[RelationRecord]:
        """Instrument-tag tables ("LI-2201 | 12-C-01 bottom level"): the description names the equipment -> MONITORS/CONTROLS."""
        out: list[RelationRecord] = []
        for d in list(self.drafts.values()):
            if d.entity_type not in INSTRUMENT_TYPES or not d.description:
                continue
            desc = d.description
            targets: list[str] = []
            for tag in find_tags(desc):
                t = self._tag_entity(tag, None, mention=False)
                if t and t.uid != d.uid and t.entity_type not in INSTRUMENT_TYPES:
                    targets.append(t.uid)
            for m in NAMED_EQUIPMENT_RE.finditer(desc):
                key = norm_alias(m.group(1))
                for uid in self.alias_index.get(key, set()):
                    if uid != d.uid and self.drafts[uid].entity_type not in INSTRUMENT_TYPES:
                        targets.append(uid)
            ident = parse_tag(d.canonical_tag or "")
            rel = "CONTROLS" if (ident and CONTROL_PREFIX_RE.match(ident.prefix.upper())) or d.entity_type == "ControlValve" else "MONITORS"
            for uid in dict.fromkeys(targets):
                out.append(RelationRecord(source_uid=d.uid, source_name=d.canonical_tag or d.name, target_uid=uid, target_name=self.drafts[uid].name, rel_type=rel,
                                          document_id=self.doc_id, page=(min(d.pages) if d.pages else None), evidence=f"{d.canonical_tag}: {desc}", source="description", confidence=0.85))
                self.stats["description_relations"] += 1
        return out

    def _instrument_relations(self, chunk_records: dict[str, ChunkRecord]) -> list[RelationRecord]:
        """Instrument tag in the same sentence as equipment -> MONITORS / CONTROLS (heuristic, marked as such)."""
        out: list[RelationRecord] = []
        seen: set[tuple[str, str, str]] = set()
        for c in self.chunks:
            if not c.is_engineering:
                continue
            for sent in SENTENCE_SPLIT_RE.split(c.text):
                if len(sent) > 400:
                    continue
                tags = find_tags(sent)
                if len(tags) < 2 and not NAMED_EQUIPMENT_RE.search(sent):
                    continue
                instruments = [t for t in tags if (self._type_for_tag(t) in INSTRUMENT_TYPES)]
                if not instruments:
                    continue
                equipment_uids: list[str] = []
                for t in tags:
                    if self._type_for_tag(t) not in INSTRUMENT_TYPES:
                        d = self._tag_entity(t, c.page_start, mention=False)
                        if d:
                            equipment_uids.append(d.uid)
                for m in NAMED_EQUIPMENT_RE.finditer(sent):
                    if len(m.group(1).split()) >= 2:
                        hits = self.alias_index.get(norm_alias(m.group(1)))
                        if hits:
                            equipment_uids.append(sorted(hits)[0])
                for inst in instruments:
                    d_i = self._tag_entity(inst, c.page_start, mention=False)
                    if d_i is None:
                        continue
                    inst_pos = sent.find(inst.split('-')[-1])
                    inst_ident = parse_tag(inst)
                    ident = parse_tag(inst)
                    rel = "CONTROLS" if ident and CONTROL_PREFIX_RE.match(ident.prefix.upper()) else "MONITORS"
                    if self._type_for_tag(inst) == "ControlValve":
                        rel = "CONTROLS"
                    for eq in dict.fromkeys(equipment_uids):
                        if eq == d_i.uid:
                            continue
                        eq_tag = self.drafts[eq].canonical_tag or ''
                        eq_ident = parse_tag(eq_tag) if eq_tag else None
                        if inst_ident and eq_ident and inst_ident.plant and eq_ident.plant and inst_ident.plant != eq_ident.plant:
                            continue                                  # 12-xxx instruments do not belong to 11-xxx equipment
                        low_sent = sent.lower()
                        eq_pos = min([low_sent.find(a.lower()) for a in self.drafts[eq].aliases if a and low_sent.find(a.lower()) >= 0] or [-1])
                        if eq_pos < 0 or inst_pos < 0 or abs(eq_pos - inst_pos) > 160:
                            continue                                  # not close enough in the sentence to be about each other
                        key = (d_i.uid, eq, rel)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append(RelationRecord(
                            source_uid=d_i.uid, source_name=inst, target_uid=eq, target_name=self.drafts[eq].name,
                            rel_type=rel, document_id=self.doc_id, page=c.page_start, chunk_id=c.chunk_id,
                            evidence=sent.strip()[:400], source="heuristic", confidence=0.5,
                        ))
        return out

    # ------------------------------------------------------------------ build
    def build(self) -> DocumentIndex:
        n = self.normalized
        prof = self.profile
        info = DocumentInfo(
            document_id=self.doc_id,
            title=(prof.title if prof else "") or self.doc_id,
            document_type=(prof.document_type.value if prof and hasattr(prof.document_type, "value") else (prof.document_type if prof else "")) or "",
            revision=(prof.revision if prof else None) or (n.chapters[0].revision if n.chapters else None),
            effective_date=prof.effective_date if prof else None,
            unit=(prof.unit or prof.plant) if prof else None,
            total_pages=n.total_pages,
            authority_rank=self.authority_rank,
            origin=self.origin,
        )
        # sections
        sections = {
            s.section_id: SectionRecord(
                section_id=s.section_id, title=s.title, path=s.path, level=s.level, chapter_number=s.chapter_number,
                page_start=s.page_start, page_end=s.page_end, document_id=self.doc_id,
            )
            for s in n.sections
        }
        # chunks + entity harvesting from text
        chunk_records: dict[str, ChunkRecord] = {}
        chunk_entities: dict[str, list[str]] = {}
        for c in self.chunks:
            chunk_records[c.chunk_id] = ChunkRecord(
                chunk_id=c.chunk_id, document_id=self.doc_id, chunk_type=c.chunk_type, text=c.text,
                page_start=c.page_start, page_end=c.page_end, section_path=c.section_path, section_id=c.section_id,
                chapter_number=c.chapter_number, procedure_ids=list(c.procedure_ids), table_ids=list(c.table_ids),
            )
            chunk_entities[c.chunk_id] = self._harvest_text(c.text, c.page_start) if c.is_engineering else []
        self._harvest_equipment_tables()
        self._merge_named_into_tagged(chunk_entities)
        # procedures
        procedures: dict[str, ProcedureRecord] = {}
        chunk_by_proc: dict[str, list[str]] = defaultdict(list)
        for c in self.chunks:
            for pid in c.procedure_ids:
                chunk_by_proc[pid].append(c.chunk_id)
        for p in n.procedures:
            applies = []
            for t in p.applies_to:
                d = self._tag_entity(t, p.page_start, mention=False)
                if d:
                    applies.append(d.uid)
            steps = []
            for s in p.steps:
                tag_uids = []
                for t in s.tags:
                    d = self._tag_entity(t, s.page, mention=False)
                    if d:
                        tag_uids.append(d.canonical_tag or d.name)
                steps.append(StepRecord(sequence=s.sequence, text=s.text, page=s.page, element_id=s.element_id, tags=tag_uids))
            ptype = p.procedure_type.value if hasattr(p.procedure_type, "value") else str(p.procedure_type)
            procedures[p.procedure_id] = ProcedureRecord(
                procedure_id=p.procedure_id, title=p.title.strip(), procedure_type=ptype, document_id=self.doc_id,
                chapter_number=p.chapter_number, section_id=p.section_id, section_path=p.section_path,
                page_start=p.page_start, page_end=p.page_end, applies_to=applies, steps=steps,
                chunk_ids=chunk_by_proc.get(p.procedure_id, []),
            )
        # claims
        raw_claims = self._build_claims()
        claims: list[ClaimRecord] = []
        for cl in raw_claims:
            subj_uid = self._resolve_subject(cl.subject, cl.page)
            claims.append(ClaimRecord(
                claim_id=cl.claim_id, subject_uid=subj_uid, subject=cl.subject, predicate=cl.predicate.value,
                value=cl.value, numeric_value=cl.value_numeric, unit=cl.unit_normalized or cl.unit or None,
                parameter_role=cl.parameter_role or None, location=cl.location or None, operating_mode=cl.operating_mode or None,
                scenario=cl.scenario or None, pressure_basis=cl.pressure_basis or None, temporal_status=cl.temporal_status or None,
                qualifier=cl.qualifier or None, context_key=cl.context_key(), document_id=self.doc_id, page=cl.page,
                chunk_id=cl.chunk_id or None, section_path=cl.section or None, evidence=cl.evidence, source=cl.source,
                revision=info.revision, confidence=cl.confidence,
            ))
        # relations (after entities exist)
        relations = self._build_relations(chunk_records)
        # finalize entities
        entities: dict[str, EntityRecord] = {}
        for uid, d in self.drafts.items():
            name = d.canonical_tag or d.name
            if d.name_votes:
                best_name, _votes = d.name_votes.most_common(1)[0]
                display = best_name.title() if best_name.isupper() or best_name.islower() else best_name
                display = re.sub(r"\b(Rco|Lpg|Bh|Pg|Aph|Bfw|Vgo|Hvgo|Lvgo|Sko|Atf|Fd|Id|Dm|Cw|Lp|Hp|Mp)\b", lambda m: m.group(1).upper(), display)
                name = f"{display} ({d.canonical_tag})" if d.canonical_tag else display
            entities[uid] = EntityRecord(
                entity_uid=uid, name=name, canonical_tag=d.canonical_tag, entity_type=d.entity_type,
                aliases=sorted(a for a in d.aliases if a)[:40], document_ids=[self.doc_id], description=d.description,
                mention_count=d.mentions, pages=sorted(d.pages)[:200],
            )
        alias_index = {k: sorted(v) for k, v in self.alias_index.items()}
        glossary = [
            GlossaryRecord(term=e.term, meaning=e.canonical_meaning, abbreviation=e.abbreviation or None, aliases=list(e.aliases),
                           page=e.page, document_id=self.doc_id)
            for e in (self.glossary.entries if self.glossary else [])
        ]
        doc_refs = [DocumentReferenceRecord(reference_text=r.reference_text, document_type=r.document_type, document_number=r.document_number,
                                            title=r.title, page=r.page, evidence=r.evidence, present_in_corpus=r.present_in_corpus,
                                            document_id=self.doc_id) for r in (prof.referenced_documents if prof else [])]
        sis = [StandingInstructionRecord(number=s.number, title=s.title, issue_date=s.issue_date or None, status=s.status or None,
                                         incorporated_in_chapter=s.incorporated_in_chapter or None, remark=s.remark or None, page=s.page or None,
                                         document_id=self.doc_id) for s in (prof.standing_instructions if prof else [])]
        xrefs = [CrossReferenceRecord(source_section=x.source_section, source_page=x.source_page or None, target_kind=x.target_kind,
                                      target_number=x.target_number, evidence=x.evidence, document_id=self.doc_id)
                 for x in (prof.cross_references if prof else [])]
        stats = dict(self.stats)
        stats.update({"entities": len(entities), "claims": len(claims), "relations": len(relations), "procedures": len(procedures),
                      "chunks": len(chunk_records), "sections": len(sections), "tagged_entities": sum(1 for e in entities.values() if e.canonical_tag)})
        logger.info("index %s: %s", self.doc_id, stats)
        return DocumentIndex(
            info=info, entities=entities, alias_index=alias_index, claims=claims, relations=relations, procedures=procedures,
            chunks=chunk_records, chunk_order=[c.chunk_id for c in self.chunks], chunk_entities=chunk_entities, sections=sections,
            glossary=glossary, document_references=doc_refs, standing_instructions=sis, cross_references=xrefs,
            profile=prof.model_dump(mode="json") if prof else {},
            chapters=[ch.model_dump(mode="json") for ch in n.chapters], build_stats=stats,
        )


def build_document_index(document_id: str, normalized: NormalizedDocument, chunks: list[Chunk], tables: list[ParsedTable] | None,
                         profile: DocumentProfile | None, glossary: DocumentGlossary | None, table_classes: dict[str, str] | None = None,
                         origin: str = "knowledge_layer", authority_rank: int = 60) -> DocumentIndex:
    return IndexBuilder(document_id, normalized, chunks, tables, profile, glossary, table_classes, origin, authority_rank).build()

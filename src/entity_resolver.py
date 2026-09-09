"""Entity resolution: decide which graph node an extracted entity refers to.

Resolution order (first hit wins):
  1. exact canonical tag            -> global UID, automatic merge
  2. glossary term / abbreviation   -> candidate SAME_AS (never auto-merge)
  3. name / canonical_name / alias match in the same plant/unit -> candidate SAME_AS
  4. vector search over Chunk embeddings -> entities mentioned in similar chunks
     whose names are near-identical -> candidate SAME_AS

Only an exact canonical tag merges automatically.  Every other match creates
the entity as a document-scoped node and records
(:Entity)-[:SAME_AS {confidence, method, evidence}]->(:Entity) when the score
reaches ``config.identity.same_as_threshold``; below it nothing is recorded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from schemas.glossary import DocumentGlossary
from src.config import PipelineConfig
from src.entity_identity import TagIdentity, document_scoped_uid, entity_uid, identify_tag

logger = logging.getLogger(__name__)


@dataclass
class SameAsCandidate:
    uid: str
    name: str
    confidence: float
    method: str
    evidence: str = ""


@dataclass
class Resolution:
    uid: str
    method: str                                  # tag | glossary | name_match | vector | document_scoped
    canonical_tag: str | None = None
    canonical_name: str = ""
    identity: TagIdentity | None = None
    matched_existing: dict | None = None
    same_as: list[SameAsCandidate] = field(default_factory=list)


def _name_similarity(a: str, b: str) -> float:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


class EntityResolver:
    """Resolves extracted entity names to graph UIDs."""

    def __init__(
        self,
        config: PipelineConfig,
        memory=None,
        glossary: DocumentGlossary | None = None,
        plant: str | None = None,
        unit: str | None = None,
    ):
        self.config = config
        self.memory = memory
        self.glossary = glossary
        self.plant = plant or None
        self.unit = unit or None
        self._glossary_index: dict[str, str] = {}
        if glossary:
            for e in glossary.entries:
                for key in (e.term, e.abbreviation):
                    if key:
                        self._glossary_index[key.strip().lower()] = e.full_form or e.canonical_meaning
                if e.full_form:
                    self._glossary_index[e.full_form.strip().lower()] = e.full_form

    # ------------------------------------------------------------------ #
    def identify(self, name: str) -> TagIdentity | None:
        return identify_tag(
            name, plant=self.plant, unit=self.unit,
            unit_scoping=self.config.identity.unit_scoping,
            patterns=self.config.identity.tag_patterns,
        )

    def resolve(
        self,
        name: str,
        document_id: str,
        canonical_name: str = "",
        aliases: list[str] | None = None,
        chunk_embedding: list[float] | None = None,
    ) -> Resolution:
        """Resolve one extracted entity name."""
        # 1. Canonical tag: try the name, then the canonical name, then aliases
        for candidate in [name, canonical_name, *(aliases or [])]:
            ident = self.identify(candidate) if candidate else None
            if ident:
                uid = entity_uid(ident.canonical)
                existing = None
                if self.memory is not None:
                    try:
                        existing = self.memory.find_entity_by_tag(ident.canonical)
                    except Exception as e:  # pragma: no cover
                        logger.debug(f"tag lookup failed: {e}")
                return Resolution(
                    uid=uid, method="tag", canonical_tag=ident.canonical,
                    canonical_name=canonical_name or name, identity=ident, matched_existing=existing,
                )

        # Untagged: document-scoped node, plus SAME_AS candidates
        uid = document_scoped_uid(name, document_id)
        res = Resolution(uid=uid, method="document_scoped", canonical_name=canonical_name or name)
        threshold = self.config.identity.same_as_threshold
        candidates: dict[str, SameAsCandidate] = {}

        def consider(cand: SameAsCandidate) -> None:
            if cand.uid == uid or cand.confidence < threshold:
                return
            prev = candidates.get(cand.uid)
            if prev is None or cand.confidence > prev.confidence:
                candidates[cand.uid] = cand

        # 2. Glossary term / abbreviation
        full_form = self._glossary_index.get(name.strip().lower()) or self._glossary_index.get(
            (canonical_name or "").strip().lower()
        )
        if full_form:
            res.canonical_name = full_form
            res.method = "glossary"
            if self.memory is not None:
                for e in self._safe(self.memory.find_entities_by_names_scoped, [full_form, name], self.plant, self.unit):
                    consider(SameAsCandidate(
                        uid=e["uid"], name=e["name"],
                        confidence=self.config.identity.glossary_match_confidence,
                        method="glossary", evidence=f"glossary: {name} = {full_form}",
                    ))

        if self.memory is not None:
            # 3. Name / canonical_name / alias match within the same plant/unit
            names = [n for n in {name, canonical_name, *(aliases or [])} if n]
            for e in self._safe(self.memory.find_entities_by_names_scoped, names, self.plant, self.unit):
                sim = max(_name_similarity(name, e["name"]), _name_similarity(canonical_name, e["canonical_name"]))
                conf = self.config.identity.name_match_confidence if sim >= 0.999 else round(sim * 0.9, 3)
                consider(SameAsCandidate(uid=e["uid"], name=e["name"], confidence=conf, method="name_match",
                                         evidence=f"name match: '{name}' ~ '{e['name']}'"))

            # 4. Vector search over chunk embeddings -> entities in similar chunks
            if chunk_embedding:
                sims = self._safe(self.memory.vector_search, chunk_embedding, self.config.identity.vector_candidates)
                chunk_ids = [c["chunk_id"] for c in sims if c.get("chunk_id")]
                scores = {c["chunk_id"]: float(c.get("score", 0.0)) for c in sims}
                for e in self._safe(self.memory.get_entities_in_chunks, chunk_ids):
                    sim = max(_name_similarity(name, e["name"]), _name_similarity(canonical_name, e["canonical_name"]),
                              max((_name_similarity(name, a) for a in e.get("aliases", [])), default=0.0))
                    if sim < 0.85:
                        continue
                    chunk_score = max(scores.values(), default=0.0)
                    consider(SameAsCandidate(uid=e["uid"], name=e["name"], confidence=round(sim * chunk_score, 3),
                                             method="vector", evidence=f"similar chunk; name sim {sim:.2f}"))

        res.same_as = sorted(candidates.values(), key=lambda c: -c.confidence)[:5]
        if res.same_as and res.method == "document_scoped":
            res.method = res.same_as[0].method
        return res

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args) or []
        except Exception as e:  # pragma: no cover
            logger.debug(f"resolver lookup failed ({fn.__name__}): {e}")
            return []

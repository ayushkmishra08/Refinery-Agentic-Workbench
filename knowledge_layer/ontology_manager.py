"""Corpus-driven ontology manager with an open, governed vocabulary.

EntityType / RelationshipType / ClaimCategory remain the *preferred* labels
that the prompt advertises.  Labels the model produces outside those lists are
no longer rejected: the extractor stores the raw label (``entity_type_raw``,
``predicate_raw``) and maps to OTHER / ASSOCIATED_WITH / generic_property.

This manager records how often each raw label occurs, per document and per
chunk.  A raw label seen in >= ``ontology_promote_min_documents`` documents
and >= ``ontology_promote_min_chunks`` chunks is *promoted*: it is written to
``data/knowledge/ontology.json`` and appended to
``prompts/entity_extraction.txt`` (inside marker comments) so the next run
advertises it to the model.  Every promotion is logged.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from knowledge_layer.schemas.claims import ClaimCategory
from knowledge_layer.schemas.knowledge import ChunkExtraction, EntityType, RelationshipType
from knowledge_layer.schemas.ontology import (
    OntologyClaimPredicate,
    OntologyDomain,
    OntologyRelationType,
    RefineryOntology,
)
from knowledge_layer.config import PipelineConfig

logger = logging.getLogger(__name__)

PROMO_START = "<!-- DOCUMENT-DISCOVERED VOCABULARY (managed by OntologyManager, do not edit) -->"
PROMO_END = "<!-- END DOCUMENT-DISCOVERED VOCABULARY -->"

_KNOWN_ENTITY = {e.value for e in EntityType}
_KNOWN_REL = {r.value for r in RelationshipType}
_KNOWN_CLAIM = {c.value for c in ClaimCategory}


def _norm_label(label: str, upper: bool = False) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", (label or "").strip()).strip("_")
    return text.upper() if upper else text.lower()


class OntologyManager:
    """Manages the corpus-driven refinery ontology and raw-label governance."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._ontology_path = config.paths.knowledge_dir / "ontology.json"
        self._prompt_path = config.paths.prompts_dir / "entity_extraction.txt"
        self.ontology = self._load_or_create()
        # raw label -> {"kind", "documents": set, "chunks": set, "mapped_to"}
        self._raw: dict[str, dict] = {}
        for item in self.ontology.raw_label_stats:
            self._raw[item["label"]] = {
                "kind": item["kind"],
                "documents": set(item.get("documents", [])),
                "chunks": set(item.get("chunks", [])),
                "mapped_to": item.get("mapped_to", ""),
            }

    def _load_or_create(self) -> RefineryOntology:
        if self._ontology_path.exists():
            try:
                data = json.loads(self._ontology_path.read_text(encoding="utf-8"))
                return RefineryOntology.model_validate(data)
            except Exception as e:
                logger.warning(f"Could not load ontology: {e}. Creating new.")
        return RefineryOntology()

    # ------------------------------------------------------------------ #
    def update_from_extraction(self, extraction: ChunkExtraction) -> None:
        """Update ontology (preferred-label usage + raw label frequencies)."""
        doc, chunk = extraction.document_id, extraction.chunk_id
        for entity in extraction.entities:
            self._record_entity(entity.entity_type.value, entity.domain.value)
            self._record_raw("entity_type", entity.entity_type_raw, entity.entity_type.value, doc, chunk, _KNOWN_ENTITY)
        for rel in extraction.relationships:
            self._record_relationship(
                rel.predicate.value,
                getattr(rel.subject_type, "value", "") if rel.subject_type else "",
                getattr(rel.object_type, "value", "") if rel.object_type else "",
            )
            self._record_raw("relationship", rel.predicate_raw, rel.predicate.value, doc, chunk, _KNOWN_REL, upper=True)
        for claim in extraction.claims:
            self._record_claim_predicate(claim.predicate.value, claim.unit)
            self._record_raw("claim_predicate", claim.predicate_raw, claim.predicate.value, doc, chunk, _KNOWN_CLAIM)

        self.ontology.total_entities_seen += len(extraction.entities)
        self.ontology.total_relationships_seen += len(extraction.relationships)
        self.ontology.total_claims_seen += len(extraction.claims)
        self.ontology.last_updated = datetime.now(timezone.utc).isoformat()

    def _record_raw(self, kind: str, raw: str, mapped_to: str, doc: str, chunk: str,
                    known: set[str], upper: bool = False) -> None:
        label = _norm_label(raw, upper=upper)
        if not label or label in known or label.lower() in known:
            return
        rec = self._raw.setdefault(label, {"kind": kind, "documents": set(), "chunks": set(), "mapped_to": mapped_to})
        rec["documents"].add(doc)
        rec["chunks"].add(chunk)
        if label not in self.ontology.unclassified_entity_types and kind == "entity_type":
            self.ontology.unclassified_entity_types.append(label)

    def _record_entity(self, entity_type: str, domain: str) -> None:
        for d in self.ontology.domains:
            if d.name == domain:
                if entity_type not in d.entity_types:
                    d.entity_types.append(entity_type)
                return
        self.ontology.domains.append(OntologyDomain(name=domain, entity_types=[entity_type]))

    def _record_relationship(self, rel_type: str, subject_type: str, object_type: str) -> None:
        for r in self.ontology.relationship_types:
            if r.name == rel_type:
                r.usage_count += 1
                if subject_type and subject_type not in r.valid_subject_types:
                    r.valid_subject_types.append(subject_type)
                if object_type and object_type not in r.valid_object_types:
                    r.valid_object_types.append(object_type)
                return
        self.ontology.relationship_types.append(OntologyRelationType(
            name=rel_type,
            valid_subject_types=[subject_type] if subject_type else [],
            valid_object_types=[object_type] if object_type else [],
            usage_count=1,
        ))

    def _record_claim_predicate(self, predicate: str, unit: str) -> None:
        for p in self.ontology.claim_predicates:
            if p.name == predicate:
                p.usage_count += 1
                return
        self.ontology.claim_predicates.append(OntologyClaimPredicate(name=predicate, usage_count=1))

    # ------------------------------------------------------------------ #
    def promote_labels(self) -> list[dict]:
        """Promote raw labels that meet the document/chunk thresholds. Returns new promotions."""
        min_docs = self.config.identity.ontology_promote_min_documents
        min_chunks = self.config.identity.ontology_promote_min_chunks
        already = {p["label"] for p in self.ontology.promoted_labels}
        new: list[dict] = []
        for label, rec in sorted(self._raw.items()):
            if label in already:
                continue
            if len(rec["documents"]) >= min_docs and len(rec["chunks"]) >= min_chunks:
                promo = {
                    "label": label,
                    "kind": rec["kind"],
                    "mapped_to": rec["mapped_to"],
                    "documents": sorted(rec["documents"]),
                    "chunk_count": len(rec["chunks"]),
                    "promoted_at": datetime.now(timezone.utc).isoformat(),
                }
                self.ontology.promoted_labels.append(promo)
                new.append(promo)
                logger.info(
                    f"ONTOLOGY PROMOTION: {rec['kind']} '{label}' seen in {len(rec['documents'])} documents / "
                    f"{len(rec['chunks'])} chunks -> added to prompt vocabulary"
                )
        if new:
            self._write_prompt_block()
        return new

    def _write_prompt_block(self) -> None:
        """Rewrite the managed vocabulary block in prompts/entity_extraction.txt."""
        if not self._prompt_path.exists():
            return
        text = self._prompt_path.read_text(encoding="utf-8")
        by_kind: dict[str, list[str]] = {"entity_type": [], "relationship": [], "claim_predicate": []}
        for p in self.ontology.promoted_labels:
            by_kind.setdefault(p["kind"], []).append(p["label"])
        lines = [PROMO_START,
                 "ADDITIONAL TYPES DISCOVERED IN THIS CORPUS (use them when they fit better than the lists above):"]
        if by_kind["entity_type"]:
            lines.append("  Entity types: " + ", ".join(sorted(set(by_kind["entity_type"]))))
        if by_kind["relationship"]:
            lines.append("  Relationship types: " + ", ".join(sorted(set(by_kind["relationship"]))))
        if by_kind["claim_predicate"]:
            lines.append("  Claim predicates: " + ", ".join(sorted(set(by_kind["claim_predicate"]))))
        lines.append(PROMO_END)
        block = "\n".join(lines)
        if PROMO_START in text and PROMO_END in text:
            start = text.index(PROMO_START)
            end = text.index(PROMO_END) + len(PROMO_END)
            text = text[:start] + block + text[end:]
        else:
            anchor = "SOURCE TEXT TO ANALYZE:"
            marker = "═══════════════════════════════════════════════════════════\n" + anchor
            if marker in text:
                text = text.replace(marker, block + "\n\n" + marker, 1)
            else:
                text = text.rstrip() + "\n\n" + block + "\n"
        self._prompt_path.write_text(text, encoding="utf-8")
        logger.info(f"Prompt vocabulary block updated: {self._prompt_path}")

    # ------------------------------------------------------------------ #
    def save(self) -> None:
        """Promote eligible labels, then save ontology to disk."""
        self.promote_labels()
        self.ontology.raw_label_stats = [
            {
                "label": label, "kind": rec["kind"], "mapped_to": rec["mapped_to"],
                "documents": sorted(rec["documents"]), "chunks": sorted(rec["chunks"]),
                "document_count": len(rec["documents"]), "chunk_count": len(rec["chunks"]),
            }
            for label, rec in sorted(self._raw.items())
        ]
        self._ontology_path.parent.mkdir(parents=True, exist_ok=True)
        self._ontology_path.write_text(self.ontology.model_dump_json(indent=2), encoding="utf-8")
        logger.info(
            f"Ontology saved: {len(self.ontology.domains)} domains, "
            f"{len(self.ontology.relationship_types)} relationship types, "
            f"{len(self._raw)} raw labels tracked, {len(self.ontology.promoted_labels)} promoted"
        )

    def increment_documents(self) -> None:
        self.ontology.total_documents_processed += 1

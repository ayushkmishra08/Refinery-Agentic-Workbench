"""Graph insertion — idempotent MERGE operations into Neo4j with global entity identity.

Entities are resolved through :class:`EntityResolver`:
  * a recognised asset tag ("11-V-02", "11 V 02", "10-P-01A/B") yields a global
    UID (md5 of the canonical tag) so the same asset from any document lands on
    one node, with per-document HAS_ENTITY and per-chunk MENTIONS provenance;
  * untagged entities keep a document-scoped UID and may receive SAME_AS links
    to probable matches (never merged automatically).

Claims stay per document/page; conflicting values are linked with
CONFLICTS_WITH instead of being overwritten.  All insertions use MERGE and are
idempotent.  On failure: log, count, continue.
"""

from __future__ import annotations

import hashlib
import logging

from schemas.claims import EngineeringClaim
from schemas.knowledge import ChunkExtraction, ExtractedEntity
from schemas.validation import ValidationResult
from src.entity_resolver import EntityResolver, Resolution
from src.memory import Neo4jMemory
from src.validator import normalize_unit

logger = logging.getLogger(__name__)


def _make_claim_uid(subject_uid: str, predicate: str, value: str, unit: str, document_id: str, page: int) -> str:
    """Deterministic UID for a claim: per subject entity, predicate, value, document and page."""
    key = f"{subject_uid}|{predicate}|{value.strip().lower()}|{unit.strip().lower()}|{document_id}|{page}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


class GraphInserter:
    """Inserts validated extraction results into Neo4j."""

    def __init__(self, memory: Neo4jMemory, resolver: EntityResolver | None = None):
        self.memory = memory
        self.resolver = resolver

    # ------------------------------------------------------------------ #
    def _resolve(self, name: str, document_id: str, canonical_name: str = "",
                 aliases: list[str] | None = None, chunk_embedding=None) -> Resolution:
        if self.resolver is None:
            from src.entity_identity import document_scoped_uid
            return Resolution(uid=document_scoped_uid(name, document_id), method="document_scoped",
                              canonical_name=canonical_name or name)
        return self.resolver.resolve(name, document_id, canonical_name=canonical_name,
                                     aliases=aliases, chunk_embedding=chunk_embedding)

    def _scope(self) -> dict:
        return {
            "plant": getattr(self.resolver, "plant", None) if self.resolver else None,
            "unit_scope": getattr(self.resolver, "unit", None) if self.resolver else None,
        }

    def _ensure_trains(self, res: Resolution, extraction: ChunkExtraction, entity_payload: dict) -> None:
        """For train lists (10-P-01A/B) create children; for a suffixed tag link its parent."""
        ident = res.identity
        if ident is None:
            return
        from src.entity_identity import entity_uid
        scope = self._scope()
        base = {
            "entity_type": entity_payload.get("entity_type", "unknown"),
            "domain": entity_payload.get("domain", "unknown"),
            "document_id": extraction.document_id,
            "page": entity_payload.get("page", 0),
            "section": extraction.section,
            "evidence": entity_payload.get("evidence", ""),
            "confidence": entity_payload.get("confidence", 0.0),
            "chunk_id": extraction.chunk_id,
            "plant": ident.plant or scope["plant"],
            "unit": scope["unit_scope"],
            "resolution_method": "tag",
        }
        parent = {"uid": res.uid, "name": entity_payload["name"], "canonical_name": entity_payload["canonical_name"],
                  "canonical_tag": ident.canonical, "is_stub": False, **base}
        for child_tag in ident.children:
            child = {"uid": entity_uid(child_tag), "name": child_tag.split("/")[-1], "canonical_name": child_tag,
                     "canonical_tag": child_tag, "is_stub": True, **base}
            self.memory.upsert_train_link(parent, child)
        if ident.parent:
            parent_node = {"uid": entity_uid(ident.parent), "name": ident.parent.split("/")[-1],
                           "canonical_name": ident.parent, "canonical_tag": ident.parent, "is_stub": True, **base}
            self.memory.upsert_train_link(parent_node, parent)

    # ------------------------------------------------------------------ #
    def insert_extraction(
        self,
        extraction: ChunkExtraction,
        validation: ValidationResult,
        chunk_embedding: list[float] | None = None,
    ) -> dict[str, int]:
        """Insert validated extraction into Neo4j (items with ERROR issues are skipped)."""
        counts = {"entities": 0, "relationships": 0, "claims": 0, "conflicts": 0, "same_as": 0, "errors": 0}
        rejected_ids = {issue.item_id for issue in validation.issues if issue.severity.value == "error"}
        doc_id = extraction.document_id
        scope = self._scope()

        # name (lower) -> resolution, so claims/relationships reuse the entity's UID
        resolved: dict[str, Resolution] = {}
        mentions: list[dict] = []
        inserted_claim_uids: list[str] = []

        def resolve_name(name: str, canonical_name: str = "", aliases=None) -> Resolution:
            key = name.strip().lower()
            if key not in resolved:
                resolved[key] = self._resolve(name, doc_id, canonical_name, aliases, chunk_embedding)
            return resolved[key]

        # -- entities -----------------------------------------------------
        for entity in extraction.entities:
            if entity.entity_id in rejected_ids:
                continue
            try:
                res = resolve_name(entity.name, entity.canonical_name, entity.aliases)
                entity.uid = res.uid
                entity.canonical_tag = res.canonical_tag
                entity.resolution_method = res.method
                payload = {
                    "uid": res.uid,
                    "name": entity.name,
                    "canonical_name": res.canonical_name or entity.canonical_name or entity.name,
                    "canonical_tag": res.canonical_tag,
                    "entity_type": entity.entity_type.value,
                    "entity_type_raw": entity.entity_type_raw,
                    "domain": entity.domain.value,
                    "aliases": list(entity.aliases),
                    "plant": (res.identity.plant if res.identity and res.identity.plant else scope["plant"]),
                    "unit": scope["unit_scope"],
                    "document_id": doc_id,
                    "page": entity.page,
                    "section": extraction.section,
                    "evidence": entity.evidence,
                    "confidence": entity.confidence,
                    "chunk_id": extraction.chunk_id,
                    "is_stub": False,
                    "resolution_method": res.method,
                    "grounding": entity.grounding,
                    "source": entity.source,
                }
                self.memory.upsert_entity(payload)
                self._ensure_trains(res, extraction, payload)
                for cand in res.same_as:
                    self.memory.upsert_same_as(res.uid, cand.uid, cand.confidence, cand.method, cand.evidence)
                    counts["same_as"] += 1
                mentions.append({"uid": res.uid, "evidence": entity.evidence, "confidence": entity.confidence})
                counts["entities"] += 1
            except Exception as e:
                logger.error(f"Failed to insert entity {entity.name}: {e}")
                counts["errors"] += 1

        # -- claims -----------------------------------------------------------
        for claim in extraction.claims:
            if claim.claim_id in rejected_ids:
                continue
            try:
                res = resolve_name(claim.subject)
                unit = normalize_unit(claim.unit)
                claim_uid = _make_claim_uid(res.uid, claim.predicate.value, claim.value, unit, doc_id, claim.page)
                claim.subject_uid = res.uid
                conflicts = self.memory.upsert_claim({
                    "uid": claim_uid,
                    "subject_uid": res.uid,
                    "subject_name": claim.subject,
                    "canonical_tag": res.canonical_tag,
                    "predicate": claim.predicate.value,
                    "predicate_raw": claim.predicate_raw,
                    "value": claim.value,
                    "unit": unit,
                    "unit_raw": claim.unit,
                    "claim_category": claim.predicate.value,
                    "document_id": doc_id,
                    "page": claim.page,
                    "evidence": claim.evidence,
                    "confidence": claim.confidence,
                    "source_text": claim.evidence[:500],
                    "chunk_id": extraction.chunk_id,
                    "grounding": claim.grounding,
                    "source": claim.source,
                    "is_from_table": claim.is_from_table,
                    "table_id": claim.table_id,
                    "sentence_index": claim.sentence_index,
                    **scope,
                })
                if conflicts:
                    claim.has_conflict = True
                    claim.conflicting_claim_ids = conflicts
                    counts["conflicts"] += len(conflicts)
                    logger.info(f"Claim conflict: {claim.subject} {claim.predicate.value}={claim.value} "
                                f"vs {len(conflicts)} existing claim(s)")
                inserted_claim_uids.append(claim_uid)
                counts["claims"] += 1
            except Exception as e:
                logger.error(f"Failed to insert claim: {e}")
                counts["errors"] += 1

        # -- relationships ------------------------------------------------------
        for rel in extraction.relationships:
            if rel.relationship_id in rejected_ids:
                continue
            try:
                s_res = resolve_name(rel.subject)
                o_res = resolve_name(rel.object)
                self.memory.upsert_relationship({
                    "subject_uid": s_res.uid,
                    "object_uid": o_res.uid,
                    "subject_name": rel.subject,
                    "object_name": rel.object,
                    "subject_tag": s_res.canonical_tag,
                    "object_tag": o_res.canonical_tag,
                    "predicate": rel.predicate.value,
                    "predicate_raw": rel.predicate_raw,
                    "evidence": rel.evidence,
                    "page": rel.page,
                    "document_id": doc_id,
                    "confidence": rel.confidence,
                    "chunk_id": extraction.chunk_id,
                    "grounding": rel.grounding,
                    "source": rel.source,
                    "sentence_index": rel.sentence_index,
                    **scope,
                })
                counts["relationships"] += 1
            except Exception as e:
                logger.error(f"Failed to insert relationship: {e}")
                counts["errors"] += 1

        # -- provenance ---------------------------------------------------------
        try:
            self.memory.link_chunk_provenance(extraction.chunk_id, mentions, inserted_claim_uids)
        except Exception as e:
            logger.debug(f"Chunk provenance link note: {e}")

        logger.info(
            f"Inserted from {extraction.chunk_id}: "
            f"{counts['entities']} entities, {counts['claims']} claims, "
            f"{counts['relationships']} relationships"
            + (f", {counts['same_as']} SAME_AS" if counts["same_as"] else "")
            + (f", {counts['conflicts']} conflicts" if counts["conflicts"] else "")
            + (f", {counts['errors']} errors" if counts["errors"] else "")
        )
        return counts

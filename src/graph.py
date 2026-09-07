"""Graph insertion — idempotent MERGE operations into Neo4j.

All insertions use MERGE to be idempotent. On failure: rollback, log, continue.
Validated extractions are inserted with full provenance linkage.
"""

from __future__ import annotations

import hashlib
import logging

from schemas.knowledge import ChunkExtraction, ExtractedEntity, ExtractedRelationship
from schemas.claims import EngineeringClaim
from schemas.validation import ValidationResult
from src.memory import Neo4jMemory

logger = logging.getLogger(__name__)


def _make_entity_uid(name: str, entity_type: str, document_id: str) -> str:
    """Create a deterministic UID for an entity."""
    key = f"{name.strip().lower()}|{entity_type}|{document_id}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _make_claim_uid(subject: str, predicate: str, value: str, document_id: str) -> str:
    """Create a deterministic UID for a claim."""
    key = f"{subject.strip().lower()}|{predicate}|{value}|{document_id}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


class GraphInserter:
    """Inserts validated extraction results into Neo4j."""

    def __init__(self, memory: Neo4jMemory):
        self.memory = memory

    def insert_extraction(
        self,
        extraction: ChunkExtraction,
        validation: ValidationResult,
    ) -> dict[str, int]:
        """Insert validated extraction into Neo4j.

        Only inserts items that passed validation (no ERROR-level issues).

        Args:
            extraction: LLM extraction output.
            validation: Validation results.

        Returns:
            Counts of inserted items.
        """
        counts = {
            "entities": 0,
            "relationships": 0,
            "claims": 0,
            "errors": 0,
        }

        # Collect rejected item IDs
        rejected_ids = {
            issue.item_id
            for issue in validation.issues
            if issue.severity.value == "error"
        }

        # Insert entities
        for entity in extraction.entities:
            if entity.entity_id in rejected_ids:
                continue
            try:
                uid = _make_entity_uid(
                    entity.name, entity.entity_type.value, extraction.document_id,
                )
                self.memory.upsert_entity({
                    "uid": uid,
                    "name": entity.name,
                    "canonical_name": entity.canonical_name or entity.name,
                    "entity_type": entity.entity_type.value,
                    "domain": entity.domain.value,
                    "document_id": extraction.document_id,
                    "page": entity.page,
                    "evidence": entity.evidence,
                    "confidence": entity.confidence,
                })
                counts["entities"] += 1
            except Exception as e:
                logger.error(f"Failed to insert entity {entity.name}: {e}")
                counts["errors"] += 1

        # Insert claims
        for claim in extraction.claims:
            if claim.claim_id in rejected_ids:
                continue
            try:
                subject_uid = _make_entity_uid(
                    claim.subject, "", extraction.document_id,
                )
                claim_uid = _make_claim_uid(
                    claim.subject, claim.predicate.value,
                    claim.value, extraction.document_id,
                )
                self.memory.upsert_claim({
                    "uid": claim_uid,
                    "subject_uid": subject_uid,
                    "predicate": claim.predicate.value,
                    "value": claim.value,
                    "unit": claim.unit,
                    "claim_category": claim.predicate.value,
                    "document_id": extraction.document_id,
                    "page": claim.page,
                    "evidence": claim.evidence,
                    "confidence": claim.confidence,
                    "source_text": claim.evidence[:500],
                })
                counts["claims"] += 1
            except Exception as e:
                logger.error(f"Failed to insert claim: {e}")
                counts["errors"] += 1

        # Insert relationships
        for rel in extraction.relationships:
            if rel.relationship_id in rejected_ids:
                continue
            try:
                subject_uid = _make_entity_uid(
                    rel.subject, "", extraction.document_id,
                )
                object_uid = _make_entity_uid(
                    rel.object, "", extraction.document_id,
                )
                self.memory.upsert_relationship({
                    "subject_uid": subject_uid,
                    "object_uid": object_uid,
                    "predicate": rel.predicate.value,
                    "evidence": rel.evidence,
                    "page": rel.page,
                    "document_id": extraction.document_id,
                    "confidence": rel.confidence,
                })
                counts["relationships"] += 1
            except Exception as e:
                logger.error(f"Failed to insert relationship: {e}")
                counts["errors"] += 1

        # Insert chunk node (for vector search later)
        try:
            self.memory.upsert_chunk({
                "chunk_id": extraction.chunk_id,
                "document_id": extraction.document_id,
                "page_start": extraction.page_start,
                "page_end": extraction.page_end,
                "section": extraction.section,
                "sequence": 0,
                "embedding": None,  # Populated separately by embedding step
            })
        except Exception as e:
            logger.debug(f"Chunk node insertion note: {e}")

        logger.info(
            f"Inserted from {extraction.chunk_id}: "
            f"{counts['entities']} entities, {counts['claims']} claims, "
            f"{counts['relationships']} relationships"
            + (f", {counts['errors']} errors" if counts["errors"] else "")
        )

        return counts

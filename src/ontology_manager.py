"""Corpus-driven ontology manager.

The ontology evolves from actual refinery documents. Only categories
justified by corpus content are marked as active. This manager
tracks what's been seen and identifies gaps.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from schemas.knowledge import ChunkExtraction, EntityDomain, EntityType
from schemas.ontology import (
    OntologyClaimPredicate,
    OntologyDomain,
    OntologyRelationType,
    RefineryOntology,
)
from src.config import PipelineConfig

logger = logging.getLogger(__name__)


class OntologyManager:
    """Manages the corpus-driven refinery ontology."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._ontology_path = config.paths.knowledge_dir / "ontology.json"
        self.ontology = self._load_or_create()

    def _load_or_create(self) -> RefineryOntology:
        """Load existing ontology or create a new one."""
        if self._ontology_path.exists():
            try:
                data = json.loads(self._ontology_path.read_text(encoding="utf-8"))
                return RefineryOntology.model_validate(data)
            except Exception as e:
                logger.warning(f"Could not load ontology: {e}. Creating new.")
        return RefineryOntology()

    def update_from_extraction(self, extraction: ChunkExtraction) -> None:
        """Update ontology based on new extraction results."""
        # Track entity types and domains
        for entity in extraction.entities:
            self._record_entity(entity.entity_type.value, entity.domain.value)

        # Track relationship types
        for rel in extraction.relationships:
            self._record_relationship(
                rel.predicate.value,
                getattr(rel.subject_type, "value", "") if rel.subject_type else "",
                getattr(rel.object_type, "value", "") if rel.object_type else "",
            )

        # Track claim predicates
        for claim in extraction.claims:
            self._record_claim_predicate(claim.predicate.value, claim.unit)

        self.ontology.total_entities_seen += len(extraction.entities)
        self.ontology.total_relationships_seen += len(extraction.relationships)
        self.ontology.total_claims_seen += len(extraction.claims)
        self.ontology.last_updated = datetime.now(timezone.utc).isoformat()

    def _record_entity(self, entity_type: str, domain: str) -> None:
        """Record an entity type in the ontology."""
        for d in self.ontology.domains:
            if d.name == domain:
                if entity_type not in d.entity_types:
                    d.entity_types.append(entity_type)
                return
        self.ontology.domains.append(OntologyDomain(
            name=domain,
            entity_types=[entity_type],
        ))

    def _record_relationship(
        self, rel_type: str, subject_type: str, object_type: str,
    ) -> None:
        """Record a relationship type."""
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
        """Record a claim predicate."""
        for p in self.ontology.claim_predicates:
            if p.name == predicate:
                p.usage_count += 1
                return
        self.ontology.claim_predicates.append(OntologyClaimPredicate(
            name=predicate,
            usage_count=1,
        ))

    def save(self) -> None:
        """Save ontology to disk."""
        self._ontology_path.parent.mkdir(parents=True, exist_ok=True)
        self._ontology_path.write_text(
            self.ontology.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info(f"Ontology saved: {len(self.ontology.domains)} domains, "
                     f"{len(self.ontology.relationship_types)} relationship types")

    def increment_documents(self) -> None:
        """Mark that another document has been processed."""
        self.ontology.total_documents_processed += 1

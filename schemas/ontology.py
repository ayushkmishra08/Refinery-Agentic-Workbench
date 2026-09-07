"""Pydantic schema for the corpus-driven refinery ontology.

The ontology is NOT hard-coded before examining documents.
It evolves from actual refinery document content. This schema
tracks what domains, entity types, relationship types, and
claim predicates have been justified by the corpus.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OntologyDomain(BaseModel):
    """A domain in the ontology, justified by corpus evidence."""
    name: str
    description: str = Field(default="")
    entity_types: list[str] = Field(default_factory=list)
    example_entities: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(
        default_factory=list,
        description="Documents that justify this domain",
    )
    is_active: bool = Field(
        default=True,
        description="Whether this domain has been seen in the corpus",
    )


class OntologyRelationType(BaseModel):
    """A relationship type justified by corpus evidence."""
    name: str
    description: str = Field(default="")
    valid_subject_types: list[str] = Field(default_factory=list)
    valid_object_types: list[str] = Field(default_factory=list)
    example_triples: list[str] = Field(
        default_factory=list,
        description="Example (subject, predicate, object) triples from corpus",
    )
    source_documents: list[str] = Field(default_factory=list)
    usage_count: int = Field(default=0)


class OntologyClaimPredicate(BaseModel):
    """A claim predicate justified by corpus evidence."""
    name: str
    valid_unit_families: list[str] = Field(default_factory=list)
    example_claims: list[str] = Field(default_factory=list)
    source_documents: list[str] = Field(default_factory=list)
    usage_count: int = Field(default=0)


class RefineryOntology(BaseModel):
    """Corpus-driven refinery ontology.

    This ontology evolves as documents are processed. Only categories
    justified by actual corpus content are marked as active.
    """
    version: str = Field(default="0.1.0")
    domains: list[OntologyDomain] = Field(default_factory=list)
    relationship_types: list[OntologyRelationType] = Field(default_factory=list)
    claim_predicates: list[OntologyClaimPredicate] = Field(default_factory=list)

    # Discovery tracking
    total_documents_processed: int = Field(default=0)
    total_entities_seen: int = Field(default=0)
    total_relationships_seen: int = Field(default=0)
    total_claims_seen: int = Field(default=0)

    # Gaps and ambiguities
    unclassified_entity_types: list[str] = Field(default_factory=list)
    ambiguous_terms: list[str] = Field(default_factory=list)
    missing_schema_concepts: list[str] = Field(default_factory=list)

    last_updated: str = Field(default="")

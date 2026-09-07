"""Memory retrieval for LLM context — targeted Neo4j queries before each chunk.

Before processing every chunk, retrieves ONLY relevant context from Neo4j:
- Mentioned entities and their aliases
- Glossary terms appearing in the chunk
- Nearby entities in the same section
- Related equipment (1-hop graph traversal)
- Existing claims and relationships
- Similar chunks (vector search)

Never dumps the entire graph. Uses targeted queries.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from schemas.glossary import DocumentGlossary
from src.chunker import Chunk
from src.memory import Neo4jMemory

logger = logging.getLogger(__name__)

# Pattern to find potential entity names (equipment tags, etc.)
ENTITY_NAME_PATTERN = re.compile(
    r"\b[A-Z]{1,4}-?\d{2,5}[A-Z]?\b"
)


@dataclass
class RetrievalContext:
    """Context retrieved from Neo4j for a chunk."""
    mentioned_entities: list[dict] = field(default_factory=list)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    glossary_terms: list[dict] = field(default_factory=list)
    section_entities: list[dict] = field(default_factory=list)
    related_equipment: list[dict] = field(default_factory=list)
    existing_claims: list[dict] = field(default_factory=list)
    existing_relationships: list[dict] = field(default_factory=list)
    referenced_procedures: list[dict] = field(default_factory=list)
    similar_chunks: list[dict] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Check if no relevant context was found."""
        return not any([
            self.mentioned_entities,
            self.glossary_terms,
            self.section_entities,
            self.related_equipment,
            self.existing_claims,
            self.existing_relationships,
        ])

    def to_prompt_context(self) -> str:
        """Format retrieval context for inclusion in LLM prompt."""
        parts: list[str] = []

        if self.mentioned_entities:
            parts.append("## Known Entities in This Context")
            for e in self.mentioned_entities[:15]:
                parts.append(
                    f"- {e.get('name', '?')} ({e.get('entity_type', '?')})"
                    f" [confidence: {e.get('confidence', 0):.2f}]"
                )

        if self.glossary_terms:
            parts.append("\n## Relevant Glossary Terms")
            for t in self.glossary_terms[:10]:
                parts.append(
                    f"- {t.get('term', '?')}: {t.get('canonical_meaning', '?')}"
                )

        if self.existing_claims:
            parts.append("\n## Existing Claims (already extracted)")
            for c in self.existing_claims[:10]:
                claim = c.get("claim", c)
                parts.append(
                    f"- {claim.get('predicate', '?')}: {claim.get('value', '?')} "
                    f"{claim.get('unit', '')} [doc: {claim.get('document_id', '?')}, "
                    f"p.{claim.get('page', '?')}]"
                )

        if self.existing_relationships:
            parts.append("\n## Existing Relationships (already extracted)")
            for r in self.existing_relationships[:10]:
                parts.append(
                    f"- {r.get('subject_name', '?')} → "
                    f"{r.get('predicate', '?')} → "
                    f"{r.get('object_name', '?')}"
                )

        if self.related_equipment:
            parts.append("\n## Related Equipment Nearby")
            for e in self.related_equipment[:10]:
                parts.append(f"- {e.get('name', '?')} ({e.get('entity_type', '?')})")

        return "\n".join(parts) if parts else "(No relevant context found in graph)"


class MemoryRetriever:
    """Retrieves targeted context from Neo4j before each chunk processing."""

    def __init__(self, memory: Neo4jMemory, glossary: DocumentGlossary | None = None):
        self.memory = memory
        self.glossary = glossary

    def retrieve_context(self, chunk: Chunk) -> RetrievalContext:
        """Retrieve relevant context for a chunk from Neo4j.

        Args:
            chunk: The chunk about to be processed.

        Returns:
            RetrievalContext with targeted graph information.
        """
        context = RetrievalContext()

        try:
            # 1. Find entity names mentioned in chunk text
            potential_names = self._extract_potential_names(chunk.text)

            if potential_names:
                # 2. Look up entities in graph
                context.mentioned_entities = self.memory.find_entities_by_name(
                    potential_names,
                )

                # 3. Get UIDs for further queries
                entity_uids = [
                    e["uid"] for e in context.mentioned_entities if "uid" in e
                ]

                if entity_uids:
                    # 4. Get existing claims
                    context.existing_claims = self.memory.get_claims(entity_uids)

                    # 5. Get existing relationships
                    context.existing_relationships = self.memory.get_relationships(
                        entity_uids,
                    )

                    # 6. Get related equipment (1-hop)
                    context.related_equipment = self.memory.get_related_equipment(
                        entity_uids, depth=1,
                    )

            # 7. Section entities
            if chunk.section_path:
                context.section_entities = self.memory.get_section_entities(
                    chunk.section_path,
                )

            # 8. Glossary terms
            if self.glossary:
                context.glossary_terms = self._match_glossary_terms(chunk.text)

            # 9. Vector search for similar chunks (if embedding available)
            if chunk.embedding:
                context.similar_chunks = self.memory.vector_search(
                    chunk.embedding, k=3,
                )

            # 10. Referenced procedures
            context.referenced_procedures = self.memory.get_procedures(chunk.text)

        except Exception as e:
            logger.warning(f"Memory retrieval error for {chunk.chunk_id}: {e}")

        return context

    def _extract_potential_names(self, text: str) -> list[str]:
        """Extract potential entity names from text."""
        # Equipment/instrument tags
        tags = ENTITY_NAME_PATTERN.findall(text)

        # Also extract capitalized multi-word names (e.g., "Crude Distillation Unit")
        # but limit to avoid noise
        names = list(set(tags))
        return names[:30]  # Limit to prevent oversized queries

    def _match_glossary_terms(self, text: str) -> list[dict]:
        """Find glossary terms that appear in the text."""
        if not self.glossary:
            return []

        matches = []
        text_upper = text.upper()
        for entry in self.glossary.entries:
            term_upper = entry.term.upper()
            if term_upper in text_upper:
                matches.append({
                    "term": entry.term,
                    "canonical_meaning": entry.canonical_meaning,
                    "abbreviation": entry.abbreviation,
                    "full_form": entry.full_form,
                })
            if len(matches) >= 15:
                break

        return matches

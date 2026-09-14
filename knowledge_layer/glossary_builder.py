"""Glossary builder — discovers terminology from the document itself.

Builds the document glossary from:
1. Abbreviation tables (highest confidence — direct table extraction)
2. Inline definitions (e.g., "CDU (Crude Distillation Unit)")
3. Document profile abbreviations

The glossary is authoritative for its source document ONLY.
The system does NOT invent expansions from general knowledge.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from knowledge_layer.schemas.document_profile import DocumentProfile
from knowledge_layer.schemas.glossary import DocumentGlossary, GlossaryEntry
from knowledge_layer.schemas.normalized_document import NormalizedDocument
from knowledge_layer.config import PipelineConfig

logger = logging.getLogger(__name__)

# Pattern for inline abbreviation definitions: "CDU (Crude Distillation Unit)"
INLINE_ABBR_PATTERN = re.compile(
    r"\b([A-Z]{2,8})\s*\(([A-Z][a-zA-Z\s/&-]+)\)"
)

# Pattern for reverse inline: "Crude Distillation Unit (CDU)"
REVERSE_INLINE_PATTERN = re.compile(
    r"([A-Z][a-zA-Z\s/&-]{5,50})\s*\(([A-Z]{2,8})\)"
)

# Articles to strip from the beginning of full forms
_STRIP_ARTICLES = re.compile(r"^(?:The|A|An)\s+", re.IGNORECASE)


class GlossaryBuilder:
    """Builds a document glossary from deterministic sources."""

    def __init__(self, config: PipelineConfig):
        self.config = config

    def build_glossary(
        self,
        normalized_doc: NormalizedDocument,
        profile: DocumentProfile,
    ) -> DocumentGlossary:
        """Build glossary from profile abbreviations and inline definitions.

        Args:
            normalized_doc: Layer 2 normalized document.
            profile: Document profile with abbreviation table extractions.

        Returns:
            Complete document glossary.
        """
        doc_id = normalized_doc.document_id
        logger.info(f"Building glossary for {doc_id}")

        entries: list[GlossaryEntry] = []
        seen_terms: set[str] = set()

        # Source 1: Abbreviation table entries (highest confidence)
        for abbr in profile.abbreviations:
            key = abbr.abbreviation.upper().strip()
            if key in seen_terms:
                continue
            seen_terms.add(key)

            entries.append(GlossaryEntry(
                term=abbr.abbreviation,
                canonical_meaning=abbr.full_form,
                abbreviation=abbr.abbreviation,
                full_form=abbr.full_form,
                evidence=abbr.evidence,
                page=abbr.page,
                confidence=0.95,
                source="abbreviation_table",
                document_id=doc_id,
            ))

        # Source 2: Inline definitions from document text
        for element in normalized_doc.elements:
            content = element.content

            # Pattern 1: "CDU (Crude Distillation Unit)"
            for match in INLINE_ABBR_PATTERN.finditer(content):
                abbr = match.group(1).strip()
                full_form = match.group(2).strip()
                key = abbr.upper()
                if key in seen_terms:
                    continue
                seen_terms.add(key)

                entries.append(GlossaryEntry(
                    term=abbr,
                    canonical_meaning=full_form,
                    abbreviation=abbr,
                    full_form=full_form,
                    evidence=match.group(0),
                    page=element.page,
                    section=element.section_path,
                    confidence=0.85,
                    source="inline_definition",
                    document_id=doc_id,
                ))

            # Pattern 2: "Crude Distillation Unit (CDU)"
            for match in REVERSE_INLINE_PATTERN.finditer(content):
                full_form = _STRIP_ARTICLES.sub("", match.group(1).strip())
                abbr = match.group(2).strip()
                key = abbr.upper()
                if key in seen_terms:
                    continue
                seen_terms.add(key)

                entries.append(GlossaryEntry(
                    term=abbr,
                    canonical_meaning=full_form,
                    abbreviation=abbr,
                    full_form=full_form,
                    evidence=match.group(0),
                    page=element.page,
                    section=element.section_path,
                    confidence=0.85,
                    source="inline_definition",
                    document_id=doc_id,
                ))

        # Build glossary
        glossary = DocumentGlossary(
            document_id=doc_id,
            source_filename=normalized_doc.source_filename,
            entries=entries,
            build_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        glossary.build_lookup_maps()

        # Save glossary
        output_dir = self.config.paths.knowledge_dir / doc_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "glossary.json"
        output_path.write_text(
            glossary.model_dump_json(indent=2),
            encoding="utf-8",
        )

        logger.info(
            f"Glossary built for {doc_id}: {glossary.total_entries} entries "
            f"({sum(1 for e in entries if e.source == 'abbreviation_table')} from tables, "
            f"{sum(1 for e in entries if e.source == 'inline_definition')} inline)"
        )

        return glossary

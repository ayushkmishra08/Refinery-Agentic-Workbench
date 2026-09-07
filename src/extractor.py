"""Constrained semantic extraction using DeepSeek-R1 7B via Ollama.

The model receives:
  A. Current normalized chunk
  B. Current document metadata
  C. Relevant document glossary
  D. Relevant entities already known
  E. Relevant relationships already known
  F. Relevant claims already known
  G. Nearby/parent section context
  H. Relevant previous chunks
  I. Relevant cross-references
  J. Structured table representation where applicable

It must extract ONLY information supported by the source text.
It must NEVER invent facts using pretrained knowledge.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from schemas.document_profile import DocumentProfile
from schemas.glossary import DocumentGlossary
from schemas.knowledge import ChunkExtraction
from src.chunker import Chunk
from src.config import PipelineConfig
from src.retriever import RetrievalContext

logger = logging.getLogger(__name__)


class OllamaExtractor:
    """Extracts structured engineering knowledge using DeepSeek-R1 via Ollama."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._client = httpx.Client(
            base_url=config.ollama.base_url,
            timeout=config.ollama.timeout_seconds,
        )
        self._prompts_dir = config.paths.prompts_dir

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def check_model_available(self) -> bool:
        """Check if the configured model is available in Ollama."""
        try:
            response = self._client.get("/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                target = self.config.ollama.model
                available = any(target in name for name in model_names)
                if available:
                    logger.info(f"Model {target} is available")
                else:
                    logger.warning(
                        f"Model {target} not found. Available: {model_names}"
                    )
                return available
            return False
        except Exception as e:
            logger.error(f"Cannot reach Ollama at {self.config.ollama.base_url}: {e}")
            return False

    def extract_chunk(
        self,
        chunk: Chunk,
        context: RetrievalContext,
        profile: DocumentProfile,
        glossary: DocumentGlossary | None = None,
    ) -> ChunkExtraction:
        """Extract engineering knowledge from a single chunk.

        Args:
            chunk: The chunk to process.
            context: Retrieved graph context for this chunk.
            profile: Document profile.
            glossary: Document glossary.

        Returns:
            Structured extraction output.
        """
        start_time = time.time()

        # Build the extraction prompt
        prompt = self._build_extraction_prompt(chunk, context, profile, glossary)

        # Call Ollama
        extraction_text = self._call_ollama(prompt)

        # Parse the structured output
        extraction = self._parse_extraction(
            extraction_text, chunk, profile.document_id,
        )

        extraction.extraction_timestamp = datetime.now(timezone.utc).isoformat()
        extraction.extraction_duration_seconds = time.time() - start_time

        logger.debug(
            f"Extracted from {chunk.chunk_id}: "
            f"{len(extraction.entities)} entities, "
            f"{len(extraction.relationships)} relationships, "
            f"{len(extraction.claims)} claims"
        )

        return extraction

    def _build_extraction_prompt(
        self,
        chunk: Chunk,
        context: RetrievalContext,
        profile: DocumentProfile,
        glossary: DocumentGlossary | None,
    ) -> str:
        """Build the constrained extraction prompt."""
        # Load the extraction prompt template
        prompt_path = self._prompts_dir / "entity_extraction.txt"
        if prompt_path.exists():
            template = prompt_path.read_text(encoding="utf-8")
        else:
            template = self._default_extraction_prompt()

        # Build context sections
        doc_info = (
            f"Document: {profile.title or profile.source_filename}\n"
            f"Type: {profile.document_type.value}\n"
            f"Plant/Unit: {profile.plant or 'unknown'} / {profile.unit or 'unknown'}"
        )

        glossary_text = ""
        if glossary and glossary.entries:
            glossary_text = "\n".join(
                f"- {e.abbreviation}: {e.full_form}"
                for e in glossary.entries[:20]
            )

        graph_context = context.to_prompt_context()

        # Assemble the prompt
        prompt = template.format(
            document_info=doc_info,
            section_path=chunk.section_path,
            page_range=f"Pages {chunk.page_start}-{chunk.page_end}",
            glossary=glossary_text or "(No glossary entries)",
            graph_context=graph_context,
            chunk_text=chunk.text,
        )

        return prompt

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API and return the response text."""
        payload = {
            "model": self.config.ollama.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": self.config.ollama.num_ctx,
                "temperature": self.config.ollama.temperature,
                "num_predict": self.config.ollama.num_predict,
            },
        }

        for attempt in range(self.config.ollama.max_retries + 1):
            try:
                response = self._client.post("/api/generate", json=payload)
                response.raise_for_status()
                result = response.json()
                return result.get("response", "")
            except httpx.TimeoutException:
                logger.warning(
                    f"Ollama timeout (attempt {attempt + 1}/{self.config.ollama.max_retries + 1})"
                )
                if attempt == self.config.ollama.max_retries:
                    raise
            except Exception as e:
                logger.error(f"Ollama error: {e}")
                if attempt == self.config.ollama.max_retries:
                    raise

        return ""

    def _parse_extraction(
        self, text: str, chunk: Chunk, document_id: str,
    ) -> ChunkExtraction:
        """Parse LLM output into structured extraction.

        Attempts to find JSON in the model's response. Falls back to
        empty extraction if parsing fails.
        """
        extraction = ChunkExtraction(
            chunk_id=chunk.chunk_id,
            document_id=document_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section=chunk.section_path,
            model_name=self.config.ollama.model,
        )

        if not text:
            return extraction

        # Try to extract JSON from the response
        json_str = self._find_json_in_response(text)
        if not json_str:
            logger.warning(f"No JSON found in extraction for {chunk.chunk_id}")
            return extraction

        try:
            data = json.loads(json_str)

            # Parse entities
            for e in data.get("entities", []):
                from schemas.knowledge import ExtractedEntity, EntityType, EntityDomain
                try:
                    entity = ExtractedEntity(
                        entity_id=f"{chunk.chunk_id}_e{len(extraction.entities)}",
                        name=e.get("name", ""),
                        canonical_name=e.get("canonical_name", e.get("name", "")),
                        entity_type=EntityType(e.get("entity_type", "generic")),
                        domain=EntityDomain(e.get("domain", "unknown")),
                        aliases=e.get("aliases", []),
                        description=e.get("description", ""),
                        evidence=e.get("evidence", ""),
                        page=e.get("page", chunk.page_start),
                        section=chunk.section_path,
                        document_id=document_id,
                        chunk_id=chunk.chunk_id,
                        confidence=float(e.get("confidence", 0.5)),
                    )
                    extraction.entities.append(entity)
                except (ValueError, KeyError) as ve:
                    logger.debug(f"Skipping invalid entity: {ve}")

            # Parse relationships
            for r in data.get("relationships", []):
                from schemas.knowledge import ExtractedRelationship, RelationshipType
                try:
                    rel = ExtractedRelationship(
                        relationship_id=f"{chunk.chunk_id}_r{len(extraction.relationships)}",
                        subject=r.get("subject", ""),
                        predicate=RelationshipType(r.get("predicate", "ASSOCIATED_WITH")),
                        object=r.get("object", ""),
                        evidence=r.get("evidence", ""),
                        page=r.get("page", chunk.page_start),
                        section=chunk.section_path,
                        document_id=document_id,
                        chunk_id=chunk.chunk_id,
                        confidence=float(r.get("confidence", 0.5)),
                    )
                    extraction.relationships.append(rel)
                except (ValueError, KeyError) as ve:
                    logger.debug(f"Skipping invalid relationship: {ve}")

            # Parse claims
            for c in data.get("claims", []):
                from schemas.claims import EngineeringClaim, ClaimCategory
                try:
                    claim = EngineeringClaim(
                        claim_id=f"{chunk.chunk_id}_c{len(extraction.claims)}",
                        subject=c.get("subject", ""),
                        predicate=ClaimCategory(c.get("predicate", "generic_property")),
                        value=str(c.get("value", "")),
                        unit=c.get("unit", ""),
                        evidence=c.get("evidence", ""),
                        page=c.get("page", chunk.page_start),
                        section=chunk.section_path,
                        document_id=document_id,
                        chunk_id=chunk.chunk_id,
                        confidence=float(c.get("confidence", 0.5)),
                        is_from_table=c.get("is_from_table", False),
                    )
                    extraction.claims.append(claim)
                except (ValueError, KeyError) as ve:
                    logger.debug(f"Skipping invalid claim: {ve}")

            # Parse references
            extraction.references = data.get("references", [])

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error for {chunk.chunk_id}: {e}")

        return extraction

    def _find_json_in_response(self, text: str) -> str | None:
        """Extract JSON block from model response (handles ```json blocks)."""
        # Try to find ```json ... ``` block
        import re
        json_match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if json_match:
            return json_match.group(1)

        # Try to find raw JSON object
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json_match.group(0)

        return None

    def _default_extraction_prompt(self) -> str:
        """Fallback extraction prompt if template file is missing."""
        return """You are an engineering knowledge extraction system.

DOCUMENT INFORMATION:
{document_info}
Section: {section_path}
{page_range}

DOCUMENT GLOSSARY (authoritative for this document):
{glossary}

EXISTING KNOWLEDGE FROM GRAPH:
{graph_context}

CRITICAL RULES:
1. Extract ONLY facts explicitly stated in the source text below.
2. NEVER invent information from your training data.
3. Every entity, relationship, and claim MUST have evidence (exact quote from source).
4. If unsure, mark confidence as low or skip entirely.
5. Use glossary terms as-is — do NOT re-interpret abbreviations.
6. A relationship requires EXPLICIT evidence — co-occurrence is NOT sufficient.
7. Prefer UNKNOWN over GUESS.

SOURCE TEXT TO ANALYZE:
---
{chunk_text}
---

Return a JSON object with this structure:
```json
{{
  "entities": [
    {{"name": "...", "canonical_name": "...", "entity_type": "...", "domain": "...",
      "aliases": [], "description": "...", "evidence": "...", "page": 0, "confidence": 0.0}}
  ],
  "relationships": [
    {{"subject": "...", "predicate": "...", "object": "...",
      "evidence": "...", "page": 0, "confidence": 0.0}}
  ],
  "claims": [
    {{"subject": "...", "predicate": "...", "value": "...", "unit": "...",
      "evidence": "...", "page": 0, "confidence": 0.0, "is_from_table": false}}
  ],
  "references": ["..."]
}}
```"""

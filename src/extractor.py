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
import re
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import httpx

from schemas.document_profile import DocumentProfile
from schemas.glossary import DocumentGlossary
from schemas.knowledge import ChunkExtraction
from src.chunker import Chunk
from src.config import PipelineConfig
from src.retriever import RetrievalContext

logger = logging.getLogger(__name__)


def _safe_float(value, default: float) -> float:
    """Coerce an LLM-provided number (may be null/str) to float, clamped to [0, 1]."""
    try:
        if value is None:
            return default
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return max(0.0, min(1.0, f))


def _safe_page(value, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class ExtractionStatus(str, Enum):
    """Status of a chunk extraction attempt."""
    SUCCESS = "success"              # Parsed JSON, found entities/claims/rels
    EMPTY = "empty"                  # Parsed JSON successfully but nothing extractable
    RETRY_SUCCEEDED = "retry_succeeded"  # Failed first attempt, succeeded on retry
    JSON_FAILED = "json_failed"      # All retries exhausted, could not parse JSON
    ERROR = "error"                  # Non-JSON error (network, timeout, etc.)


class ExtractionResult:
    """Wraps a ChunkExtraction with status metadata."""
    def __init__(
        self,
        extraction: ChunkExtraction,
        status: ExtractionStatus,
        raw_response: str = "",
        failure_reason: str = "",
        attempts: int = 1,
    ):
        self.extraction = extraction
        self.status = status
        self.raw_response = raw_response
        self.failure_reason = failure_reason
        self.attempts = attempts

    @property
    def is_success(self) -> bool:
        return self.status in (
            ExtractionStatus.SUCCESS,
            ExtractionStatus.EMPTY,
            ExtractionStatus.RETRY_SUCCEEDED,
        )

    @property
    def is_failure(self) -> bool:
        return self.status in (
            ExtractionStatus.JSON_FAILED,
            ExtractionStatus.ERROR,
        )


class OllamaExtractor:
    """Extracts structured engineering knowledge using DeepSeek-R1 via Ollama."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._client = httpx.Client(
            base_url=config.ollama.base_url,
            timeout=config.ollama.timeout_seconds,
        )
        self._prompts_dir = config.paths.prompts_dir
        self._failures_dir = config.paths.data_dir / "failures"
        self._failures_dir.mkdir(parents=True, exist_ok=True)

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
    ) -> ExtractionResult:
        """Extract engineering knowledge from a single chunk.

        Args:
            chunk: The chunk to process.
            context: Retrieved graph context for this chunk.
            profile: Document profile.
            glossary: Document glossary.

        Returns:
            ExtractionResult with extraction data and status metadata.
        """
        start_time = time.time()

        # Build the extraction prompt
        prompt = self._build_extraction_prompt(chunk, context, profile, glossary)

        max_attempts = 2
        last_raw_response = ""
        last_error = ""

        for attempt in range(max_attempts):
            try:
                # Call Ollama
                extraction_text = self._call_ollama(prompt)
                last_raw_response = extraction_text

                # Parse the structured output
                extraction = self._parse_extraction(
                    extraction_text, chunk, profile.document_id,
                )

                extraction.extraction_timestamp = datetime.now(timezone.utc).isoformat()
                extraction.extraction_duration_seconds = time.time() - start_time

                has_content = (
                    len(extraction.entities) > 0
                    or len(extraction.claims) > 0
                    or len(extraction.relationships) > 0
                )

                if attempt > 0:
                    status = ExtractionStatus.RETRY_SUCCEEDED
                elif has_content:
                    status = ExtractionStatus.SUCCESS
                else:
                    status = ExtractionStatus.EMPTY

                return ExtractionResult(
                    extraction=extraction,
                    status=status,
                    raw_response=extraction_text,
                    attempts=attempt + 1,
                )

            except json.JSONDecodeError as e:
                last_error = str(e)
                logger.warning(
                    f"JSON parse error on attempt {attempt + 1}/{max_attempts} "
                    f"for {chunk.chunk_id}: {e}"
                )
                if attempt < max_attempts - 1:
                    continue  # Retry

            except Exception as e:
                last_error = str(e)
                logger.error(
                    f"Extraction error on attempt {attempt + 1}/{max_attempts} "
                    f"for {chunk.chunk_id}: {e}"
                )
                if attempt < max_attempts - 1:
                    continue

        # All retries exhausted — record failure
        logger.error(
            f"FAILED extraction for {chunk.chunk_id} after {max_attempts} attempts: "
            f"{last_error}"
        )

        # Save raw response for inspection
        self._save_failure(chunk.chunk_id, last_raw_response, last_error)

        empty_extraction = ChunkExtraction(
            chunk_id=chunk.chunk_id,
            document_id=profile.document_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section=chunk.section_path,
            model_name=self.config.ollama.model,
        )
        empty_extraction.extraction_timestamp = datetime.now(timezone.utc).isoformat()
        empty_extraction.extraction_duration_seconds = time.time() - start_time

        return ExtractionResult(
            extraction=empty_extraction,
            status=ExtractionStatus.JSON_FAILED,
            raw_response=last_raw_response,
            failure_reason=last_error,
            attempts=max_attempts,
        )

    def extract_relationships(
        self,
        chunk: Chunk,
        entities: list,
        context: RetrievalContext,
        profile: DocumentProfile,
    ) -> tuple[list, list]:
        """Second pass: relationships and claims only, given the entities found in the chunk.

        Returns (relationships, claims) tagged with source='llm_pass2'.
        """
        from schemas.claims import ClaimCategory, EngineeringClaim
        from schemas.knowledge import ExtractedRelationship, RelationshipType

        prompt_path = self._prompts_dir / "relationship_extraction.txt"
        if not prompt_path.exists():
            return [], []
        template = prompt_path.read_text(encoding="utf-8")
        names = []
        for e in entities:
            label = e.name if not e.canonical_name or e.canonical_name == e.name else f"{e.name} ({e.canonical_name})"
            tag = f" tag={e.canonical_tag}" if getattr(e, "canonical_tag", None) else ""
            names.append(f"- {label} [{e.entity_type.value}]{tag}")
        nl = chr(10)
        prompt = template.format(
            document_info=f"Document: {profile.title or profile.source_filename}{nl}"
                          f"Plant/Unit: {profile.plant or 'unknown'} / {profile.unit or 'unknown'}",
            section_path=chunk.section_path,
            page_range=f"Pages {chunk.page_start}-{chunk.page_end}",
            entity_list=nl.join(names) or "(none)",
            graph_context=context.to_prompt_context(),
            chunk_text=chunk.text,
            page_hint=chunk.page_start,
        )
        try:
            raw = self._call_ollama(prompt)
        except Exception as e:
            logger.warning(f"Relationship pass failed for {chunk.chunk_id}: {e}")
            return [], []
        json_str = self._find_json_in_response(raw)
        if not json_str:
            return [], []
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            repaired = self._repair_json(json_str)
            try:
                data = json.loads(repaired) if repaired else {}
            except json.JSONDecodeError:
                return [], []
        if not isinstance(data, dict):
            return [], []

        rels = []
        for r in data.get("relationships", []) or []:
            if not isinstance(r, dict):
                continue
            raw_pred = str(r.get("predicate") or "ASSOCIATED_WITH")
            try:
                pred = RelationshipType(raw_pred.strip().upper().replace(" ", "_"))
            except ValueError:
                pred = RelationshipType.ASSOCIATED_WITH
            try:
                rels.append(ExtractedRelationship(
                    relationship_id=f"{chunk.chunk_id}_r2_{len(rels)}",
                    subject=str(r.get("subject") or ""),
                    predicate=pred,
                    predicate_raw=raw_pred,
                    object=str(r.get("object") or ""),
                    evidence=str(r.get("evidence") or ""),
                    page=_safe_page(r.get("page"), chunk.page_start),
                    section=chunk.section_path,
                    document_id=profile.document_id,
                    chunk_id=chunk.chunk_id,
                    confidence=_safe_float(r.get("confidence"), 0.5),
                    sentence_index=_safe_page(r.get("sentence_index"), -1) if r.get("sentence_index") is not None else None,
                    source="llm_pass2",
                ))
            except (ValueError, KeyError, TypeError) as ve:
                logger.debug(f"Skipping invalid relationship (pass 2): {ve}")

        claims = []
        for c in data.get("claims", []) or []:
            if not isinstance(c, dict):
                continue
            raw_cpred = str(c.get("predicate") or "generic_property")
            try:
                cpred = ClaimCategory(raw_cpred.strip().lower().replace(" ", "_"))
            except ValueError:
                cpred = ClaimCategory.GENERIC_PROPERTY
            try:
                claims.append(EngineeringClaim(
                    claim_id=f"{chunk.chunk_id}_c2_{len(claims)}",
                    subject=str(c.get("subject") or ""),
                    predicate=cpred,
                    predicate_raw=raw_cpred,
                    value=str(c.get("value") if c.get("value") is not None else ""),
                    unit=str(c.get("unit") or ""),
                    evidence=str(c.get("evidence") or ""),
                    page=_safe_page(c.get("page"), chunk.page_start),
                    section=chunk.section_path,
                    document_id=profile.document_id,
                    chunk_id=chunk.chunk_id,
                    confidence=_safe_float(c.get("confidence"), 0.5),
                    is_from_table=bool(c.get("is_from_table") or False),
                    source="llm_pass2",
                ))
            except (ValueError, KeyError, TypeError) as ve:
                logger.debug(f"Skipping invalid claim (pass 2): {ve}")
        logger.info(f"Relationship pass for {chunk.chunk_id}: {len(rels)} relationships, {len(claims)} claims")
        return rels, claims

    def _save_failure(self, chunk_id: str, raw_response: str, error: str) -> None:
        """Preserve raw LLM output on failure for later inspection."""
        try:
            failure_data = {
                "chunk_id": chunk_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": error,
                "raw_response": raw_response,
            }
            path = self._failures_dir / f"{chunk_id}_failure.json"
            path.write_text(json.dumps(failure_data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not save failure data: {e}")

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
            "format": "json",  # Enforce strict JSON output
            "think": self.config.ollama.think,
            "keep_alive": "30m",
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

        Attempts to find and repair JSON in the model's response.
        Raises json.JSONDecodeError if parsing fails (triggers retry).
        """
        extraction = ChunkExtraction(
            chunk_id=chunk.chunk_id,
            document_id=document_id,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section=chunk.section_path,
            model_name=self.config.ollama.model,
        )

        if not text or not text.strip():
            return extraction

        # Try to extract JSON from the response
        json_str = self._find_json_in_response(text)
        if not json_str:
            raise json.JSONDecodeError("No JSON found in response", text, 0)

        # Attempt to parse, with repair on failure
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Attempt deterministic repair
            repaired = self._repair_json(json_str)
            if repaired:
                data = json.loads(repaired)  # Let this raise if repair also fails
            else:
                raise

        # Parse entities (open vocabulary: unknown labels map to OTHER, raw kept)
        for e in data.get("entities", []):
            from schemas.knowledge import ExtractedEntity, EntityType, EntityDomain
            try:
                raw_type = str(e.get("entity_type") or "generic")
                try:
                    etype = EntityType(raw_type.strip().lower())
                except ValueError:
                    etype = EntityType.OTHER
                try:
                    edomain = EntityDomain(str(e.get("domain") or "unknown").strip().lower())
                except ValueError:
                    edomain = EntityDomain.UNKNOWN
                entity = ExtractedEntity(
                    entity_id=f"{chunk.chunk_id}_e{len(extraction.entities)}",
                    name=str(e.get("name") or ""),
                    canonical_name=str(e.get("canonical_name") or e.get("name") or ""),
                    entity_type=etype,
                    entity_type_raw=raw_type,
                    domain=edomain,
                    aliases=[str(a) for a in (e.get("aliases") or []) if a],
                    description=str(e.get("description") or ""),
                    evidence=str(e.get("evidence") or ""),
                    page=_safe_page(e.get("page"), chunk.page_start),
                    section=chunk.section_path,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    confidence=_safe_float(e.get("confidence"), 0.5),
                    sentence_index=_safe_page(e.get("sentence_index"), -1) if e.get("sentence_index") is not None else None,
                )
                extraction.entities.append(entity)
            except (ValueError, KeyError, TypeError) as ve:
                logger.debug(f"Skipping invalid entity: {ve}")

        # Parse relationships
        for r in data.get("relationships", []):
            from schemas.knowledge import ExtractedRelationship, RelationshipType
            try:
                raw_pred = str(r.get("predicate") or "ASSOCIATED_WITH")
                try:
                    pred = RelationshipType(raw_pred.strip().upper().replace(" ", "_"))
                except ValueError:
                    pred = RelationshipType.ASSOCIATED_WITH
                rel = ExtractedRelationship(
                    relationship_id=f"{chunk.chunk_id}_r{len(extraction.relationships)}",
                    subject=str(r.get("subject") or ""),
                    predicate=pred,
                    predicate_raw=raw_pred,
                    object=str(r.get("object") or ""),
                    evidence=str(r.get("evidence") or ""),
                    page=_safe_page(r.get("page"), chunk.page_start),
                    section=chunk.section_path,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    confidence=_safe_float(r.get("confidence"), 0.5),
                )
                extraction.relationships.append(rel)
            except (ValueError, KeyError, TypeError) as ve:
                logger.debug(f"Skipping invalid relationship: {ve}")

        # Parse claims
        for c in data.get("claims", []):
            from schemas.claims import EngineeringClaim, ClaimCategory
            try:
                raw_cpred = str(c.get("predicate") or "generic_property")
                try:
                    cpred = ClaimCategory(raw_cpred.strip().lower().replace(" ", "_"))
                except ValueError:
                    cpred = ClaimCategory.GENERIC_PROPERTY
                claim = EngineeringClaim(
                    claim_id=f"{chunk.chunk_id}_c{len(extraction.claims)}",
                    subject=str(c.get("subject") or ""),
                    predicate=cpred,
                    predicate_raw=raw_cpred,
                    value=str(c.get("value", "")),
                    unit=str(c.get("unit") or ""),
                    evidence=str(c.get("evidence") or ""),
                    page=_safe_page(c.get("page"), chunk.page_start),
                    section=chunk.section_path,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    confidence=_safe_float(c.get("confidence"), 0.5),
                    is_from_table=bool(c.get("is_from_table") or False),
                )
                extraction.claims.append(claim)
            except (ValueError, KeyError, TypeError) as ve:
                logger.debug(f"Skipping invalid claim: {ve}")

        # Parse references
        extraction.references = [str(x) for x in (data.get("references") or []) if x]

        return extraction

    def _find_json_in_response(self, text: str) -> str | None:
        """Extract JSON block from model response (handles ```json blocks)."""
        # Try to find ```json ... ``` block
        json_match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if json_match:
            return json_match.group(1)

        # Try to find raw JSON object
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json_match.group(0)

        return None

    def _repair_json(self, json_str: str) -> str | None:
        """Attempt deterministic JSON repair.

        Handles two failure modes:
        1. Trailing commas before } or ] (common DeepSeek-R1 issue)
        2. Truncated output (model hit num_predict limit mid-JSON)
        
        For truncation, we close any open strings/arrays/objects to salvage
        whatever complete entities/claims/relationships were already emitted.
        
        Returns repaired string or None if repair is not possible.
        """
        try:
            repaired = json_str

            # Remove trailing commas before closing brackets
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

            # Remove any control characters except newlines and tabs
            repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)

            # Try to parse after basic fixes
            try:
                json.loads(repaired)
                return repaired
            except json.JSONDecodeError:
                pass

            # Truncation repair: close open brackets/braces
            # Find the last complete JSON value by tracking bracket depth
            # Remove any trailing incomplete value after the last comma
            # Then close remaining open brackets
            
            # Strip any trailing incomplete string value
            # Pattern: remove text after the last complete key-value pair
            # Find last complete object or array element
            last_good = self._find_last_complete_element(repaired)
            if last_good:
                repaired = last_good
                try:
                    json.loads(repaired)
                    logger.info("JSON repair: recovered truncated output")
                    return repaired
                except json.JSONDecodeError:
                    pass

            return None
        except Exception:
            return None

    def _find_last_complete_element(self, json_str: str) -> str | None:
        """Find the last position where JSON can be validly closed.
        
        Walks through the string tracking bracket/brace depth and
        tries closing from the last complete value.
        """
        # Strategy: progressively trim from the end and try to close
        # Find positions of all commas that could be element separators
        
        # First, remove any trailing partial string (after last unmatched quote)
        trimmed = json_str.rstrip()
        
        # Try progressively shorter prefixes, looking for one we can close
        for end_pos in range(len(trimmed), max(len(trimmed) - 500, 0), -1):
            candidate = trimmed[:end_pos].rstrip().rstrip(',').rstrip()
            
            # Count open brackets/braces
            open_braces = 0
            open_brackets = 0
            in_string = False
            escape_next = False
            
            for ch in candidate:
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    open_braces += 1
                elif ch == '}':
                    open_braces -= 1
                elif ch == '[':
                    open_brackets += 1
                elif ch == ']':
                    open_brackets -= 1
            
            # If we're inside a string, skip this position
            if in_string:
                continue
            
            # Close remaining open brackets/braces
            if open_braces >= 0 and open_brackets >= 0:
                closing = ']' * open_brackets + '}' * open_braces
                try:
                    result = candidate + closing
                    json.loads(result)
                    return result
                except json.JSONDecodeError:
                    continue
        
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

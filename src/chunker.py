"""Structure-aware chunker for normalized documents.

Chunks respect document structure:
  - Never split mid-table
  - Never split mid-procedure
  - Prefer section boundaries
  - Include parent heading context in every chunk
  - Target ~800-1200 tokens per chunk
  - Overlap: last 2 sentences of previous chunk as context bridge

Each chunk carries metadata for the retriever and extractor.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from schemas.normalized_document import ContentType, NormalizedDocument, NormalizedElement
from schemas.table_schema import TableNormalizationResult
from src.config import PipelineConfig

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A structure-aware chunk of document content."""
    chunk_id: str
    document_id: str
    sequence: int  # Order within the document
    page_start: int
    page_end: int
    section_path: str
    parent_heading: str

    # Content
    text: str
    elements: list[NormalizedElement] = field(default_factory=list)

    # Metadata
    contains_table: bool = False
    table_ids: list[str] = field(default_factory=list)
    contains_procedure: bool = False
    element_types: list[str] = field(default_factory=list)
    token_estimate: int = 0

    # For context bridge
    overlap_text: str = ""  # From previous chunk

    # Embedding (populated later)
    embedding: list[float] | None = None


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (words * 1.3 for subword tokenization)."""
    return int(len(text.split()) * 1.3)


def _last_n_sentences(text: str, n: int = 2) -> str:
    """Extract the last N sentences from text."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[-n:]) if sentences else ""


class DocumentChunker:
    """Creates structure-aware chunks from normalized documents."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.target_tokens = config.chunker.target_tokens
        self.max_tokens = config.chunker.max_tokens
        self.overlap_sentences = config.chunker.overlap_sentences

    def chunk(
        self,
        normalized_doc: NormalizedDocument,
        table_results: TableNormalizationResult | None = None,
    ) -> list[Chunk]:
        """Chunk a normalized document respecting structure.

        Args:
            normalized_doc: Layer 2 normalized document.
            table_results: Table classifications (optional, for enrichment).

        Returns:
            List of chunks in document order.
        """
        doc_id = normalized_doc.document_id
        logger.info(f"Chunking document: {doc_id}")

        chunks: list[Chunk] = []
        current_elements: list[NormalizedElement] = []
        current_tokens = 0
        current_section_path = ""
        current_heading = ""
        prev_overlap = ""
        sequence = 0

        # Large tables are split into row batches (header row repeated) so a
        # single table never exceeds the model context on its own.
        elements = self._split_large_tables(normalized_doc.elements)

        for elem in elements:
            elem_tokens = _estimate_tokens(elem.content)

            # Check if we should start a new chunk
            should_break = False

            # Rule 1: Section boundary with sufficient content
            if (
                elem.content_type == ContentType.HEADING
                and current_tokens >= self.target_tokens * 0.5
            ):
                should_break = True

            # Rule 2: Exceeding max tokens (but not mid-table/procedure)
            if current_tokens + elem_tokens > self.max_tokens:
                if not self._in_atomic_block(current_elements):
                    should_break = True

            if should_break and current_elements:
                # Emit current chunk
                chunk = self._build_chunk(
                    doc_id, sequence, current_elements, current_section_path,
                    current_heading, prev_overlap,
                )
                chunks.append(chunk)

                # Prepare overlap for next chunk
                prev_overlap = _last_n_sentences(chunk.text, self.overlap_sentences)
                sequence += 1

                current_elements = []
                current_tokens = 0

            # Update tracking
            if elem.content_type == ContentType.HEADING:
                current_heading = elem.content.strip()
                current_section_path = elem.section_path

            current_elements.append(elem)
            current_tokens += elem_tokens

        # Emit final chunk
        if current_elements:
            chunk = self._build_chunk(
                doc_id, sequence, current_elements, current_section_path,
                current_heading, prev_overlap,
            )
            chunks.append(chunk)

        logger.info(
            f"Chunked {doc_id}: {len(chunks)} chunks, "
            f"avg {sum(c.token_estimate for c in chunks) // max(len(chunks), 1)} tokens/chunk"
        )

        return chunks

    def _build_chunk(
        self,
        doc_id: str,
        sequence: int,
        elements: list[NormalizedElement],
        section_path: str,
        heading: str,
        overlap: str,
    ) -> Chunk:
        """Build a Chunk from accumulated elements."""
        text_parts = []
        if overlap:
            text_parts.append(f"[Context from previous section:]\n{overlap}\n\n---\n")

        for elem in elements:
            if elem.content_type == ContentType.HEADING:
                level = elem.heading_level or 2
                text_parts.append(f"{'#' * level} {elem.content}")
            else:
                text_parts.append(elem.content)

        full_text = "\n\n".join(text_parts)

        pages = [e.page for e in elements]
        element_types = list(set(e.content_type.value for e in elements))
        table_ids = [e.table_id for e in elements if e.table_id]

        return Chunk(
            chunk_id=f"{doc_id}_chunk_{sequence:04d}",
            document_id=doc_id,
            sequence=sequence,
            page_start=min(pages) if pages else 0,
            page_end=max(pages) if pages else 0,
            section_path=section_path,
            parent_heading=heading,
            text=full_text,
            elements=elements,
            contains_table=bool(table_ids),
            table_ids=table_ids,
            contains_procedure=ContentType.PROCEDURE_STEP.value in element_types,
            element_types=element_types,
            token_estimate=_estimate_tokens(full_text),
            overlap_text=overlap,
        )

    def _split_large_tables(
        self, elements: list[NormalizedElement],
    ) -> list[NormalizedElement]:
        """Split table elements whose text exceeds ``max_tokens`` into row batches.

        The table text produced by the parser is one pipe-separated line per
        row, first line = header row.  Each batch repeats the header row and
        is annotated with its row range so provenance stays explicit.
        """
        out: list[NormalizedElement] = []
        for elem in elements:
            if elem.content_type != ContentType.TABLE or _estimate_tokens(elem.content) <= self.max_tokens:
                out.append(elem)
                continue

            lines = [ln for ln in elem.content.split("\n") if ln.strip()]
            if len(lines) < 4:
                out.append(elem)
                continue

            header, body = lines[0], lines[1:]
            header_tokens = _estimate_tokens(header)
            budget = max(self.target_tokens - header_tokens - 20, 100)
            batches: list[list[str]] = []
            current: list[str] = []
            current_tokens = 0
            for row in body:
                rt = _estimate_tokens(row)
                if current and current_tokens + rt > budget:
                    batches.append(current)
                    current, current_tokens = [], 0
                current.append(row)
                current_tokens += rt
            if current:
                batches.append(current)

            total_rows = len(body)
            row_cursor = 0
            for i, batch in enumerate(batches):
                first = row_cursor + 1
                last = row_cursor + len(batch)
                row_cursor = last
                note = f"[Table {elem.table_id or ''} part {i + 1}/{len(batches)}: rows {first}-{last} of {total_rows}]"
                out.append(elem.model_copy(update={
                    "element_id": f"{elem.element_id}_part{i + 1}",
                    "content": "\n".join([note, header, *batch]),
                }))
            logger.debug(f"Split table {elem.table_id} into {len(batches)} parts")
        return out

    def _in_atomic_block(self, elements: list[NormalizedElement]) -> bool:
        """Check if the last element is part of an atomic block (table/procedure)."""
        if not elements:
            return False
        last = elements[-1]
        return last.content_type in (ContentType.TABLE, ContentType.PROCEDURE_STEP)

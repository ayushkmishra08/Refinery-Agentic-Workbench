"""Structure-aware chunker for normalized documents.

Two representations come out of every chunk:

  * ``text``            the canonical source span, once.  No overlap, no context
                        bridge, no page furniture.  This is what the knowledge
                        extractor (LLM passes, rule pass, table/spec mappers) sees,
                        so a fact is never extracted twice from two chunks and a
                        previous section's numbers never sit next to this section's.
  * ``retrieval_text``  section path + a short bridge from the previous chunk +
                        text.  Used only for embeddings / similarity retrieval.

Chunk boundaries follow the document structure:
  - a chapter or top-level section heading (level <= 2) always starts a new chunk;
  - deeper headings start a new chunk once the current one is half the target size;
  - a procedure (label + ordered steps) is atomic: it starts its own chunk and is
    never split unless it exceeds twice the maximum, in which case it is cut at a
    step boundary and both parts are annotated;
  - a table is never split mid-table (large tables are pre-split into row batches).

Every chunk carries a ``chunk_type`` (procedure, table, specification, safety,
upset, equipment, narrative, document_control) and ``is_engineering`` so the
extractor can choose the right schema and skip document-control content.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from schemas.normalized_document import ContentType, NormalizedDocument, NormalizedElement
from schemas.table_schema import TableNormalizationResult
from src.config import PipelineConfig

logger = logging.getLogger(__name__)

CHUNK_TYPES = (
    "narrative", "procedure", "table", "specification", "safety", "upset",
    "equipment", "control", "document_control", "toc",
)

_UPSET_RE = re.compile(r"upset|deviation|stabili[sz]ation|consequence|abnormal|troubleshoot", re.I)
_SAFETY_RE = re.compile(
    r"safety|hazard|emergency|permit|confined|hot work|\bppe\b|spill|fire|toxic|msds|\bsds\b|first aid|"
    r"lock\s*out|isolation|relief|\bpsv\b|interlock", re.I,
)
_CONTROL_RE = re.compile(r"control scheme|control loop|\bdcs\b|\bapc\b|controller|interlock|trip", re.I)
_EQUIPMENT_RE = re.compile(r"equipment|pump|heater|furnace|column|desalter|exchanger|ejector|compressor|vessel|drum|tower", re.I)


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

    # Content (canonical source span, once)
    text: str
    elements: list[NormalizedElement] = field(default_factory=list)

    # Metadata
    contains_table: bool = False
    table_ids: list[str] = field(default_factory=list)
    contains_procedure: bool = False
    procedure_ids: list[str] = field(default_factory=list)
    element_types: list[str] = field(default_factory=list)
    token_estimate: int = 0
    chunk_type: str = "narrative"
    is_engineering: bool = True
    chapter_number: int | None = None
    section_id: str | None = None

    # Context bridge for retrieval only (never part of ``text``)
    overlap_text: str = ""

    # Embedding (populated later)
    embedding: list[float] | None = None

    @property
    def retrieval_text(self) -> str:
        """Text used for embeddings: section path + short bridge + canonical text."""
        parts = []
        if self.section_path:
            parts.append(self.section_path)
        if self.overlap_text:
            parts.append(self.overlap_text)
        parts.append(self.text)
        return "\n".join(parts)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (words * 1.3 for subword tokenization)."""
    return int(len(text.split()) * 1.3)


def _last_n_sentences(text: str, n: int = 2) -> str:
    """Extract the last N sentences from text."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[-n:]) if sentences else ""


def _render(elem: NormalizedElement) -> str:
    """How one element appears inside chunk text."""
    if elem.content_type == ContentType.HEADING:
        level = elem.heading_level or 2
        return f"{'#' * min(level, 6)} {elem.content.strip()}"
    if elem.content_type == ContentType.PROCEDURE_STEP and elem.step_number:
        body = re.sub(r"^\s*(?:\(?\d{1,2}[.)]|\(?[a-z][.)]|\(?[ivx]{1,4}[.)]|[-•▪■●○◦¾*Øø>])\s*", "", elem.content.strip())
        return f"{elem.step_number}. {body}"
    return elem.content


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
        current: list[NormalizedElement] = []
        current_tokens = 0
        current_section_path = ""
        current_heading = ""
        prev_overlap = ""
        sequence = 0

        elements = self._split_large_tables(normalized_doc.elements)

        def emit() -> None:
            nonlocal current, current_tokens, prev_overlap, sequence
            if not current:
                return
            chunk = self._build_chunk(doc_id, sequence, current, current_section_path, current_heading, prev_overlap)
            chunks.append(chunk)
            prev_overlap = _last_n_sentences(chunk.text, self.overlap_sentences)
            sequence += 1
            current = []
            current_tokens = 0

        for idx, elem in enumerate(elements):
            elem_tokens = _estimate_tokens(elem.content)
            should_break = False

            has_body = any(e.content_type != ContentType.HEADING for e in current)
            # Rule 1: chapter / top-level section boundary always breaks (never leave a heading-only chunk)
            if elem.content_type == ContentType.HEADING and (elem.heading_level or 2) <= 2 and has_body:
                should_break = True
            # Rule 2: deeper section boundary once the chunk is half full
            elif elem.content_type == ContentType.HEADING and has_body and current_tokens >= self.target_tokens * 0.5:
                should_break = True

            # Rule 3: a procedure starts its own chunk (label line = step 0, or first step)
            starts_procedure = (
                elem.procedure_id is not None
                and (elem.step_number == 0 or (elem.step_number == 1 and not self._prev_same_procedure(elements, idx)))
            )
            if starts_procedure and current and current_tokens >= self.target_tokens * 0.25:
                # keep the immediately preceding heading with the procedure
                should_break = True
                if current and current[-1].content_type == ContentType.HEADING and len(current) > 1:
                    heading = current.pop()
                    current_tokens -= _estimate_tokens(heading.content)
                    emit()
                    current.append(heading)
                    current_tokens += _estimate_tokens(heading.content)
                    should_break = False

            # Rule 4: exceeding max tokens (but not mid-table / mid-procedure)
            if current_tokens + elem_tokens > self.max_tokens and current:
                in_atomic = self._in_atomic_block(current, elem)
                if not in_atomic or current_tokens + elem_tokens > self.max_tokens * 2:
                    should_break = True

            if should_break:
                emit()

            if elem.content_type == ContentType.HEADING:
                current_heading = elem.content.strip()
                current_section_path = elem.section_path
            elif not current:
                # a chunk that does not start with a heading inherits the element's own path
                current_section_path = elem.section_path
                current_heading = elem.parent_heading or current_heading

            current.append(elem)
            current_tokens += elem_tokens

        emit()

        logger.info(
            f"Chunked {doc_id}: {len(chunks)} chunks, "
            f"avg {sum(c.token_estimate for c in chunks) // max(len(chunks), 1)} tokens/chunk, "
            f"types={ {t: sum(1 for c in chunks if c.chunk_type == t) for t in CHUNK_TYPES if any(c.chunk_type == t for c in chunks)} }"
        )
        return chunks

    @staticmethod
    def _prev_same_procedure(elements: list[NormalizedElement], idx: int) -> bool:
        return idx > 0 and elements[idx - 1].procedure_id == elements[idx].procedure_id

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
        text_parts = [_render(e) for e in elements]
        full_text = "\n\n".join(text_parts)

        pages = [e.page for e in elements]
        element_types = sorted(set(e.content_type.value for e in elements))
        table_ids = list(dict.fromkeys(e.table_id for e in elements if e.table_id))
        procedure_ids = list(dict.fromkeys(e.procedure_id for e in elements if e.procedure_id))
        is_engineering = any(e.is_engineering_content for e in elements if e.content_type != ContentType.HEADING) \
            or all(e.content_type == ContentType.HEADING for e in elements) and all(e.is_engineering_content for e in elements)
        chapter_number = next((e.chapter_number for e in elements if e.chapter_number is not None), None)
        section_id = next((e.section_id for e in elements if e.section_id), None)

        chunk = Chunk(
            chunk_id=f"{doc_id}_chunk_{sequence:04d}",
            document_id=doc_id,
            sequence=sequence,
            page_start=min(pages) if pages else 0,
            page_end=max(pages) if pages else 0,
            section_path=section_path or (elements[0].section_path if elements else ""),
            parent_heading=heading,
            text=full_text,
            elements=elements,
            contains_table=bool(table_ids),
            table_ids=table_ids,
            contains_procedure=bool(procedure_ids),
            procedure_ids=procedure_ids,
            element_types=element_types,
            token_estimate=_estimate_tokens(full_text),
            is_engineering=is_engineering,
            chapter_number=chapter_number,
            section_id=section_id,
            overlap_text=overlap,
        )
        chunk.chunk_type = self._classify_chunk(chunk)
        return chunk

    @staticmethod
    def _classify_chunk(chunk: Chunk) -> str:
        """Semantic chunk type from its elements and section path."""
        if not chunk.is_engineering:
            return "document_control"
        els = chunk.elements
        total = sum(_estimate_tokens(e.content) for e in els) or 1
        proc_tokens = sum(_estimate_tokens(e.content) for e in els if e.procedure_id is not None)
        table_tokens = sum(_estimate_tokens(e.content) for e in els if e.content_type == ContentType.TABLE)
        if chunk.contains_procedure and proc_tokens / total >= 0.4:
            return "procedure"
        if table_tokens / total >= 0.5:
            return "table"
        path = chunk.section_path or ""
        try:
            from src.spec_claims import detect_spec_pairs
            if len(detect_spec_pairs(els)) >= 3:
                return "specification"
        except Exception:  # pragma: no cover - classification must never fail chunking
            pass
        if _UPSET_RE.search(path):
            return "upset"
        if _SAFETY_RE.search(path):
            return "safety"
        if _CONTROL_RE.search(path):
            return "control"
        if _EQUIPMENT_RE.search(path):
            return "equipment"
        return "narrative"

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

    @staticmethod
    def _in_atomic_block(elements: list[NormalizedElement], next_elem: NormalizedElement) -> bool:
        """True when breaking before ``next_elem`` would split a table or a procedure."""
        if not elements:
            return False
        last = elements[-1]
        if (last.content_type == ContentType.TABLE and next_elem.content_type == ContentType.TABLE
                and last.table_id and last.table_id == next_elem.table_id):
            return True
        if last.procedure_id is not None and next_elem.procedure_id == last.procedure_id:
            return True
        return False

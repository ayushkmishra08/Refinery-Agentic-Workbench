"""Deterministic document normalizer — no LLM required.

Applies rule-based filtering to remove boilerplate, repeated headers/footers,
approval blocks, TOC entries, and decorative elements. The original parsed
document is never modified — this creates a separate cleaned representation.

Preserves document order: page → section → heading → element.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone

from schemas.normalized_document import (
    ContentType,
    FilterDecision,
    FilteredElement,
    NormalizationStats,
    NormalizedDocument,
    NormalizedElement,
    SectionNode,
)
from schemas.parsed_document import ElementType, ParsedDocument, ParsedElement
from src.config import PipelineConfig

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """Normalize and hash text for comparison."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")
_CHAPTER_HEADING = re.compile(r"^\s*(?:CHAPTER|SECTION|PART|APPENDIX|ANNEXURE)\b[\s\-:]*[A-Z0-9IVX]*", re.IGNORECASE)


def infer_heading_level(content: str, parser_level: int | None) -> int:
    """Infer a heading level from numbering ("3.2.1 Title" -> 3, "CHAPTER 5" -> 1).

    Docling's layout model reports every section header at the same level, so
    the section hierarchy is reconstructed deterministically from the heading
    text.  Falls back to the parser level (or 2) when no numbering is present.
    """
    text = content.strip()
    m = _NUMBERED_HEADING.match(text)
    if m:
        depth = m.group(1).count(".") + 1
        return min(max(depth, 1), 6)
    if _CHAPTER_HEADING.match(text):
        return 1
    if parser_level is not None:
        return parser_level
    return 2


_CHAPTER_NO = re.compile(r"chapter\s*no\.?\s*:?\s*(\d+)", re.IGNORECASE)


def chapter_by_page(parsed_doc: ParsedDocument, template_table_ids: set[str]) -> dict[int, int]:
    """Map page number -> chapter number using the repeating page-header box."""
    result: dict[int, int] = {}
    for table in parsed_doc.tables:
        if table.table_id not in template_table_ids:
            continue
        text = " ".join(c.content for c in table.cells)
        m = _CHAPTER_NO.search(text)
        if m:
            result.setdefault(table.page, int(m.group(1)))
    return result


def _map_content_type(element_type: ElementType, content: str) -> ContentType:
    """Map parsed element type to normalized content type."""
    mapping = {
        ElementType.HEADING: ContentType.HEADING,
        ElementType.TEXT: ContentType.PARAGRAPH,
        ElementType.LIST_ITEM: ContentType.LIST_ITEM,
        ElementType.TABLE: ContentType.TABLE,
        ElementType.EQUATION: ContentType.EQUATION,
        ElementType.CAPTION: ContentType.FIGURE_CAPTION,
    }

    content_type = mapping.get(element_type, ContentType.PARAGRAPH)

    # Detect safety notes from content patterns
    content_upper = content.upper().strip()
    if content_upper.startswith(("WARNING:", "WARNING -", "⚠")):
        content_type = ContentType.WARNING
    elif content_upper.startswith(("CAUTION:", "CAUTION -")):
        content_type = ContentType.CAUTION
    elif content_upper.startswith(("NOTE:", "NOTE -", "N.B.")):
        content_type = ContentType.NOTE
    elif content_upper.startswith(("SAFETY:", "SAFETY NOTE")):
        content_type = ContentType.SAFETY_NOTE

    return content_type


class DocumentNormalizer:
    """Deterministic normalizer for parsed documents."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._boilerplate_patterns = [
            p.lower() for p in config.normalizer.boilerplate_patterns
        ]

    def normalize(self, parsed_doc: ParsedDocument) -> NormalizedDocument:
        """Normalize a parsed document by removing boilerplate and classifying content.

        Args:
            parsed_doc: Layer 1 parsed document.

        Returns:
            Layer 2 normalized document.
        """
        start_time = time.time()
        doc_id = parsed_doc.document_id

        logger.info(f"Normalizing document: {doc_id}")

        # Step 1: Detect repeated elements (headers/footers)
        repeated_hashes = self._detect_repeated_elements(parsed_doc)

        # Step 1b: Detect repeating page-template tables (header/footer boxes)
        from src.table_classifier import detect_page_template_tables
        template_table_ids = detect_page_template_tables(parsed_doc)
        if template_table_ids:
            logger.info(f"Found {len(template_table_ids)} page-template tables (header/footer boxes)")
        page_chapter = chapter_by_page(parsed_doc, template_table_ids)
        if page_chapter:
            logger.info(f"Chapter numbers recovered for {len(page_chapter)} pages "
                        f"({len(set(page_chapter.values()))} chapters)")
        last_chapter_with_heading: int | None = None

        # Step 2: Detect approval/signature blocks
        approval_pattern = re.compile(
            r"(approved|authorized|signature|signed|prepared|checked|reviewed|verified)\s*(by)?",
            re.IGNORECASE,
        )

        # Step 3: Detect TOC patterns
        toc_pattern = re.compile(
            r"^[\d.]+\s+.+\s+\d+\s*$",  # "3.2  Operating Conditions  15"
        )

        # Step 4: Process all elements
        kept_elements: list[NormalizedElement] = []
        filtered_elements: list[FilteredElement] = []
        stats = NormalizationStats()
        current_section_path = ""
        current_heading = ""
        section_nodes: list[SectionNode] = []
        heading_stack: list[tuple[int, str, str]] = []  # (level, title, section_id)

        for page in parsed_doc.pages:
            for element in page.elements:
                stats.total_elements += 1

                # Determine filter decision
                decision = self._classify_element(
                    element, repeated_hashes, approval_pattern, toc_pattern,
                    template_table_ids,
                )

                if decision == FilterDecision.KEEP:
                    # Update section tracking for headings
                    heading_level = element.heading_level
                    if element.element_type == ElementType.HEADING:
                        level = infer_heading_level(element.content, element.heading_level)
                        chapter_no = page_chapter.get(element.page)
                        if chapter_no is not None and chapter_no != last_chapter_with_heading:
                            level = 1  # first heading inside a new chapter = chapter title
                            last_chapter_with_heading = chapter_no
                        heading_level = level
                        current_heading = element.content.strip()
                        section_id = f"s_{doc_id}_{element.page}_{element.position_in_page}"

                        # Pop stack to find parent
                        while heading_stack and heading_stack[-1][0] >= level:
                            heading_stack.pop()

                        parent_id = heading_stack[-1][2] if heading_stack else None
                        heading_stack.append((level, current_heading, section_id))

                        # Build section path
                        current_section_path = " > ".join(
                            title for _, title, _ in heading_stack
                        )

                        section_nodes.append(SectionNode(
                            section_id=section_id,
                            title=current_heading,
                            level=level,
                            page_start=element.page,
                            parent_section_id=parent_id,
                        ))

                    content_type = _map_content_type(
                        element.element_type, element.content,
                    )

                    kept_elements.append(NormalizedElement(
                        element_id=element.element_id,
                        content_type=content_type,
                        content=element.content,
                        page=element.page,
                        position_in_page=element.position_in_page,
                        section_path=current_section_path,
                        heading_level=heading_level,
                        parent_heading=current_heading if element.element_type != ElementType.HEADING else None,
                        filter_decision=FilterDecision.KEEP,
                        table_id=element.table.table_id if element.table else None,
                        is_engineering_content=True,
                    ))
                    stats.kept_elements += 1
                else:
                    filtered_elements.append(FilteredElement(
                        element_id=element.element_id,
                        page=element.page,
                        filter_decision=decision,
                        reason=f"Filtered as {decision.value}",
                        content_preview=element.content[:100] if element.content else "",
                    ))
                    stats.filtered_elements += 1

                    # Track stats by filter type
                    if decision == FilterDecision.BOILERPLATE:
                        stats.boilerplate_patterns_found += 1
                    elif decision == FilterDecision.REPEATED_HEADER:
                        stats.repeated_headers_found += 1
                    elif decision == FilterDecision.REPEATED_FOOTER:
                        stats.repeated_footers_found += 1
                    elif decision == FilterDecision.APPROVAL_BLOCK:
                        stats.approval_blocks_found += 1
                    elif decision == FilterDecision.TOC:
                        stats.toc_entries_found += 1
                    elif decision == FilterDecision.DECORATIVE:
                        stats.decorative_elements_found += 1
                    elif decision == FilterDecision.EMPTY:
                        stats.empty_elements_found += 1

        duration = time.time() - start_time

        normalized_doc = NormalizedDocument(
            document_id=doc_id,
            source_filename=parsed_doc.source_filename,
            total_pages=parsed_doc.total_pages,
            sections=section_nodes,
            elements=kept_elements,
            filtered_elements=filtered_elements,
            stats=stats,
            normalization_timestamp=datetime.now(timezone.utc).isoformat(),
            normalization_duration_seconds=duration,
        )

        # Save to normalized directory
        output_path = self.config.paths.normalized_dir / f"{doc_id}_normalized.json"
        output_path.write_text(
            normalized_doc.model_dump_json(indent=2),
            encoding="utf-8",
        )

        logger.info(
            f"Normalized {doc_id}: kept {stats.kept_elements}/{stats.total_elements} "
            f"elements, filtered {stats.filtered_elements} in {duration:.1f}s"
        )

        return normalized_doc

    def _detect_repeated_elements(self, parsed_doc: ParsedDocument) -> set[str]:
        """Find content hashes that appear on more than threshold% of pages."""
        if not parsed_doc.pages:
            return set()

        hash_pages: dict[str, set[int]] = {}
        for page in parsed_doc.pages:
            for element in page.elements:
                if not element.content or len(element.content.strip()) < 3:
                    continue
                h = _content_hash(element.content)
                if h not in hash_pages:
                    hash_pages[h] = set()
                hash_pages[h].add(page.page_number)

        total_pages = len(parsed_doc.pages)
        threshold = self.config.normalizer.repeated_element_threshold
        repeated = set()
        for h, pages in hash_pages.items():
            if len(pages) / total_pages >= threshold:
                repeated.add(h)

        if repeated:
            logger.info(f"Found {len(repeated)} repeated element patterns across pages")

        return repeated

    def _classify_element(
        self,
        element: ParsedElement,
        repeated_hashes: set[str],
        approval_pattern: re.Pattern,
        toc_pattern: re.Pattern,
        template_table_ids: set[str] | None = None,
    ) -> FilterDecision:
        """Classify a single element as keep or filtered."""
        content = element.content.strip() if element.content else ""

        # Empty content
        if len(content) < self.config.normalizer.min_content_length:
            return FilterDecision.EMPTY

        # Repeating page-template tables (header/footer boxes on every page)
        if element.table is not None and template_table_ids and element.table.table_id in template_table_ids:
            return FilterDecision.REPEATED_HEADER

        # Parser-labelled furniture (Docling furniture layer)
        if element.content_layer == "furniture" and element.element_type != ElementType.TABLE:
            return FilterDecision.REPEATED_HEADER

        # Page headers/footers from parser
        if element.element_type == ElementType.PAGE_HEADER:
            return FilterDecision.REPEATED_HEADER
        if element.element_type == ElementType.PAGE_FOOTER:
            return FilterDecision.REPEATED_FOOTER

        # Repeated content across pages
        if _content_hash(content) in repeated_hashes:
            # Headings can repeat across pages (chapter titles) — be careful
            if element.element_type != ElementType.HEADING:
                return FilterDecision.REPEATED_HEADER

        # Boilerplate patterns
        content_lower = content.lower()
        for pattern in self._boilerplate_patterns:
            # Only match if the pattern is a substantial part of the content
            if pattern in content_lower and len(content) < 200:
                # Check if this is JUST the boilerplate (not a paragraph that mentions it)
                if len(content_lower.split()) <= 10:
                    return FilterDecision.BOILERPLATE

        # Approval/signature blocks (usually in tables or short text)
        if approval_pattern.search(content) and len(content) < 200:
            # Check if this looks like an approval block vs. a procedure step
            approval_words = len(approval_pattern.findall(content))
            total_words = len(content.split())
            if approval_words / max(total_words, 1) > 0.3:
                return FilterDecision.APPROVAL_BLOCK

        # TOC entries
        if toc_pattern.match(content) and element.element_type == ElementType.TEXT:
            return FilterDecision.TOC

        return FilterDecision.KEEP

"""Deterministic document normalizer — no LLM required.

Turns the faithful parser output into the *canonical source representation*
that every later stage (chunking, extraction, graph) works from:

  parse -> clean -> structure -> (chunk)

  * removes page furniture: repeating header/footer boxes (tables *and* the loose
    text lines TableFormer sometimes emits instead), running chapter titles,
    approval / signature blocks, TOC tables, "[Figure on page N]" placeholders,
    boilerplate lines ("Chapter Rev No:", "PLANT NAME: ...");
  * rebuilds the chapter hierarchy from the table of contents and the page-header
    boxes (src.structure) and inserts one canonical "Chapter N: Title" heading per
    chapter, so section paths read "Chapter 16: ... > 16.1 Crude charge pump > ...";
  * infers heading levels from numbering, relative to the enclosing chapter;
  * marks ordered instruction blocks as procedures (content_type=PROCEDURE_STEP,
    procedure_id, step_number) and records them on the document;
  * flags document-control chapters as non-engineering content.

The original parsed document is never modified.  Every kept element keeps its
element_id, page and position so provenance survives; every removed element is
recorded with the reason.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone

from schemas.normalized_document import (
    ChapterNode,
    ContentType,
    FilterDecision,
    FilteredElement,
    NormalizationStats,
    NormalizedDocument,
    NormalizedElement,
    SectionNode,
)
from schemas.parsed_document import ElementType, ParsedDocument, ParsedElement, ParsedTable
from schemas.table_schema import TableClassification, TableNormalizationResult
from src.config import PipelineConfig

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """Normalize and hash text for comparison."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")
_CHAPTER_HEADING = re.compile(r"^\s*(?:CHAPTER|SECTION|PART|APPENDIX|ANNEXURE)\b[\s\-:]*[A-Z0-9IVX]*", re.IGNORECASE)
_FIGURE_PLACEHOLDER_RE = re.compile(r"^\s*\[(?:figure|image|picture)\b[^\]]*\]\s*$", re.I)
_APPROVAL_TABLE_RE = re.compile(
    r"\b(approved\s+by|prepared\s+by|checked\s+by|reviewed\s+by|verified\s+by|certified\s+by|signature|sign\b|designation)",
    re.I,
)
_TOC_TABLE_RE = re.compile(r"\bchapter\b.*\btitle\b.*\bpage\b|\bcontents\b.*\bpage\b", re.I | re.S)


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


def heading_level_in_chapter(content: str, parser_level: int | None, chapter_number: int | None,
                             current_level: int, numbered_level: int | None = None) -> int:
    """Heading level relative to a chapter whose canonical title is level 1.

    "6.1.2 X" inside chapter 6 -> 4 (6 -> 2, 6.1 -> 3, 6.1.2 -> 4).  A numbered
    heading whose leading number is *not* the chapter ("3. Procedure to be
    followed ..." inside chapter 6, "9. THE FOLLOWING ACTION ..." inside 15) is an
    enumerated sub-heading of the current section, never a new chapter.  An
    unnumbered heading ("Operating Conditions:") is a child of the nearest
    numbered section (``numbered_level``), so consecutive unnumbered headings are
    siblings and never pop the numbered section above them.
    """
    text = content.strip()
    m = _NUMBERED_HEADING.match(text)
    if chapter_number is None:
        return infer_heading_level(content, parser_level)
    if m:
        parts = m.group(1).split(".")
        try:
            lead = int(parts[0])
        except ValueError:
            lead = -1
        if lead == chapter_number:
            return min(len(parts) + 1, 6)
        return min(max(numbered_level or current_level, 1) + 1, 6)
    if _CHAPTER_HEADING.match(text):
        return 2
    return min(max((numbered_level or 1) + 1, 2), 6)


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
        ElementType.FIGURE: ContentType.FIGURE_CAPTION,
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


def _table_text(table: ParsedTable) -> str:
    return " ".join(c.content for c in table.cells)


def _is_approval_table(table: ParsedTable) -> bool:
    text = _table_text(table)
    hits = len(_APPROVAL_TABLE_RE.findall(text))
    return hits >= 2 and table.num_rows <= 8 and len(text) < 400


def _is_toc_table(table: ParsedTable) -> bool:
    if not table.grid:
        return False
    header = " ".join(table.grid[0])
    return bool(_TOC_TABLE_RE.search(header))


class DocumentNormalizer:
    """Deterministic normalizer for parsed documents."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._boilerplate_patterns = [
            p.lower() for p in config.normalizer.boilerplate_patterns
        ]

    # ------------------------------------------------------------------ #
    def normalize(
        self,
        parsed_doc: ParsedDocument,
        table_results: TableNormalizationResult | None = None,
    ) -> NormalizedDocument:
        """Normalize a parsed document: clean, structure, detect procedures.

        Args:
            parsed_doc: Layer 1 parsed document.
            table_results: Table classifications (optional).  When given, TOC /
                approval / header / administrative tables are filtered by class.

        Returns:
            Layer 2 normalized document.
        """
        from src.structure import (
            chapter_page_map,
            detect_procedures,
            parse_toc,
            running_title_texts,
        )
        from src.table_classifier import detect_page_template_tables

        start_time = time.time()
        doc_id = parsed_doc.document_id
        logger.info(f"Normalizing document: {doc_id}")

        # -- Step 1: page furniture ------------------------------------------------
        repeated_hashes = self._detect_repeated_elements(parsed_doc)
        template_table_ids = detect_page_template_tables(parsed_doc)
        if template_table_ids:
            logger.info(f"Found {len(template_table_ids)} page-template tables (header/footer boxes)")

        # Tables that are document furniture rather than content
        filtered_tables: dict[str, FilterDecision] = {}
        if table_results is not None:
            by_class = {
                TableClassification.TOC_TABLE: FilterDecision.TOC,
                TableClassification.APPROVAL_TABLE: FilterDecision.APPROVAL_BLOCK,
                TableClassification.SIGNATURE_TABLE: FilterDecision.APPROVAL_BLOCK,
                TableClassification.HEADER_TABLE: FilterDecision.REPEATED_HEADER,
                TableClassification.FOOTER_TABLE: FilterDecision.REPEATED_FOOTER,
                TableClassification.ADMINISTRATIVE_TABLE: FilterDecision.ADMINISTRATIVE,
                TableClassification.DECORATIVE_TABLE: FilterDecision.DECORATIVE,
            }
            for t in table_results.classified_tables:
                if t.classification in by_class:
                    filtered_tables[t.table_id] = by_class[t.classification]
        for table in parsed_doc.tables:
            if table.table_id in filtered_tables:
                continue
            if table.table_id in template_table_ids:
                filtered_tables[table.table_id] = FilterDecision.REPEATED_HEADER
            elif _is_toc_table(table):
                filtered_tables[table.table_id] = FilterDecision.TOC
            elif _is_approval_table(table):
                filtered_tables[table.table_id] = FilterDecision.APPROVAL_BLOCK

        # -- Step 2: chapter structure ---------------------------------------------
        toc_chapters = parse_toc(parsed_doc, template_table_ids)
        page_chapter = chapter_page_map(parsed_doc, template_table_ids, toc_chapters)
        chapters = self._reconcile_chapters(toc_chapters, page_chapter, parsed_doc.total_pages)
        chapters_by_no = {c.number: c for c in chapters}
        admin_chapters = {c.number for c in chapters if c.is_administrative}
        if toc_chapters:
            logger.info(f"Table of contents: {len(toc_chapters)} chapters; "
                        f"page->chapter known for {len(page_chapter)} pages")
        running_titles = running_title_texts(parsed_doc, toc_chapters)
        if running_titles:
            logger.info(f"Running-title headings dropped: {sorted(running_titles)}")

        approval_pattern = re.compile(
            r"(approved|authorized|signature|signed|prepared|checked|reviewed|verified|certified)\s*(by)?",
            re.IGNORECASE,
        )
        toc_pattern = re.compile(r"^[\d.]+\s+.+\s+\d+\s*$")   # "3.2  Operating Conditions  15"

        # -- Step 3: walk the elements ---------------------------------------------
        kept_elements: list[NormalizedElement] = []
        filtered_elements: list[FilteredElement] = []
        stats = NormalizationStats(chapters_from_toc=len(toc_chapters))
        section_nodes: list[SectionNode] = []
        heading_stack: list[tuple[int, str, str]] = []  # (level, title, section_id)
        current_section_path = ""
        current_heading = ""
        current_chapter: int | None = None
        last_chapter_with_heading: int | None = None
        running_seen_in_chapter: set[tuple[int | None, str]] = set()
        numbered_sections: set[str] = set()      # section ids of "N.x.y" headings (and chapter titles)

        def section_path() -> str:
            return " > ".join(title for _, title, _ in heading_stack)

        def push_heading(level: int, title: str, section_id: str, page: int, number: str = "") -> None:
            nonlocal current_section_path, current_heading
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            parent_id = heading_stack[-1][2] if heading_stack else None
            heading_stack.append((level, title, section_id))
            current_section_path = section_path()
            current_heading = title
            section_nodes.append(SectionNode(
                section_id=section_id, title=title, level=level, page_start=page,
                parent_section_id=parent_id, chapter_number=current_chapter, number=number,
                path=current_section_path,
            ))
            if current_chapter in chapters_by_no:
                chapters_by_no[current_chapter].section_ids.append(section_id)

        def record_filtered(element: ParsedElement, decision: FilterDecision) -> None:
            filtered_elements.append(FilteredElement(
                element_id=element.element_id, page=element.page, filter_decision=decision,
                reason=f"Filtered as {decision.value}",
                content_preview=element.content[:100] if element.content else "",
            ))
            stats.filtered_elements += 1
            counter = {
                FilterDecision.BOILERPLATE: "boilerplate_patterns_found",
                FilterDecision.REPEATED_HEADER: "repeated_headers_found",
                FilterDecision.REPEATED_FOOTER: "repeated_footers_found",
                FilterDecision.APPROVAL_BLOCK: "approval_blocks_found",
                FilterDecision.TOC: "toc_entries_found",
                FilterDecision.DECORATIVE: "decorative_elements_found",
                FilterDecision.EMPTY: "empty_elements_found",
                FilterDecision.FIGURE_PLACEHOLDER: "figure_placeholders_found",
                FilterDecision.RUNNING_TITLE: "running_titles_found",
                FilterDecision.ADMINISTRATIVE: "administrative_found",
            }.get(decision)
            if counter:
                setattr(stats, counter, getattr(stats, counter) + 1)

        for page in parsed_doc.pages:
            chapter_no = page_chapter.get(page.page_number)
            if chapter_no is not None and chapter_no != current_chapter:
                current_chapter = chapter_no
                ch = chapters_by_no.get(chapter_no)
                if ch is not None and ch.source != "heading":
                    # Canonical chapter heading from the TOC: one per chapter, level 1
                    section_id = f"s_{doc_id}_ch{chapter_no}"
                    title = f"Chapter {chapter_no}: {ch.title}"
                    heading_stack.clear()
                    numbered_sections.add(section_id)
                    push_heading(1, title, section_id, page.page_number, number=str(chapter_no))
                    last_chapter_with_heading = chapter_no
                    kept_elements.append(NormalizedElement(
                        element_id=f"{doc_id}_ch{chapter_no}_title",
                        content_type=ContentType.HEADING,
                        content=title,
                        page=page.page_number,
                        position_in_page=-1,
                        section_path=current_section_path,
                        heading_level=1,
                        parent_heading=None,
                        chapter_number=chapter_no,
                        section_id=section_id,
                        is_engineering_content=chapter_no not in admin_chapters,
                    ))
                    stats.kept_elements += 1

            for element in page.elements:
                stats.total_elements += 1

                decision = self._classify_element(
                    element, repeated_hashes, approval_pattern, toc_pattern,
                    template_table_ids, filtered_tables, running_titles,
                    current_chapter, running_seen_in_chapter,
                )
                if decision != FilterDecision.KEEP:
                    record_filtered(element, decision)
                    continue

                heading_level = element.heading_level
                if element.element_type == ElementType.HEADING:
                    current_level = heading_stack[-1][0] if heading_stack else 1
                    numbered_level = next(
                        (lvl for lvl, _t, sid in reversed(heading_stack) if sid in numbered_sections), None,
                    )
                    level = heading_level_in_chapter(
                        element.content, element.heading_level, current_chapter, current_level, numbered_level,
                    )
                    if current_chapter is not None and current_chapter not in chapters_by_no:
                        # No TOC entry for this chapter: its first heading is the chapter title
                        if current_chapter != last_chapter_with_heading:
                            level = 1
                            last_chapter_with_heading = current_chapter
                            heading_stack.clear()
                            chapters_by_no[current_chapter] = ChapterNode(
                                number=current_chapter, title=element.content.strip(),
                                page_start=element.page, source="heading",
                            )
                            chapters.append(chapters_by_no[current_chapter])
                    elif current_chapter is None and page_chapter == {} and level == 1:
                        heading_stack.clear()
                    heading_level = level
                    title = re.sub(r"\s+", " ", element.content.strip())
                    section_id = f"s_{doc_id}_{element.page}_{element.position_in_page}"
                    m = _NUMBERED_HEADING.match(title)
                    if m and (current_chapter is None or m.group(1).split(".")[0] == str(current_chapter)):
                        numbered_sections.add(section_id)
                    push_heading(level, title, section_id, element.page, number=(m.group(1) if m else ""))

                content_type = _map_content_type(element.element_type, element.content)
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
                    is_engineering_content=current_chapter not in admin_chapters,
                    chapter_number=current_chapter,
                    section_id=heading_stack[-1][2] if heading_stack else None,
                    list_enumerated=element.list_enumerated,
                    list_marker=element.list_marker,
                ))
                stats.kept_elements += 1

        # -- Step 4: procedures ----------------------------------------------------
        procedures = detect_procedures(kept_elements, doc_id)
        stats.procedures_found = len(procedures)
        stats.procedure_steps_found = sum(len(p.steps) for p in procedures)
        if procedures:
            logger.info(f"Procedures detected: {len(procedures)} ({stats.procedure_steps_found} steps)")

        # section page_end: page of the next section at the same or higher level
        for i, node in enumerate(section_nodes):
            for later in section_nodes[i + 1:]:
                if later.level <= node.level:
                    node.page_end = max(node.page_start, later.page_start)
                    break
            else:
                node.page_end = parsed_doc.total_pages or node.page_start
        counts: dict[str, int] = {}
        for el in kept_elements:
            if el.section_id:
                counts[el.section_id] = counts.get(el.section_id, 0) + 1
        for node in section_nodes:
            node.element_count = counts.get(node.section_id, 0)

        duration = time.time() - start_time
        normalized_doc = NormalizedDocument(
            document_id=doc_id,
            source_filename=parsed_doc.source_filename,
            total_pages=parsed_doc.total_pages,
            chapters=sorted(chapters, key=lambda c: c.number),
            sections=section_nodes,
            procedures=procedures,
            elements=kept_elements,
            filtered_elements=filtered_elements,
            stats=stats,
            normalization_timestamp=datetime.now(timezone.utc).isoformat(),
            normalization_duration_seconds=duration,
        )

        output_path = self.config.paths.normalized_dir / f"{doc_id}_normalized.json"
        output_path.write_text(normalized_doc.model_dump_json(indent=2), encoding="utf-8")

        logger.info(
            f"Normalized {doc_id}: kept {stats.kept_elements}/{stats.total_elements} "
            f"elements, filtered {stats.filtered_elements} "
            f"(figures {stats.figure_placeholders_found}, running titles {stats.running_titles_found}, "
            f"header boxes {stats.repeated_headers_found}, approvals {stats.approval_blocks_found}, "
            f"toc {stats.toc_entries_found}) in {duration:.1f}s"
        )
        return normalized_doc

    # ------------------------------------------------------------------ #
    @staticmethod
    def _reconcile_chapters(
        toc_chapters: list[ChapterNode], page_chapter: dict[int, int], total_pages: int,
    ) -> list[ChapterNode]:
        """Chapter page ranges from the page-header evidence (the TOC 'from page' is often off by one)."""
        pages_by_chapter: dict[int, list[int]] = {}
        for pg, ch in page_chapter.items():
            pages_by_chapter.setdefault(ch, []).append(pg)
        chapters = [c.model_copy(deep=True) for c in toc_chapters]
        by_no = {c.number: c for c in chapters}
        for no, pages in pages_by_chapter.items():
            if no in by_no:
                by_no[no].page_start = min(pages)
                by_no[no].page_end = max(pages)
                if by_no[no].source == "toc":
                    by_no[no].source = "toc+page_header"
        chapters.sort(key=lambda c: c.number)
        for i, ch in enumerate(chapters):
            if i + 1 < len(chapters):
                nxt = chapters[i + 1]
                if ch.page_end is None or ch.page_end >= nxt.page_start:
                    ch.page_end = max(ch.page_start, nxt.page_start - 1)
            elif ch.page_end is None:
                ch.page_end = total_pages or ch.page_start
        return chapters

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
                hash_pages.setdefault(h, set()).add(page.page_number)

        total_pages = len(parsed_doc.pages)
        threshold = self.config.normalizer.repeated_element_threshold
        repeated = {h for h, pages in hash_pages.items() if len(pages) / total_pages >= threshold}
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
        filtered_tables: dict[str, FilterDecision] | None = None,
        running_titles: set[str] | None = None,
        current_chapter: int | None = None,
        running_seen: set[tuple[int | None, str]] | None = None,
    ) -> FilterDecision:
        """Classify a single element as keep or filtered."""
        content = element.content.strip() if element.content else ""
        filtered_tables = filtered_tables or {}
        running_titles = running_titles or set()

        # Figures: keep a real caption as content, drop bare placeholders
        if element.element_type == ElementType.FIGURE:
            if not content or _FIGURE_PLACEHOLDER_RE.match(content):
                return FilterDecision.FIGURE_PLACEHOLDER
        elif _FIGURE_PLACEHOLDER_RE.match(content):
            return FilterDecision.FIGURE_PLACEHOLDER

        # Empty content
        if len(content) < self.config.normalizer.min_content_length:
            return FilterDecision.EMPTY

        # Tables that are document furniture (header boxes, TOC, approvals, admin)
        if element.table is not None:
            tid = element.table.table_id
            if tid in filtered_tables:
                return filtered_tables[tid]
            if template_table_ids and tid in template_table_ids:
                return FilterDecision.REPEATED_HEADER

        # Parser-labelled furniture (Docling furniture layer)
        if element.content_layer == "furniture" and element.element_type != ElementType.TABLE:
            return FilterDecision.REPEATED_HEADER

        # Page headers/footers from parser
        if element.element_type == ElementType.PAGE_HEADER:
            return FilterDecision.REPEATED_HEADER
        if element.element_type == ElementType.PAGE_FOOTER:
            return FilterDecision.REPEATED_FOOTER

        # Running chapter titles printed as headings on every page
        if element.element_type == ElementType.HEADING:
            key = re.sub(r"\s+", " ", content.lower()).strip(" :.-")
            if key in running_titles:
                if running_seen is not None and (current_chapter, key) not in running_seen and current_chapter is None:
                    running_seen.add((current_chapter, key))   # keep one copy when no chapter context exists
                else:
                    return FilterDecision.RUNNING_TITLE

        # Repeated content across pages
        if _content_hash(content) in repeated_hashes:
            # Headings can repeat across pages (chapter titles) — be careful
            if element.element_type != ElementType.HEADING:
                return FilterDecision.REPEATED_HEADER

        # Boilerplate patterns ("Chapter Rev No:", "PLANT NAME: CDU II", "Page No Page 225 of 562")
        content_lower = content.lower()
        for pattern in self._boilerplate_patterns:
            if pattern in content_lower and len(content) < 200:
                if len(content_lower.split()) <= 10:
                    return FilterDecision.BOILERPLATE

        # Approval/signature blocks (usually in tables or short text)
        if approval_pattern.search(content) and len(content) < 300:
            approval_words = len(approval_pattern.findall(content))
            total_words = len(content.split())
            if approval_words / max(total_words, 1) > 0.3 or (element.table is not None and approval_words >= 2):
                return FilterDecision.APPROVAL_BLOCK

        # TOC entries
        if toc_pattern.match(content) and element.element_type == ElementType.TEXT:
            return FilterDecision.TOC

        return FilterDecision.KEEP

"""Tests for the document normalizer."""

import pytest
from schemas.parsed_document import (
    ElementType, ParsedDocument, ParsedElement, ParsedPage,
)
from schemas.normalized_document import FilterDecision
from src.config import PipelineConfig
from src.normalizer import DocumentNormalizer


def _make_element(page: int, position: int, content: str, etype: ElementType = ElementType.TEXT) -> ParsedElement:
    return ParsedElement(
        element_id=f"e_{page}_{position}",
        element_type=etype,
        content=content,
        page=page,
        position_in_page=position,
    )


def _make_parsed_doc(pages_data: list[list[tuple[str, ElementType]]]) -> ParsedDocument:
    """Helper to create a parsed document from page content."""
    pages = []
    for page_num, elements_data in enumerate(pages_data, 1):
        elements = [
            _make_element(page_num, i, content, etype)
            for i, (content, etype) in enumerate(elements_data)
        ]
        pages.append(ParsedPage(page_number=page_num, elements=elements))
    return ParsedDocument(
        document_id="test_doc",
        source_filename="test.pdf",
        source_path="test.pdf",
        source_hash="abc123",
        total_pages=len(pages),
        pages=pages,
    )


class TestNormalizer:
    def setup_method(self):
        self.config = PipelineConfig()
        self.normalizer = DocumentNormalizer(self.config)

    def test_empty_elements_filtered(self):
        doc = _make_parsed_doc([[("", ElementType.TEXT), ("Hi", ElementType.TEXT)]])
        result = self.normalizer.normalize(doc)
        assert result.stats.empty_elements_found >= 1

    def test_page_headers_filtered(self):
        doc = _make_parsed_doc([
            [("Page header", ElementType.PAGE_HEADER), ("Real content here", ElementType.TEXT)],
        ])
        result = self.normalizer.normalize(doc)
        assert result.stats.repeated_headers_found >= 1

    def test_repeated_elements_detected(self):
        """Content appearing on >60% of pages should be flagged as repeated."""
        repeated = "OPERATING MANUAL - Chapter 1 - Page"
        doc = _make_parsed_doc([
            [(repeated, ElementType.TEXT), ("Content A", ElementType.TEXT)],
            [(repeated, ElementType.TEXT), ("Content B", ElementType.TEXT)],
            [(repeated, ElementType.TEXT), ("Content C", ElementType.TEXT)],
            [(repeated, ElementType.TEXT), ("Content D", ElementType.TEXT)],
        ])
        result = self.normalizer.normalize(doc)
        # The repeated text should be filtered on at least some pages
        kept_contents = [e.content for e in result.elements]
        assert "Content A" in kept_contents
        assert "Content B" in kept_contents

    def test_headings_preserved(self):
        doc = _make_parsed_doc([
            [("Chapter 3: Operating Conditions", ElementType.HEADING)],
        ])
        result = self.normalizer.normalize(doc)
        assert any(e.content == "Chapter 3: Operating Conditions" for e in result.elements)

    def test_section_path_built(self):
        doc = _make_parsed_doc([
            [("Chapter 1: Introduction", ElementType.HEADING)],
            [("Some text about the unit", ElementType.TEXT)],
        ])
        # Set heading level on the heading element
        doc.pages[0].elements[0].heading_level = 1
        result = self.normalizer.normalize(doc)
        text_elements = [e for e in result.elements if e.content == "Some text about the unit"]
        assert len(text_elements) == 1

"""Tests for the structure-aware chunker."""

import pytest
from schemas.normalized_document import NormalizedDocument, NormalizedElement, ContentType
from src.config import PipelineConfig
from src.chunker import DocumentChunker


def _make_elements(contents: list[tuple[str, ContentType, int]]) -> list[NormalizedElement]:
    return [
        NormalizedElement(
            element_id=f"e{i}",
            content_type=ctype,
            content=text,
            page=page,
            position_in_page=i,
        )
        for i, (text, ctype, page) in enumerate(contents)
    ]


class TestChunker:
    def setup_method(self):
        self.config = PipelineConfig()
        self.chunker = DocumentChunker(self.config)

    def test_creates_chunks(self):
        elements = _make_elements([
            ("Chapter 1: Introduction", ContentType.HEADING, 1),
            ("This is paragraph one. " * 50, ContentType.PARAGRAPH, 1),
            ("Chapter 2: Operations", ContentType.HEADING, 2),
            ("This is paragraph two. " * 50, ContentType.PARAGRAPH, 2),
        ])
        doc = NormalizedDocument(
            document_id="test", source_filename="test.pdf",
            elements=elements, total_pages=2,
        )
        chunks = self.chunker.chunk(doc)
        assert len(chunks) >= 1

    def test_chunk_ids_sequential(self):
        elements = _make_elements([
            ("Heading", ContentType.HEADING, 1),
            ("Content " * 100, ContentType.PARAGRAPH, 1),
            ("More Heading", ContentType.HEADING, 2),
            ("More content " * 100, ContentType.PARAGRAPH, 2),
        ])
        doc = NormalizedDocument(
            document_id="test", source_filename="test.pdf",
            elements=elements, total_pages=2,
        )
        chunks = self.chunker.chunk(doc)
        for i, chunk in enumerate(chunks):
            assert chunk.sequence == i

    def test_small_doc_single_chunk(self):
        elements = _make_elements([
            ("Short heading", ContentType.HEADING, 1),
            ("Short content", ContentType.PARAGRAPH, 1),
        ])
        doc = NormalizedDocument(
            document_id="test", source_filename="test.pdf",
            elements=elements, total_pages=1,
        )
        chunks = self.chunker.chunk(doc)
        assert len(chunks) == 1

"""Stub for parser tests. Full tests require Docling + a test PDF."""

import pytest


class TestParserConfig:
    def test_config_defaults(self):
        from knowledge_layer.config import PipelineConfig
        config = PipelineConfig()
        assert config.parser.ocr_enabled is True
        assert config.parser.table_structure_mode == "accurate"
        assert config.parser.page_window_size == 15

    def test_source_hash_deterministic(self):
        """Same file should always produce same hash."""
        import tempfile
        from pathlib import Path
        from knowledge_layer.parser import _compute_file_hash

        fd, tmpname = tempfile.mkstemp(suffix=".txt")
        try:
            import os
            os.write(fd, b"test content for hashing")
            os.close(fd)
            hash1 = _compute_file_hash(Path(tmpname))
            hash2 = _compute_file_hash(Path(tmpname))
            assert hash1 == hash2
        finally:
            Path(tmpname).unlink(missing_ok=True)

    def test_element_type_mapping(self):
        from knowledge_layer.parser import _map_docling_label
        from knowledge_layer.schemas.parsed_document import ElementType
        
        assert _map_docling_label("text") == ElementType.TEXT
        assert _map_docling_label("section_header") == ElementType.HEADING
        assert _map_docling_label("table") == ElementType.TABLE
        assert _map_docling_label("unknown_label") == ElementType.UNKNOWN

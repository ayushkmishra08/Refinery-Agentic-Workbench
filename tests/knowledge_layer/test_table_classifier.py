"""Tests for the table classifier."""

import pytest
from knowledge_layer.schemas.parsed_document import ParsedTable, ParsedTableCell, ParsedDocument, ParsedPage
from knowledge_layer.schemas.table_schema import TableClassification
from knowledge_layer.config import PipelineConfig
from knowledge_layer.table_classifier import TableClassifier


def _make_table(
    table_id: str, page: int, cells: list[tuple[int, int, str, bool]], caption: str = ""
) -> ParsedTable:
    return ParsedTable(
        table_id=table_id,
        page=page,
        num_rows=max((c[0] for c in cells), default=0) + 1,
        num_cols=max((c[1] for c in cells), default=0) + 1,
        cells=[
            ParsedTableCell(row=r, col=c, content=text, is_header=is_h)
            for r, c, text, is_h in cells
        ],
        caption=caption if caption else None,
    )


class TestTableClassifier:
    def setup_method(self):
        self.config = PipelineConfig()
        self.classifier = TableClassifier(self.config)

    def test_abbreviation_table_detected(self):
        table = _make_table("t1", 1, [
            (0, 0, "Abbreviation", True),
            (0, 1, "Full Form", True),
            (1, 0, "CDU", False),
            (1, 1, "Crude Distillation Unit", False),
            (2, 0, "VDU", False),
            (2, 1, "Vacuum Distillation Unit", False),
        ])
        doc = ParsedDocument(
            document_id="test", source_filename="test.pdf",
            source_path="test.pdf", source_hash="abc",
            total_pages=10, pages=[], tables=[table],
        )
        result = self.classifier.classify_all(doc)
        assert len(result.classified_tables) == 1
        assert result.classified_tables[0].classification == TableClassification.ABBREVIATION_TABLE

    def test_equipment_table_detected(self):
        table = _make_table("t2", 5, [
            (0, 0, "Equipment Tag", True),
            (0, 1, "Description", True),
            (1, 0, "P-101", False),
            (1, 1, "Crude Charge Pump", False),
            (2, 0, "P-102", False),
            (2, 1, "Reflux Pump", False),
            (3, 0, "E-201", False),
            (3, 1, "Feed/Bottoms Exchanger", False),
        ])
        doc = ParsedDocument(
            document_id="test", source_filename="test.pdf",
            source_path="test.pdf", source_hash="abc",
            total_pages=10, pages=[], tables=[table],
        )
        result = self.classifier.classify_all(doc)
        assert result.classified_tables[0].classification == TableClassification.EQUIPMENT_TABLE

    def test_approval_table_detected(self):
        table = _make_table("t3", 1, [
            (0, 0, "Prepared By", True),
            (0, 1, "Approved By", True),
            (0, 2, "Date", True),
            (1, 0, "John", False),
            (1, 1, "Jane", False),
            (1, 2, "2024-01-01", False),
        ])
        doc = ParsedDocument(
            document_id="test", source_filename="test.pdf",
            source_path="test.pdf", source_hash="abc",
            total_pages=10, pages=[], tables=[table],
        )
        result = self.classifier.classify_all(doc)
        assert result.classified_tables[0].classification == TableClassification.APPROVAL_TABLE

    def test_process_data_table_detected(self):
        table = _make_table("t4", 10, [
            (0, 0, "Parameter", True),
            (0, 1, "Value", True),
            (0, 2, "Unit", True),
            (1, 0, "Flow Rate", False),
            (1, 1, "1500", False),
            (1, 2, "kg/h", False),
            (2, 0, "Pressure", False),
            (2, 1, "10", False),
            (2, 2, "barg", False),
            (3, 0, "Temperature", False),
            (3, 1, "350", False),
            (3, 2, "°C", False),
        ])
        doc = ParsedDocument(
            document_id="test", source_filename="test.pdf",
            source_path="test.pdf", source_hash="abc",
            total_pages=10, pages=[], tables=[table],
        )
        result = self.classifier.classify_all(doc)
        cls = result.classified_tables[0].classification
        assert cls in (
            TableClassification.PROCESS_DATA_TABLE,
            TableClassification.OPERATING_LIMIT_TABLE,
        )

    def test_engineering_tables_enter_extraction(self):
        """Engineering tables should be marked for extraction."""
        table = _make_table("t5", 5, [
            (0, 0, "Equipment", True),
            (0, 1, "Power", True),
            (1, 0, "P-101", False),
            (1, 1, "75 kW", False),
            (2, 0, "P-102", False),
            (2, 1, "55 kW", False),
            (3, 0, "K-301", False),
            (3, 1, "500 kW", False),
        ])
        doc = ParsedDocument(
            document_id="test", source_filename="test.pdf",
            source_path="test.pdf", source_hash="abc",
            total_pages=10, pages=[], tables=[table],
        )
        result = self.classifier.classify_all(doc)
        assert result.classified_tables[0].enters_extraction is True

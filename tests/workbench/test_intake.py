"""On-device OCR and the intake pipeline (no vision model in tests)."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from workbench.intake.ocr import OcrLine, OcrResult, engine_available, is_scanned_pdf, ocr_image, ocr_pdf
from workbench.intake.pipeline import IntakeResult, _flags_from_ocr, intake_file, intake_to_chunks
from workbench.intake.vision import describe_image

ROOT = Path(__file__).resolve().parents[2]
TEST_PDF = ROOT / "data" / "sample docs" / "test.pdf"
needs_ocr = pytest.mark.skipif(not engine_available(), reason="RapidOCR engine not available")
needs_pdf = pytest.mark.skipif(not TEST_PDF.exists(), reason="sample test.pdf missing")


def _font(size: int):
    for name in ("DejaVuSans.ttf", "arial.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _label_image(path: Path, text: str = "PRESSURE 24.45 kg/cm2") -> Path:
    img = Image.new("RGB", (900, 160), "white")
    ImageDraw.Draw(img).text((20, 40), text, fill="black", font=_font(48))
    img.save(path)
    return path


@needs_ocr
def test_ocr_reads_a_rendered_label(tmp_path):
    res = ocr_image(_label_image(tmp_path / "label.png"))
    assert res.engine == "rapidocr"
    assert res.lines, "no lines read"
    assert "24" in res.text
    assert res.mean_confidence > 0.3
    assert all(0.0 <= ln.confidence <= 1.0 for ln in res.lines)


@needs_pdf
def test_test_pdf_is_born_digital():
    assert is_scanned_pdf(TEST_PDF) is False


@needs_pdf
def test_ocr_pdf_takes_the_text_layer():
    res = ocr_pdf(TEST_PDF, pages=[1])
    assert res.page_count == 1
    assert "Crude" in res.text
    assert all(ln.note == "text layer" and ln.confidence == 1.0 for ln in res.lines)
    assert res.low_confidence_lines == 0


@needs_ocr
def test_intake_image_without_vision(tmp_path):
    res = intake_file(_label_image(tmp_path / "gauge.png"), run_vision=False)
    assert isinstance(res, IntakeResult)
    assert res.kind == "image" and res.pages == 1
    assert res.ocr is not None and res.vision == []
    rows = intake_to_chunks(res)
    assert rows and rows[0]["source"] == "ocr" and "24" in rows[0]["text"]
    assert res.summary["vision_calls"] == 0


def test_low_confidence_lines_are_flagged_never_dropped():
    ocr = OcrResult(source="x", lines=[
        OcrLine(text="Design pressure 31.7 kg/cm2", confidence=0.97, page=0),
        OcrLine(text="Suction 2.0 kg/cm2A", confidence=0.4, page=0),
    ], threshold=0.6)
    flags = _flags_from_ocr(ocr, 0.6)
    assert len(flags) == 1 and flags[0].text.startswith("Suction") and "0.40" in flags[0].reason
    res = IntakeResult(source="x", kind="image", ocr=ocr, flagged=flags)
    rows = intake_to_chunks(res)
    assert any("Suction" in r["text"] for r in rows)     # still indexed, just flagged


def test_vision_without_a_model_reports_unavailable(tmp_path):
    img = _label_image(tmp_path / "v.png")
    note = describe_image(img, None, purpose="pid")
    assert note.ok is False and note.error == "no vision model"

    class Dead:
        def available(self):
            return False

        def describe_image(self, *a, **k):
            raise AssertionError("must not be called")

    note = describe_image(img, Dead())
    assert note.ok is False and note.error == "no vision model"


def test_vision_note_extracts_tags_and_numbers(tmp_path):
    img = _label_image(tmp_path / "v.png")

    class Fake:
        vision_model = "fake-vision"

        def available(self):
            return True

        def describe_image(self, path, prompt, max_tokens=500):
            assert "P&ID" in prompt or "piping" in prompt.lower()
            return "Pump 11-P-01A discharges to 12-C-01 via line 6-CR-101; TIC-1001 reads 350 [illegible] deg C."

    note = describe_image(img, Fake(), purpose="pid")
    assert note.ok and note.model == "fake-vision"
    assert "11-P-01A" in note.tags_found and "TIC-1001" in note.tags_found
    assert "350" in note.numbers_found and note.illegible_spans == 1

"""Multimodal intake: on-device OCR (RapidOCR, ONNX on CPU) and vision (the profile's local
vision model through Ollama) for scanned PDFs, handwritten notes, P&IDs and photographs.

Nothing here leaves the machine. Every OCR line carries a confidence; every line under the
threshold is flagged for human review rather than passed through as fact.
"""
from workbench.intake.ocr import OcrLine, OcrResult, is_scanned_pdf, ocr_image, ocr_pdf
from workbench.intake.pipeline import IntakeResult, intake_file, intake_to_chunks
from workbench.intake.vision import VisionNote, describe_image

__all__ = [
    "OcrLine", "OcrResult", "is_scanned_pdf", "ocr_image", "ocr_pdf",
    "IntakeResult", "intake_file", "intake_to_chunks",
    "VisionNote", "describe_image",
]

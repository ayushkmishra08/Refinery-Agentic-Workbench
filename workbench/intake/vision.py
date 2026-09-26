"""Vision reading with the profile's local vision model (Ollama, e.g. qwen3.5:2b).

Purpose-specific prompts: a P&ID is read for tags, lines and instruments; handwriting is
transcribed literally with ``[illegible]`` where the model cannot read; a gauge or a photograph
is read for the visible reading and its units, and told not to guess. The model's words are
a *note*, never a fact: tags and numbers it mentions are extracted so they can be checked
against the knowledge layer and flagged for review.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TAG_RE = re.compile(r"\b\d{1,3}\s*-\s*[A-Z]{1,4}\s*-\s*\d{1,5}(?:\s*[A-Z](?:\s*/\s*[A-Z])*)?\b|\b[A-Z]{1,4}-\d{2,5}[A-Z]?\b")
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])")
ILLEGIBLE_RE = re.compile(r"\[illegible\]", re.IGNORECASE)

PROMPTS: dict[str, str] = {
    "general": ("Describe this image for a refinery engineer: equipment, tags, readings, labels, warnings. "
                "Be literal. Do not guess values that are not visible; say 'not visible' instead."),
    "pid": ("This is a piping and instrumentation diagram (P&ID) or process drawing. List, as plainly as you can: "
            "(1) every equipment tag you can read (e.g. 11-P-01A, 12-C-01), (2) line numbers, (3) instrument tags "
            "(e.g. TIC-1001, PT-203) and what they measure, (4) the flow direction between equipment. "
            "Only report what is legible. Write 'not legible' for anything you cannot read; never invent a tag."),
    "handwriting": ("Transcribe the handwritten text in this image literally, line by line, keeping numbers and units "
                    "exactly as written. Write [illegible] for any word you cannot read. Do not paraphrase or correct."),
    "photo": ("Describe what this photograph shows for a refinery engineer: the equipment, its condition, any visible "
              "labels, tags, gauges and readings with their units. Only report what is visible; do not guess."),
    "gauge": ("Read the instrument in this image: the value shown, its units and the range on the scale. "
              "If the reading is not clearly legible, say so instead of guessing."),
}


class VisionNote(BaseModel):
    path: str
    purpose: str = "general"
    model: str | None = None
    text: str = ""
    tags_found: list[str] = Field(default_factory=list)
    numbers_found: list[str] = Field(default_factory=list)
    illegible_spans: int = 0
    duration_ms: int = 0
    ok: bool = False
    error: str | None = None


def _resolve_model(obj: Any) -> str | None:
    llm = getattr(obj, "llm", obj)
    return getattr(llm, "vision_model", None) or getattr(llm, "model", None)


def describe_image(path: str | Path, llm_or_resources: Any, *, purpose: str = "general", max_tokens: int = 500,
                   extra_prompt: str = "") -> VisionNote:
    """Ask the local vision model about one image. Never raises; ``ok`` says whether it worked."""
    t0 = time.time()
    note = VisionNote(path=str(path), purpose=purpose if purpose in PROMPTS else "general")
    prompt = PROMPTS[note.purpose] + (f"\n{extra_prompt}" if extra_prompt else "")
    if llm_or_resources is None:
        note.error = "no vision model"
        return note
    if not Path(path).exists():
        note.error = f"file not found: {path}"
        return note
    describe = getattr(llm_or_resources, "describe_image", None)
    if describe is None:
        note.error = "no vision model"
        return note
    available = getattr(llm_or_resources, "available", None) or getattr(getattr(llm_or_resources, "llm", None), "available", None)
    try:
        if callable(available) and not available():
            note.error = "no vision model"
            return note
        note.model = _resolve_model(llm_or_resources)
        text = describe(str(path), prompt, max_tokens=max_tokens)
    except Exception as exc:
        note.error = f"{type(exc).__name__}: {exc}"
        note.duration_ms = int((time.time() - t0) * 1000)
        return note
    note.text = (text or "").strip()
    note.ok = bool(note.text)
    if not note.ok:
        note.error = "empty response"
    note.tags_found = sorted({re.sub(r"\s+", "", m.group(0)) for m in TAG_RE.finditer(note.text)})
    note.numbers_found = NUMBER_RE.findall(note.text)
    note.illegible_spans = len(ILLEGIBLE_RE.findall(note.text))
    note.duration_ms = int((time.time() - t0) * 1000)
    return note

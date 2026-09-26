"""Shared pieces of the deliverable builders: the spec, markdown flattening, figure and
calculation-step extraction from a FinalResponse.

A *figure* here is a number the answer states, joined to the evidence label that supports it.
The provenance table in every deliverable is built from these, so a reviewer checks specific
claims against specific pages instead of re-reading the whole document.
"""
from __future__ import annotations

import re
import time
from typing import Any

from pydantic import BaseModel, Field

CITE_RE = re.compile(r"\[(\d+)\]")
NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w.])")
TAG_RE = re.compile(r"\b\d{1,3}\s*-?\s*[A-Z]{1,4}\s*-\s*\d{1,5}(?:\s*[A-Z](?:\s*/\s*[A-Z])*)?\b|\b[A-Z]{1,4}-\d{2,5}[A-Z]?\b")
PENDING_STATUS = "DRAFT — pending human sign-off"


class DeliverableSpec(BaseModel):
    title: str = "Engineering answer"
    subtitle: str = ""
    author: str = "Refinery Engineering AI Workbench"
    classification: str | None = None
    status_line: str = PENDING_STATUS
    watermark: bool = True
    generated_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M"))
    signed_off: bool = False


def spec_for(resp, title: str | None = None, author: str = "Refinery Engineering AI Workbench",
             signed_off: bool = False) -> DeliverableSpec:
    sec = getattr(resp, "security", None)
    classification = getattr(sec, "classification", None) if sec else None
    principal = getattr(sec, "principal", "") if sec else ""
    role = getattr(sec, "role", "") if sec else ""
    task = getattr(getattr(resp, "task_type", None), "value", str(getattr(resp, "task_type", "")))
    return DeliverableSpec(
        title=title or f"{task.replace('_', ' ').title()} — {_first_sentence(resp.answer_markdown)}"[:120],
        subtitle=f"Prepared for {principal or 'the requester'}" + (f" ({role})" if role else "") + f" · {task}",
        author=author, classification=classification, signed_off=signed_off,
        status_line=("Signed off" if signed_off else PENDING_STATUS),
        watermark=not signed_off,
    )


def _first_sentence(md: str) -> str:
    text = re.sub(r"[*_`#>]", "", md or "").strip()
    text = text.split("\n")[0]
    return (text[:70] + "…") if len(text) > 70 else text or "answer"


def strip_banner(md: str) -> str:
    """Drop the italic classification footer the orchestrator appends; the deliverable has its own."""
    return "\n".join(line for line in (md or "").splitlines() if not line.strip().startswith("*Classification:")).strip()


def markdown_to_paragraphs(md: str) -> list[tuple[str, str]]:
    """Flatten simple Markdown into (style, text) rows: heading1..3, bullet, number, plain."""
    out: list[tuple[str, str]] = []
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            out.append((f"heading{len(m.group(1))}", _clean(m.group(2))))
            continue
        m = re.match(r"^\s*[-*•]\s+(.*)", line)
        if m:
            out.append(("bullet", _clean(m.group(1))))
            continue
        m = re.match(r"^\s*\d+[.)]\s+(.*)", line)
        if m:
            out.append(("number", _clean(m.group(1))))
            continue
        if line.startswith("|"):
            out.append(("table_row", line))
            continue
        out.append(("plain", _clean(line)))
    return out


def _clean(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", text)
    text = text.replace("`", "")
    text = CITE_RE.sub(lambda m: f" [{m.group(1)}]", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# ---------------------------------------------------------------- evidence join
def evidence_by_ref(resp) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ev in getattr(resp, "evidence", []) or []:
        ref = getattr(ev, "ref", None)
        if ref:
            out[ref] = ev
            out[ref.strip("[]")] = ev
    return out


def _ev_fields(ev) -> dict:
    if ev is None:
        return {"document_id": None, "page": None, "chunk_id": None, "claim_id": None, "source_text": "", "source_kind": None, "grounding": None}
    return {"document_id": ev.document_id, "page": ev.page, "chunk_id": ev.chunk_id, "claim_id": ev.claim_id,
            "source_text": (ev.text or "")[:300], "source_kind": ev.source, "grounding": ev.grounding}


def _is_number(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    return bool(value) and bool(re.fullmatch(r"\s*-?\d+(?:[.,]\d+)?\s*", str(value)))


def figures_from_response(resp) -> list[dict]:
    """Every numeric value the answer states, with the evidence it cites.

    Rows: {value, unit, label, qualifier, citation, document_id, page, chunk_id, claim_id,
    source_text, source_kind, grounding, origin}. ``origin`` names the block the figure came from.
    """
    refs = evidence_by_ref(resp)
    rows: list[dict] = []
    seen: set[tuple] = set()
    text_blocks: list = []

    def add(value, unit, label, citation, origin, qualifier=None):
        if value is None or str(value).strip() in ("", "—"):
            return
        cite = citation.strip() if isinstance(citation, str) else None
        ev = refs.get(cite) if cite else None
        key = (str(value), unit, label, cite)
        if key in seen:
            return
        seen.add(key)
        rows.append({"value": str(value).strip(), "unit": unit, "label": label, "qualifier": qualifier,
                     "citation": cite, "origin": origin, **_ev_fields(ev)})

    for b in getattr(resp, "blocks", []) or []:
        t = b.type
        if t == "kpi":
            for it in b.items:
                add(it.value, it.unit, it.label, it.citation, "kpi", it.qualifier)
        elif t == "table":
            cites = b.row_citations or []
            for i, row in enumerate(b.rows):
                cite = cites[i][0] if i < len(cites) and cites[i] else None
                label = str(row[0]) if row else b.title or "table"
                for j, cell in enumerate(row[1:], start=1):
                    if _is_number(cell):
                        col = b.columns[j] if j < len(b.columns) else f"col{j}"
                        add(cell, None, f"{label} · {col}", cite, f"table:{b.title or b.id}")
        elif t == "comparison":
            for a, row in zip(b.attributes, b.cells):
                for subj, cell in zip(b.subjects, row):
                    if cell.value is not None and _is_number(cell.value):
                        add(cell.value, cell.unit, f"{subj} · {a}", cell.citation, "comparison")
        elif t == "limit_gauge":
            for m in b.markers:
                add(f"{m.value:g}", b.unit, f"{b.entity} · {b.parameter} ({m.label})", m.citation, "limit_gauge", m.label)
            if b.value is not None:
                add(f"{b.value:g}", b.unit, f"{b.entity} · {b.parameter} (checked value)", None, "limit_gauge", "requested")
        elif t == "conflict":
            for c in b.claims:
                add(c.value, c.unit, f"{b.subject} · {b.parameter}", c.citation, "conflict", c.context)
        elif t in ("text", "callout"):
            text_blocks.append(b)
    # Prose is read last: a number the structured blocks already carry with a citation is the
    # same figure, so it is not listed twice. A number only the prose states is joined to the
    # evidence whose text contains it; failing that it stays uncited, and the review layer
    # flags it as "no evidence" — which is the right outcome for an ungrounded number.
    cited_values = {r["value"].replace(",", ".") for r in rows if r.get("citation")}
    for b in text_blocks:
        cites = b.citations or []
        scrubbed = TAG_RE.sub(" ", b.markdown or "")
        scrubbed = re.sub(r"\b(p\.|page|step|chapter|section|rev)\s*\d+[A-Za-z0-9./-]*", " ", scrubbed, flags=re.IGNORECASE)
        for sentence in re.split(r"(?<=[.;])\s+", scrubbed):
            inline = CITE_RE.findall(sentence)
            cite = f"[{inline[0]}]" if inline else (cites[0] if cites else None)
            for num in NUMBER_RE.findall(CITE_RE.sub(" ", sentence)):
                if len(num) >= 5 and "." not in num and "," not in num:
                    continue                      # ids, years, page-ish numbers
                if num.replace(",", ".") in cited_values:
                    continue                      # already provenance-linked through a structured block
                found = cite
                if found is None:
                    pattern = re.compile(r"(?<![\d.])" + re.escape(num) + r"(?![\d.])")
                    found = next((ev.ref for ev in (getattr(resp, "evidence", []) or []) if ev.ref and pattern.search(ev.text or "")), None)
                add(num, None, (b.title or "answer") + ": " + sentence.strip()[:60], found, "text")
    return rows


def calculation_steps_from_response(resp) -> list[dict]:
    """Ordered calculation steps: {step, expression, result, note, refs}.

    The limit gauge is the one deterministic calculation the workbench performs; its steps are
    reconstructed here so every deliverable shows the arithmetic, not only the verdict.
    """
    steps: list[dict] = []
    for b in getattr(resp, "blocks", []) or []:
        if b.type == "limit_gauge":
            marks = {m.label.lower(): m for m in b.markers}
            n = 1
            for m in b.markers:
                steps.append({"step": n, "expression": f"{m.label} {b.parameter}", "result": f"{m.value:g} {b.unit}",
                              "note": f"documented for {b.entity}", "refs": [m.citation] if m.citation else [], "kind": "input"})
                n += 1
            if b.value is not None:
                steps.append({"step": n, "expression": f"requested {b.parameter}", "result": f"{b.value:g} {b.unit}",
                              "note": "value under check", "refs": [], "kind": "input"})
                n += 1
                normal = marks.get("normal") or marks.get("rated") or marks.get("operating")
                if normal and normal.value:
                    pct = (b.value - normal.value) / normal.value * 100
                    steps.append({"step": n, "expression": f"({b.value:g} − {normal.value:g}) / {normal.value:g} × 100",
                                  "result": f"{pct:+.1f} % vs normal", "note": "deviation from normal",
                                  "refs": [normal.citation] if normal.citation else [], "kind": "arith",
                                  "operands": [b.value, normal.value], "op": "pct_dev"})
                    n += 1
                for lab in ("maximum", "design", "minimum", "trip", "alarm"):
                    m = marks.get(lab)
                    if m and m.value:
                        cmp = "≤" if lab != "minimum" else "≥"
                        ok = (b.value <= m.value) if lab != "minimum" else (b.value >= m.value)
                        steps.append({"step": n, "expression": f"{b.value:g} {cmp} {m.value:g} ({lab})",
                                      "result": "true" if ok else "FALSE", "note": f"check against {lab}",
                                      "refs": [m.citation] if m.citation else [], "kind": "check",
                                      "operands": [b.value, m.value], "op": "le" if lab != "minimum" else "ge"})
                        n += 1
                steps.append({"step": n, "expression": "verdict", "result": b.verdict.replace("_", " "),
                              "note": b.message, "refs": [], "kind": "verdict"})
        elif b.type in ("text", "callout") and ((b.id or "").startswith("calc") or re.search(r"calculat", b.title or "", re.I)):
            for i, line in enumerate(l for l in (b.markdown or "").splitlines() if l.strip()):
                steps.append({"step": len(steps) + 1, "expression": _clean(line), "result": "", "note": "", "refs": b.citations or [], "kind": "text"})
    return steps


def audit_line(resp) -> str:
    conf = getattr(resp, "confidence", None)
    return (f"Audit {getattr(resp, 'audit_trail_id', '')} · response {getattr(resp, 'response_id', '')} · "
            f"confidence {getattr(conf, 'score', 0):.2f} ({getattr(conf, 'level', '')}) · "
            f"LLM calls {getattr(resp, 'llm_calls', 0)} · backend {getattr(resp, 'backend', '')} · "
            f"generated {time.strftime('%Y-%m-%d %H:%M')}")


def evidence_rows(resp) -> list[dict]:
    rows = []
    for ev in getattr(resp, "evidence", []) or []:
        rows.append({"ref": ev.ref or "", "document_id": ev.document_id, "page": ev.page, "source": ev.source,
                     "section": ev.section_path, "text": (ev.text or "").strip()})
    return rows

"""The draft registry: every deliverable, code artefact and flagged answer waits here for sign-off.

- **Provenance-linked**: each figure keeps document, page, chunk/claim id and the source line.
- **Confidence-flagged**: OCR under the threshold, LLM-derived text, or a figure with no
  evidence at all is flagged; it is never silently passed through.
- **Mandatory resolution**: ``sign_off`` raises ``SignoffBlocked`` while any flag is open —
  there is no blanket approve.
- **Visible pending state**: ``status`` is ``pending_signoff`` from creation until a reviewer
  decides. Every change is appended to the draft's history and to a hash-chained event log.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from workbench.sovereignty.hashchain import ChainVerification, HashChainedLog

STATUSES = ("pending_signoff", "signed_off", "rejected")
ACTIONS = ("accepted", "corrected", "removed")
REVIEWER_ROLES = ("manager", "admin")
NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w.])")


class SignoffBlocked(ValueError):
    """Raised when a sign-off is attempted with flags still open."""

    def __init__(self, open_flags: list["DraftFigure"], draft_id: str = "") -> None:
        self.open_flags = open_flags
        self.draft_id = draft_id
        names = ", ".join(f"{f.figure_id} ({f.label}: {f.value}{(' ' + f.unit) if f.unit else ''} — {f.flag_reason})" for f in open_flags[:6])
        more = f" and {len(open_flags) - 6} more" if len(open_flags) > 6 else ""
        super().__init__(f"{len(open_flags)} flagged figure(s) still open on draft {draft_id}: {names}{more}. "
                         f"Resolve each one (accept, correct or remove) before signing off.")


class FigureSource(BaseModel):
    document_id: str | None = None
    page: int | None = None
    chunk_id: str | None = None
    claim_id: str | None = None
    text: str = ""
    source_kind: str | None = None        # table | specification | procedure | narrative | ocr | vision | calculator | ...
    line: int | None = None


class Resolution(BaseModel):
    by: str
    ts: float = Field(default_factory=time.time)
    action: str                            # accepted | corrected | removed
    corrected_value: str | None = None
    note: str = ""


class DraftFigure(BaseModel):
    figure_id: str = Field(default_factory=lambda: f"fig-{uuid.uuid4().hex[:8]}")
    label: str
    value: str
    unit: str | None = None
    source: FigureSource = Field(default_factory=FigureSource)
    confidence: float = 1.0
    flagged: bool = False
    flag_reason: str | None = None
    resolution: Resolution | None = None
    citation: str | None = None
    origin: str = ""

    @property
    def open(self) -> bool:
        return self.flagged and self.resolution is None

    @property
    def effective_value(self) -> str | None:
        if self.resolution and self.resolution.action == "removed":
            return None
        if self.resolution and self.resolution.action == "corrected":
            return self.resolution.corrected_value
        return self.value


class Draft(BaseModel):
    draft_id: str = Field(default_factory=lambda: f"draft-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}")
    kind: str                              # docx | pptx | xlsx | md | code | answer
    title: str
    path: str | None = None
    response_id: str | None = None
    session_id: str = ""
    owner: str = ""
    owner_role: str = ""
    classification: str | None = None
    created: float = Field(default_factory=time.time)
    status: str = "pending_signoff"
    figures: list[DraftFigure] = Field(default_factory=list)
    signed_off_by: str | None = None
    signed_off_at: float | None = None
    signed_off_note: str = ""
    rejected_by: str | None = None
    rejected_at: float | None = None
    rejected_note: str = ""
    sha256: str | None = None
    history: list[dict] = Field(default_factory=list)

    @property
    def open_flags(self) -> list[DraftFigure]:
        return [f for f in self.figures if f.open]

    @property
    def flagged_count(self) -> int:
        return sum(1 for f in self.figures if f.flagged)

    def note(self, event: str, by: str, detail: str = "") -> None:
        self.history.append({"ts": time.time(), "event": event, "by": by, "detail": detail})

    def public(self) -> dict:
        out = self.model_dump(mode="json")
        out["open_flags"] = len(self.open_flags)
        out["flagged"] = self.flagged_count
        out["pending"] = self.status == "pending_signoff"
        return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _figure_from_row(row: dict, *, ocr_threshold: float, flag_llm_derived: bool) -> DraftFigure:
    src = FigureSource(document_id=row.get("document_id"), page=row.get("page"), chunk_id=row.get("chunk_id"),
                       claim_id=row.get("claim_id"), text=(row.get("source_text") or "")[:300], source_kind=row.get("source_kind"))
    grounding = row.get("grounding")
    confidence = float(row.get("confidence", grounding if grounding is not None else 1.0) or 0.0)
    fig = DraftFigure(label=str(row.get("label") or "figure"), value=str(row.get("value")), unit=row.get("unit"), source=src,
                      confidence=confidence, citation=row.get("citation"), origin=row.get("origin") or "")
    if row.get("origin") == "limit_gauge" and row.get("qualifier") == "requested":
        fig.source.source_kind = "requester"
        fig.confidence = 1.0
        return fig                                  # the value the engineer typed is not evidence-bearing
    if src.document_id is None and not row.get("citation"):
        fig.flagged, fig.flag_reason = True, "no evidence"
    elif src.source_kind in ("ocr", "vision") and confidence < ocr_threshold:
        fig.flagged, fig.flag_reason = True, f"low {src.source_kind.upper()} confidence {confidence:.2f}"
    elif flag_llm_derived and grounding is not None and grounding < 1.0:
        fig.flagged, fig.flag_reason = True, f"LLM-derived, not verbatim (grounding {grounding:.2f})"
    return fig


def apply_flags(fig: DraftFigure, *, ocr_threshold: float = 0.6) -> DraftFigure:
    """Flag a figure handed in by a caller by the same rules the response path uses."""
    if fig.flagged or fig.resolution is not None or fig.source.source_kind == "requester":
        return fig
    kind = fig.source.source_kind
    if fig.source.document_id is None and not fig.citation and not fig.source.claim_id and not fig.source.chunk_id:
        fig.flagged, fig.flag_reason = True, "no evidence"
    elif kind in ("ocr", "vision") and fig.confidence < ocr_threshold:
        fig.flagged, fig.flag_reason = True, f"low {kind.upper()} confidence {fig.confidence:.2f}"
    return fig


def figures_from_intake(result, *, threshold: float | None = None) -> list[DraftFigure]:
    """Every OCR line that carries a number becomes a figure at the line's confidence."""
    figs: list[DraftFigure] = []
    thr = threshold if threshold is not None else getattr(result, "threshold", 0.6)
    ocr = getattr(result, "ocr", None)
    lines = list(ocr.lines) if ocr is not None else []
    for fo in getattr(result, "figures_ocr", []) or []:
        lines.extend(fo.lines)
    source_name = Path(getattr(result, "source", "")).name
    for i, ln in enumerate(lines):
        nums = NUMBER_RE.findall(ln.text)
        if not nums:
            continue
        kind = "text_layer" if getattr(ln, "note", "") == "text layer" else "ocr"
        fig = DraftFigure(label=ln.text[:80], value=nums[0], unit=None,
                          source=FigureSource(document_id=source_name, page=ln.page, text=ln.text[:300], source_kind=kind, line=i),
                          confidence=float(ln.confidence), origin="intake")
        if kind == "ocr" and ln.confidence < thr:
            fig.flagged, fig.flag_reason = True, f"low OCR confidence {ln.confidence:.2f}"
        figs.append(fig)
    for note in getattr(result, "vision", []) or []:
        if getattr(note, "ok", False):
            for num in note.numbers_found[:20]:
                figs.append(DraftFigure(label=f"vision: {Path(note.path).name}", value=num,
                                        source=FigureSource(document_id=source_name, text=note.text[:300], source_kind="vision"),
                                        confidence=0.5, flagged=True, flag_reason="read by the vision model; verify against the image", origin="intake"))
    return figs


class DraftRegistry:
    def __init__(self, root: Path) -> None:
        self.dir = Path(root)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = HashChainedLog(self.dir / "events.jsonl", name="drafts")
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ persistence
    def _path(self, draft_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", draft_id)[:80]
        return self.dir / f"{safe}.json"

    def _save(self, draft: Draft) -> None:
        tmp = self._path(draft.draft_id).with_suffix(".tmp")
        tmp.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self._path(draft.draft_id))

    def _event(self, draft: Draft, event: str, by: str, **fields: Any) -> None:
        self.events.append({"event": event, "draft_id": draft.draft_id, "by": by, "status": draft.status,
                            "open_flags": len(draft.open_flags), **fields})

    def get(self, draft_id: str) -> Draft:
        p = self._path(draft_id)
        if not p.exists():
            raise KeyError(f"no draft {draft_id!r}")
        return Draft.model_validate_json(p.read_text(encoding="utf-8"))

    def list(self, *, owner: str | None = None, status: str | None = None, session_id: str | None = None) -> list[Draft]:
        out = []
        for p in sorted(self.dir.glob("draft-*.json")):
            try:
                d = Draft.model_validate_json(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if owner and d.owner != owner:
                continue
            if status and d.status != status:
                continue
            if session_id and d.session_id != session_id:
                continue
            out.append(d)
        return sorted(out, key=lambda d: -d.created)

    def pending(self) -> list[Draft]:
        return self.list(status="pending_signoff")

    def summary(self) -> dict:
        drafts = self.list()
        return {"total": len(drafts),
                "pending_signoff": sum(1 for d in drafts if d.status == "pending_signoff"),
                "signed_off": sum(1 for d in drafts if d.status == "signed_off"),
                "rejected": sum(1 for d in drafts if d.status == "rejected"),
                "open_flags": sum(len(d.open_flags) for d in drafts),
                "figures": sum(len(d.figures) for d in drafts)}

    # ------------------------------------------------------------------ lifecycle
    def create(self, kind: str, title: str, *, response=None, path: str | Path | None = None,
               figures: list[DraftFigure] | None = None, session_id: str = "", owner: str = "", owner_role: str = "",
               classification: str | None = None, ocr_threshold: float = 0.6, flag_llm_derived: bool = True,
               response_id: str | None = None) -> Draft:
        if figures is None and response is not None:
            from workbench.deliverables.common import figures_from_response

            figures = [_figure_from_row(r, ocr_threshold=ocr_threshold, flag_llm_derived=flag_llm_derived)
                       for r in figures_from_response(response)]
        else:
            figures = [apply_flags(f, ocr_threshold=ocr_threshold) for f in (figures or [])]
        draft = Draft(kind=kind, title=title, session_id=session_id, owner=owner, owner_role=owner_role,
                      classification=classification or (getattr(getattr(response, "security", None), "classification", None) if response is not None else None),
                      response_id=response_id or (getattr(response, "response_id", None) if response is not None else None),
                      figures=list(figures or []), status="pending_signoff")
        if path is not None:
            draft.path = str(path)
            if Path(path).exists():
                draft.sha256 = _sha256(Path(path))
        if response is not None and getattr(response, "requires_human_review", False) and not draft.figures:
            draft.figures.append(DraftFigure(label="answer", value="(whole answer)", confidence=float(response.confidence.score),
                                             flagged=True, flag_reason=f"answer flagged for review: {getattr(response, 'review_reason', '') or 'policy'}",
                                             source=FigureSource(source_kind="answer"), origin="governance"))
        draft.note("created", owner, f"{kind}: {len(draft.figures)} figure(s), {len(draft.open_flags)} flagged")
        with self._lock:
            self._save(draft)
            self._event(draft, "draft_created", owner, kind=kind, figures=len(draft.figures), flagged=len(draft.open_flags), sha256=draft.sha256)
        return draft

    def attach_file(self, draft_id: str, path: str | Path) -> Draft:
        with self._lock:
            draft = self.get(draft_id)
            draft.path = str(path)
            draft.sha256 = _sha256(Path(path)) if Path(path).exists() else None
            draft.note("file_attached", draft.owner, f"{Path(path).name} sha256={draft.sha256}")
            self._save(draft)
            self._event(draft, "file_attached", draft.owner, sha256=draft.sha256)
        return draft

    def resolve_figure(self, draft_id: str, figure_id: str, *, by: str, action: str, corrected_value: str | None = None,
                       note: str = "") -> Draft:
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
        if action == "corrected" and not (corrected_value and str(corrected_value).strip()):
            raise ValueError("a corrected figure needs the corrected value")
        with self._lock:
            draft = self.get(draft_id)
            if draft.status != "pending_signoff":
                raise ValueError(f"draft {draft_id} is {draft.status}; only a pending draft can be edited")
            fig = next((f for f in draft.figures if f.figure_id == figure_id), None)
            if fig is None:
                raise KeyError(f"no figure {figure_id!r} on draft {draft_id}")
            fig.resolution = Resolution(by=by, action=action, corrected_value=(str(corrected_value) if corrected_value is not None else None), note=note)
            draft.note("figure_resolved", by, f"{figure_id} {action}" + (f" -> {corrected_value}" if corrected_value else "") + (f": {note}" if note else ""))
            self._save(draft)
            self._event(draft, "figure_resolved", by, figure_id=figure_id, action=action, corrected_value=corrected_value)
        return draft

    def sign_off(self, draft_id: str, *, by: str, role: str, note: str = "") -> Draft:
        if str(role).lower() not in REVIEWER_ROLES:
            raise PermissionError(f"sign-off needs a manager or an administrator; {by or 'the caller'} is {role or 'unknown'}")
        with self._lock:
            draft = self.get(draft_id)
            if draft.status == "signed_off":
                return draft
            if draft.status == "rejected":
                raise ValueError(f"draft {draft_id} was rejected; it cannot be signed off")
            open_flags = draft.open_flags
            if open_flags:
                draft.note("signoff_blocked", by, f"{len(open_flags)} flag(s) open")
                self._save(draft)
                self._event(draft, "signoff_blocked", by, open=[f.figure_id for f in open_flags])
                raise SignoffBlocked(open_flags, draft_id)
            draft.status = "signed_off"
            draft.signed_off_by, draft.signed_off_at, draft.signed_off_note = by, time.time(), note
            draft.note("signed_off", by, note)
            self._save(draft)
            self._event(draft, "draft_signed_off", by, role=role, note=note, sha256=draft.sha256)
        return draft

    def reject(self, draft_id: str, *, by: str, role: str = "manager", note: str = "") -> Draft:
        if str(role).lower() not in REVIEWER_ROLES:
            raise PermissionError(f"rejecting a draft needs a manager or an administrator; {by or 'the caller'} is {role or 'unknown'}")
        with self._lock:
            draft = self.get(draft_id)
            draft.status = "rejected"
            draft.rejected_by, draft.rejected_at, draft.rejected_note = by, time.time(), note
            draft.note("rejected", by, note)
            self._save(draft)
            self._event(draft, "draft_rejected", by, role=role, note=note)
        return draft

    def verify_chain(self) -> ChainVerification:
        return self.events.verify()

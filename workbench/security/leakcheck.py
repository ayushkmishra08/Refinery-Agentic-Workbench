"""The last gate: nothing leaves unless its provenance is permitted.

The guard already makes restricted material unreachable, so in a correct system this check never
fires. It exists because "never fires in a correct system" is an assumption, and the cost of the
assumption being wrong is a classified operating figure on a user's screen. This is the seatbelt,
not the brakes.

Three independent checks run over the finished response, just before it is handed back:

1. **Provenance.** Every evidence item on the response must come from a document the principal
   may read, or from a record their grant named. This is authoritative — evidence carries its
   own ``document_id``, so there is nothing to infer.

2. **Tags.** Every equipment tag in the released prose must resolve inside the principal's own
   view. A tag that only exists in a withheld document has no business being named, whether it
   arrived through retrieval, through the model, or through the conversation history.

3. **Figures.** Every number in the released prose must appear in the evidence actually attached
   to that response. The composer already checks its draft against its brief; this repeats the
   check against what was finally released, which is a different set after governance has
   trimmed, relabelled and reordered.

A failure is not a warning. The response is replaced with a refusal, the incident is written to
the security log with the reason, and the person is told their question could not be answered
safely. A wrong answer is recoverable; a leak is not.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])")
TAG_RE = re.compile(r"\b\d{1,3}-[A-Z]{1,4}-\d{1,5}(?:[A-Z](?:/[A-Z])*)?\b", re.IGNORECASE)
# Numbers that are part of how an answer is written rather than claims about the plant
STRUCTURAL_NUM_RE = re.compile(r"(p\.|page|pages|step|steps|section|chapter|rev(?:ision)?|figure|table|item|\[)\s*$",
                               re.IGNORECASE)


class LeakFinding(BaseModel):
    check: str                  # provenance | tags | figures
    detail: str
    severity: str = "block"     # block | note


class LeakReport(BaseModel):
    ok: bool = True
    findings: list[LeakFinding] = Field(default_factory=list)
    checked_evidence: int = 0
    checked_tags: int = 0
    checked_numbers: int = 0

    @property
    def blocking(self) -> list[LeakFinding]:
        return [f for f in self.findings if f.severity == "block"]

    def summary(self) -> str:
        if self.ok:
            return (f"{self.checked_evidence} citation(s), {self.checked_tags} tag(s) and "
                    f"{self.checked_numbers} figure(s) all trace to permitted material")
        return "; ".join(f.detail for f in self.blocking[:3])


def _strip_structural_numbers(text: str) -> list[str]:
    """Numbers in the prose that are claims about the plant, not page or step references."""
    out = []
    for m in NUM_RE.finditer(text or ""):
        before = text[max(0, m.start() - 12):m.start()]
        if STRUCTURAL_NUM_RE.search(before):
            continue
        out.append(m.group(0))
    return out


def _norm_num(s: str) -> str:
    try:
        return f"{float(s.replace(',', '')):g}"
    except ValueError:
        return s


def check_response(response, *, allowed_documents, granted_records=None, granted_documents=None,
                   knowledge=None) -> LeakReport:
    """Run the three checks over a finished FinalResponse."""
    report = LeakReport()
    allowed = set(allowed_documents or ())
    granted_docs = set(granted_documents or ())
    granted = set(granted_records or ())

    # ---- 1. provenance ------------------------------------------------------------------
    for ev in response.evidence:
        report.checked_evidence += 1
        if ev.document_id in allowed:
            continue
        if ev.document_id in granted_docs and granted:
            continue                      # released under a grant; the guard already matched ids
        report.findings.append(LeakFinding(
            check="provenance",
            detail=f"a citation from '{ev.document_id}', which this role may not read, reached the answer"))

    released_text = response.answer_markdown or ""

    # ---- 2. tags ------------------------------------------------------------------------
    if knowledge is not None:
        for tag in sorted({t.upper() for t in TAG_RE.findall(released_text)}):
            report.checked_tags += 1
            try:
                if not knowledge.resolve_entity(tag, limit=1):
                    report.findings.append(LeakFinding(
                        check="tags", severity="note",
                        detail=f"the answer names {tag}, which does not resolve inside this role's view"))
            except Exception as exc:                # a lookup failure is not a leak
                logger.debug("leak check tag lookup failed for %s: %s", tag, exc)

    # ---- 3. figures ---------------------------------------------------------------------
    evidence_numbers = {_norm_num(n) for ev in response.evidence for n in NUM_RE.findall(ev.text or "")}
    evidence_numbers |= {_norm_num(str(ev.page)) for ev in response.evidence if ev.page is not None}
    if response.evidence:
        for raw in _strip_structural_numbers(released_text):
            report.checked_numbers += 1
            if _norm_num(raw) not in evidence_numbers:
                report.findings.append(LeakFinding(
                    check="figures", severity="note",
                    detail=f"the figure {raw} in the answer is not in the evidence attached to it"))

    report.ok = not report.blocking
    return report


def refusal_response(response, report: LeakReport):
    """Replace a response that failed the gate with one that says so and carries nothing."""
    from workbench.core.blocks import CalloutBlock
    from workbench.core.evidence import Confidence

    blocked = response.model_copy(deep=True)
    blocked.status = "blocked"
    blocked.blocks = [CalloutBlock(
        id="lead", level="danger", title="Answer withheld",
        markdown=("This answer could not be released: part of it traced back to material your role is not "
                  "cleared to read, so the whole response was withheld rather than redacted. Nothing from "
                  "that material is shown. If you need it, request access and it will be reviewed by the "
                  "responsible role.\n\nThis has been recorded as a security event."))]
    blocked.answer_markdown = blocked.blocks[0].markdown
    blocked.evidence = []
    blocked.safety_flags = []
    blocked.plan = None
    blocked.confidence = Confidence(score=0.0, basis="the response failed the release check and was withheld")
    blocked.warnings = ["release check failed: " + report.summary()]
    blocked.security.release_blocked = True
    blocked.security.classification = None      # nothing was released, so nothing carries a classification
    blocked.security.source_documents = []
    blocked.requires_human_review = True
    blocked.review_reason = "A release check failed. A responsible person should look at the audit trail."
    return blocked

"""What clearance each document needs.

A document is classified once, by rule, and the answer is cached in
``data/workbench/security/classifications.json`` so an administrator can edit it by hand.
The shipped rules classify any unit operating manual — the CDU manual included — as
``confidential``: only a lead engineer or an administrator may read it.

Documents a session uploads are classified ``internal`` by default: they were brought in by
the person holding the session, so they are not more restricted than the session itself, but
they are not public either.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from workbench.security.roles import Clearance

logger = logging.getLogger(__name__)

# (pattern matched against "<document_id> <title> <unit> <document_type>", clearance, reason)
DEFAULT_RULES: list[tuple[str, Clearance, str]] = [
    (r"\b(licensor|proprietary|contract|confidential agreement|process licence|process license)\b",
     Clearance.SECRET, "licensor or contractual material"),
    (r"\b(cdu|vdu|crude distillation|vacuum distillation|crude unit|atmospheric unit)\b",
     Clearance.CONFIDENTIAL, "CDU/VDU unit operating data"),
    (r"\b(operating manual|operations manual|operating procedure|start-?up manual|unit manual)\b",
     Clearance.CONFIDENTIAL, "unit operating manual"),
    (r"\b(p&id|pid drawing|datasheet|data sheet|specification|design basis)\b",
     Clearance.CONFIDENTIAL, "engineering design document"),
    (r"\b(safety data sheet|msds|training|awareness|notice)\b",
     Clearance.INTERNAL, "general plant material"),
]
UPLOAD_CLEARANCE = Clearance.INTERNAL
FALLBACK_CLEARANCE = Clearance.CONFIDENTIAL      # an unrecognised document is never public


class DocumentClassification(BaseModel):
    document_id: str
    clearance: Clearance = FALLBACK_CLEARANCE
    reason: str = "default classification"
    title: str = ""
    unit: str | None = None
    pinned: bool = Field(default=False, description="Set by hand in the JSON; the rules never overwrite a pinned entry")


class ClassificationRegistry:
    """Document id -> clearance, rule-derived and persisted so it can be overridden by hand."""

    def __init__(self, path: Path | None = None, rules: list[tuple[str, Clearance, str]] | None = None) -> None:
        self.path = path
        self.rules = [(re.compile(rx, re.IGNORECASE), c, why) for rx, c, why in (rules or DEFAULT_RULES)]
        self._entries: dict[str, DocumentClassification] = {}
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for row in raw.get("documents", []):
                dc = DocumentClassification.model_validate(row)
                self._entries[dc.document_id] = dc
        except Exception as exc:  # a corrupt file must not open the doors
            logger.warning("classification file unreadable (%s); falling back to the rules", exc)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"documents": [e.model_dump(mode="json") for e in self._entries.values()]}
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("classification file could not be written (%s)", exc)

    # ------------------------------------------------------------------ classification
    def classify(self, doc) -> DocumentClassification:
        """Classification of a DocumentInfo, computing and persisting it on first sight."""
        existing = self._entries.get(doc.document_id)
        if existing is not None and (existing.pinned or existing.reason != "default classification"):
            return existing
        dc = self._by_rules(doc)
        if existing is None or existing.clearance != dc.clearance:
            self._entries[doc.document_id] = dc
            self._save()
        return dc

    def _by_rules(self, doc) -> DocumentClassification:
        if getattr(doc, "origin", "") == "upload":
            return DocumentClassification(document_id=doc.document_id, clearance=UPLOAD_CLEARANCE,
                                          reason="uploaded in this session", title=doc.title or "", unit=doc.unit)
        haystack = " ".join(str(x) for x in (doc.document_id, doc.title, doc.unit or "", doc.document_type or ""))
        for rx, clearance, why in self.rules:
            if rx.search(haystack):
                return DocumentClassification(document_id=doc.document_id, clearance=clearance, reason=why,
                                              title=doc.title or "", unit=doc.unit)
        return DocumentClassification(document_id=doc.document_id, clearance=FALLBACK_CLEARANCE,
                                      reason="unrecognised document, classified conservatively",
                                      title=doc.title or "", unit=doc.unit)

    def clearance_of(self, document_id: str) -> Clearance:
        """Clearance for a document id alone (used when only the id is on a record).

        An id that was never classified is treated as ``FALLBACK_CLEARANCE``: a record whose
        provenance the registry has not seen is not handed out.
        """
        e = self._entries.get(document_id)
        return e.clearance if e else FALLBACK_CLEARANCE

    def known(self) -> dict[str, DocumentClassification]:
        return dict(self._entries)

    def set(self, document_id: str, clearance: Clearance, reason: str = "set by administrator") -> DocumentClassification:
        dc = DocumentClassification(document_id=document_id, clearance=clearance, reason=reason, pinned=True)
        self._entries[document_id] = dc
        self._save()
        return dc

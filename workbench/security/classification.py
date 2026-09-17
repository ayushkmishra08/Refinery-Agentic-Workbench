"""Which tag each document carries.

A document is tagged once and the tag is written to
``data/workbench/security/classifications.json``, where it can be read, audited and edited by
hand. Three ways a tag is decided, in order:

1. **An explicit assignment** — what ``scripts/setup_security.py`` writes, and what an
   administrator sets with ``workbench classify``. Pinned; the rules never overwrite it.
2. **A pattern rule** — ``DEFAULT_RULES`` below, matched against the document's id, title, unit
   and type. This is what tags a document that arrives after the deployment was set up.
3. **The fallback** — ``SECRET``. A document nobody has classified is not a public one, and a
   new PDF dropped into ``data/raw`` is readable by nobody until someone says otherwise.

That third rule is the one worth arguing about, so: the alternative is that an unrecognised
document defaults to readable, and then the failure mode of *forgetting to classify something*
is a leak instead of an inconvenience. Deny by default.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from pydantic import BaseModel, Field

from workbench.security.roles import FALLBACK_TAG, Role, Tag, normalise_roles, roles_that_can_read

logger = logging.getLogger(__name__)

# (pattern against "<id> <title> <unit> <type>", tag, why). First match wins, so the most
# specific patterns come first.
DEFAULT_RULES: list[tuple[str, Tag, str]] = [
    (r"\b(operating manual|operations manual|unit manual|operating procedure)\b",
     Tag.SECRET, "unit operating manual — how this plant is actually run"),
    (r"\b(cdu|vdu|crude distillation|vacuum distillation)\b.*\b(manual|procedure)\b",
     Tag.SECRET, "crude/vacuum unit operating documentation"),
    (r"\b(desalter|desalting)\b",
     Tag.CONFIDENTIAL, "unit equipment documentation"),
    (r"\b(p&id|datasheet|data sheet|design basis|equipment manual)\b",
     Tag.CONFIDENTIAL, "unit equipment documentation"),
    (r"\b(api\s?\d{3}|iso\s?\d+|asme|ansi|standard|specification|bulletin|vendor|catalog(ue)?)\b",
     Tag.INTERNAL, "published standard or vendor reference"),
    (r"\b(training|awareness|notice|safety data sheet|msds)\b",
     Tag.INTERNAL, "general plant reference material"),
]
UPLOAD_TAG = Tag.CONFIDENTIAL   # a session upload is unreviewed; it is not general reference


class DocumentClassification(BaseModel):
    document_id: str
    tag: Tag = FALLBACK_TAG
    reason: str = "unclassified: defaulted to the most restricted tag"
    title: str = ""
    unit: str | None = None
    assigned_by: str = "rule"          # rule | setup | administrator | fallback
    assigned_at: float = Field(default_factory=time.time)
    pinned: bool = Field(default=False, description="Set by hand or by the setup script; the rules never overwrite it")
    roles: list[str] | None = Field(
        default=None,
        description="Explicit reader allowlist. None means the tag ladder decides; a list means "
                    "exactly these roles read this document and no others, whatever their level.",
    )

    @property
    def readable_by(self) -> list[Role]:
        """Every role that may read this document, lowest first.

        Two ways a document says who reads it. By default the **tag ladder**: the tag names a
        level, and every role at or above it is in. Set ``roles`` and the document becomes a
        **compartment**: exactly those roles, and being senior no longer implies being included.
        That is what makes "the desalter tree belongs to the manager" expressible — under the
        ladder alone, anything a manager reads an administrator reads too.
        """
        if self.roles is not None:
            return normalise_roles(self.roles)
        return roles_that_can_read(self.tag)

    @property
    def compartmented(self) -> bool:
        """True when this document carries its own allowlist rather than following the ladder."""
        return self.roles is not None

    @property
    def min_role(self) -> str:
        readers = self.readable_by
        return readers[0].value if readers else "admin"

    def describe(self) -> str:
        readers = self.readable_by
        who = (", ".join(r.value for r in readers) or "nobody") if self.compartmented else f"{self.min_role} and above"
        return f"{self.title or self.document_id} — {self.tag.value} ({self.reason}); readable by {who}"


class ClassificationRegistry:
    """Document id -> tag, persisted, auditable, editable."""

    def __init__(self, path: Path | None = None, rules: list[tuple[str, Tag, str]] | None = None) -> None:
        self.path = path
        self.rules = [(re.compile(rx, re.IGNORECASE), tag, why) for rx, tag, why in (rules or DEFAULT_RULES)]
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
        except Exception as exc:
            # A corrupt classification file must not open the doors: forget it and re-derive
            # from the rules, which fail closed.
            logger.error("classification file unreadable (%s); falling back to the rules", exc)
            self._entries = {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated": time.time(),
                       "documents": [e.model_dump(mode="json") for e in sorted(self._entries.values(), key=lambda d: d.document_id)]}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            logger.warning("classification file could not be written (%s)", exc)

    # ------------------------------------------------------------------ classification
    def classify(self, doc) -> DocumentClassification:
        """The tag for a DocumentInfo, deriving and persisting it the first time it is seen."""
        existing = self._entries.get(doc.document_id)
        if existing is not None and (existing.pinned or existing.assigned_by in ("setup", "administrator")):
            return existing
        dc = self._by_rules(doc)
        if existing is not None and existing.roles is not None:
            # A re-derived tag must never silently drop an allowlist somebody set by hand: the
            # allowlist is the stricter statement, and losing it would widen access.
            dc = dc.model_copy(update={"roles": list(existing.roles)})
        if existing is None or existing.tag != dc.tag:
            self._entries[doc.document_id] = dc
            self._save()
        return self._entries[doc.document_id]

    def _by_rules(self, doc) -> DocumentClassification:
        if getattr(doc, "origin", "") == "upload":
            return DocumentClassification(document_id=doc.document_id, tag=UPLOAD_TAG, assigned_by="rule",
                                          reason="uploaded during a session and not yet reviewed",
                                          title=doc.title or "", unit=doc.unit)
        # The document profiler sometimes lands a paragraph in ``unit`` when a PDF has no clean
        # header. Trim it: a rule matching a stray sentence would classify by accident.
        unit = (doc.unit or "")[:60]
        haystack = " ".join(str(x) for x in (doc.document_id, doc.title, unit, doc.document_type or ""))
        for rx, tag, why in self.rules:
            if rx.search(haystack):
                return DocumentClassification(document_id=doc.document_id, tag=tag, reason=why, assigned_by="rule",
                                              title=doc.title or "", unit=doc.unit)
        return DocumentClassification(document_id=doc.document_id, tag=FALLBACK_TAG, assigned_by="fallback",
                                      reason="unclassified: defaulted to the most restricted tag",
                                      title=doc.title or "", unit=doc.unit)

    def tag_of(self, document_id: str) -> Tag:
        """The tag for a document id alone, for a record that carries only its id.

        An id the registry has never classified is ``SECRET``: a record whose provenance is not
        established is not handed out.
        """
        entry = self._entries.get(document_id)
        return entry.tag if entry else FALLBACK_TAG

    def entry(self, document_id: str) -> DocumentClassification | None:
        return self._entries.get(document_id)

    def readers_of(self, document_id: str) -> list[Role]:
        """Every role that may read this document id, lowest first.

        The one function the rest of the system should ask. An id the registry has never seen is
        treated as ``FALLBACK_TAG`` — the same deny-by-default rule as ``tag_of``.
        """
        entry = self._entries.get(document_id)
        return entry.readable_by if entry else roles_that_can_read(FALLBACK_TAG)

    def set_roles(self, document_id: str, roles: list[str] | None, *, by: str = "administrator") -> DocumentClassification:
        """Pin an explicit reader allowlist, or pass ``None`` to hand the document back to the ladder.

        The tag is left alone: it still says how sensitive the document is, and the audit trail,
        the banners and the refusal wording all read it. The allowlist only changes *who*.
        """
        existing = self._entries.get(document_id)
        if existing is None:
            existing = DocumentClassification(document_id=document_id, assigned_by="fallback")
        cleaned = None if roles is None else [r.value for r in normalise_roles(roles)]
        dc = existing.model_copy(update={"roles": cleaned, "assigned_by": by, "assigned_at": time.time(), "pinned": True})
        self._entries[document_id] = dc
        self._save()
        logger.info("document %s readable by %s", document_id, cleaned if cleaned is not None else "the tag ladder")
        return dc

    def known(self) -> dict[str, DocumentClassification]:
        return dict(self._entries)

    def assign(self, document_id: str, tag: Tag | str, reason: str = "", *, by: str = "administrator",
               title: str = "", unit: str | None = None,
               roles: list[str] | None = None, keep_roles: bool = True) -> DocumentClassification:
        """Pin a tag by hand. Used by the setup script and ``workbench classify``."""
        tag = tag if isinstance(tag, Tag) else Tag(str(tag).strip().upper())
        existing = self._entries.get(document_id)
        if roles is not None:
            allowlist: list[str] | None = [r.value for r in normalise_roles(roles)]
        elif keep_roles and existing is not None:
            allowlist = list(existing.roles) if existing.roles is not None else None
        else:
            allowlist = None
        dc = DocumentClassification(
            document_id=document_id, tag=tag, assigned_by=by, pinned=True,
            reason=reason or f"assigned by {by}",
            title=title or (existing.title if existing else ""),
            unit=unit if unit is not None else (existing.unit if existing else None),
            roles=allowlist,
        )
        self._entries[document_id] = dc
        self._save()
        return dc

"""GuardedKnowledgeService — the enforcement point.

Every agent receives a guarded service rather than the backend. The guard forwards each call and
then drops every record the principal may not read, so there is no route to restricted content:
not through a search, not through a tag lookup, not through a neighbour walk, not through an
entity the record happens to hang off.

A record survives one of two ways:

* its **document** is readable by the principal's role, or
* its **record id** is named in an approved access grant.

The second is how an escalation lands: the grant does not promote the role, it adds a short
list of ids. Everything else in that document stays shut, which is what "only the accessed data
and nothing else" has to mean if it is to mean anything.

Entities are the composite case — one entity can be mentioned in several documents — so an
entity survives if any of its documents is readable, and its ``document_ids`` are narrowed to
the readable ones. A user who meets a pump that appears in both an open standard and the CDU
manual sees the pump and the standard's half of it, never the manual's.

The guard also counts what it withheld, so the answer can say it was trimmed rather than quietly
returning less, and so ``scope_probe`` can build a grant scope without leaking anything.
"""
from __future__ import annotations

import logging
from typing import Any

from workbench.core.knowledge import ConflictRecord, EntityRecord
from workbench.security.records import record_id

logger = logging.getLogger(__name__)

# Methods whose return value is a list of records carrying a document_id
_LIST_METHODS = (
    "entity_claims", "search_claims", "entity_neighbors", "procedures", "search_chunks",
    "chunks_for_entity", "sections", "glossary", "document_references", "standing_instructions",
    "cross_references",
)
_ENTITY_LIST_METHODS = ("resolve_entity", "search_entities", "list_entities")
_SINGLE_METHODS = ("get_entity", "get_procedure", "get_chunk")

#: What the backends default `limit` to, used to spot a truncated page when none was asked for.
_DEFAULT_ENTITY_LIMIT = 500
#: How wide to re-ask when filtering left too little. The indexes are in memory; this is cheap.
_WIDE_ENTITY_LIMIT = 100_000


class GuardedKnowledgeService:
    """Read-only KnowledgeService view restricted to what a principal may read."""

    name = "guarded"

    def __init__(self, inner, allowed_document_ids: list[str] | set[str] | None, *, enabled: bool = True,
                 granted_record_ids: list[str] | set[str] | None = None,
                 granted_document_ids: list[str] | set[str] | None = None) -> None:
        self.inner = inner
        self.enabled = enabled
        self.allowed: set[str] | None = None if allowed_document_ids is None else set(allowed_document_ids)
        self.granted: set[str] = set(granted_record_ids or ())
        # Where the granted records live. An entity released by a grant is narrowed to these, so
        # naming the equipment cannot become a way to reach the rest of the document it is
        # described in — see `_filter_entities`.
        self.granted_documents: set[str] = set(granted_document_ids or ())
        self.withheld: dict[str, int] = {}
        self.released_by_grant: set[str] = set()

    # ------------------------------------------------------------------ the filter
    def _ok(self, record) -> bool:
        """True when this record may be returned: its document is open, or a grant names it."""
        if not self.enabled or self.allowed is None:
            return True
        document_id = getattr(record, "document_id", None) if not isinstance(record, str) else record
        if document_id and document_id in self.allowed:
            return True
        if self.granted and not isinstance(record, str):
            rid = record_id(record)
            if rid and rid in self.granted:
                self.released_by_grant.add(rid)
                return True
        return False

    def _ok_document(self, document_id: str | None) -> bool:
        if not self.enabled or self.allowed is None:
            return True
        return bool(document_id) and document_id in self.allowed

    def _note(self, document_id: str | None) -> None:
        key = document_id or "unattributed"
        self.withheld[key] = self.withheld.get(key, 0) + 1

    def _filter(self, rows: list) -> list:
        if not self.enabled or self.allowed is None:
            return rows
        out = []
        for r in rows:
            if isinstance(r, ConflictRecord):
                kept = [c for c in r.claims if self._ok(c)]
                for c in r.claims:
                    if not self._ok(c):
                        self._note(c.document_id)
                if kept:
                    out.append(r.model_copy(update={"claims": kept}))
                continue
            if self._ok(r):
                out.append(r)
            else:
                self._note(getattr(r, "document_id", None))
        return out

    def _filter_entities(self, rows: list[EntityRecord]) -> list[EntityRecord]:
        if not self.enabled or self.allowed is None:
            return rows
        out: list[EntityRecord] = []
        for e in rows:
            docs = [d for d in (e.document_ids or []) if self._ok_document(d)]
            if docs:
                out.append(e if docs == list(e.document_ids) else e.model_copy(update={"document_ids": docs}))
            elif self.granted and (record_id(e) or "") in self.granted:
                # A grant that opens a procedure has to open the equipment it hangs off, or the
                # resolver never finds the equipment and the approved material is unreachable.
                # But the equipment record may be *described* somewhere the caller still cannot
                # read, so it is handed over narrowed to the documents the grant actually covers:
                # the name resolves, and every claim, chunk and neighbour behind it is still
                # filtered by the same rules as everything else.
                self.released_by_grant.add(record_id(e))
                narrowed = sorted(set(e.document_ids or []) & self.granted_documents) or sorted(self.granted_documents)
                out.append(e if list(e.document_ids or []) == narrowed else e.model_copy(update={"document_ids": narrowed}))
            else:
                # An entity with no document at all is withheld, not passed through. Unattributed
                # data is exactly the case where a fail-open default turns into a leak, so the
                # rule is the same as everywhere else here: no provenance, no release.
                self._note(e.document_ids[0] if e.document_ids else None)
        return out

    def withheld_count(self) -> int:
        return sum(self.withheld.values())

    def withheld_documents(self) -> list[str]:
        return sorted(d for d in self.withheld if d != "unattributed")

    # ------------------------------------------------------------------ documents
    def documents(self) -> list:
        return [d for d in self.inner.documents() if self._ok_document(d.document_id)]

    def document_profile(self, document_id: str) -> dict:
        return self.inner.document_profile(document_id) if self._ok_document(document_id) else {}

    def chapters(self, document_id: str | None = None) -> list[dict]:
        fn = getattr(self.inner, "chapters", None)
        if fn is None:
            return []
        rows = fn(document_id)
        if not self.enabled or self.allowed is None:
            return rows
        return [c for c in rows if self._ok_document(c.get("document_id")) or "document_id" not in c]

    def entity_type_counts(self, tagged_only: bool = True, min_mentions: int = 1, plant_only: bool = True) -> dict[str, int]:
        if not self.enabled or self.allowed is None:
            return self.inner.entity_type_counts(tagged_only=tagged_only, min_mentions=min_mentions, plant_only=plant_only)
        counts: dict[str, int] = {}
        for e in self.list_entities(tagged_only=tagged_only, min_mentions=min_mentions, limit=100_000, plant_only=plant_only):
            counts[e.entity_type or "Equipment"] = counts.get(e.entity_type or "Equipment", 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    # ------------------------------------------------------------------ forwarding
    def __getattr__(self, item: str) -> Any:
        """Forward anything not overridden, filtering list results by document and grant."""
        target = getattr(self.inner, item)
        if item in _SINGLE_METHODS:
            def single(*a, **kw):
                r = target(*a, **kw)
                if r is None:
                    return None
                if isinstance(r, EntityRecord):
                    kept = self._filter_entities([r])
                    return kept[0] if kept else None
                if self._ok(r):
                    return r
                self._note(getattr(r, "document_id", None))
                return None
            return single
        if item in _ENTITY_LIST_METHODS:
            return lambda *a, **kw: self._entity_list(item, target, *a, **kw)
        if item in _LIST_METHODS or item == "conflicts_for":
            return lambda *a, **kw: self._filter(target(*a, **kw))
        return target

    def _entity_list(self, item: str, target, *a, **kw) -> list[EntityRecord]:
        """An entity lookup, filtered — and asked again, wider, if the filter emptied it.

        ``resolve_entity`` is an *exact* resolver: "crude desalter" matches one record, the one in
        the CDU manual, and it returns that alone however high the limit. Filter it for someone
        below that tier and the list is empty, so the resolver concludes the equipment does not
        exist — even when an open document describes the same thing under a slightly different
        name, and even when an approved grant has just opened it. Then the answer comes back
        identical to the refused one and the key looks broken.

        So when the filter empties an exact resolution, the looser search is asked the same
        question and *that* is filtered. This is not a relaxation: every record still has to pass
        the same check against the same allowlist and the same grant. There is simply more for the
        check to choose from, and what comes back is only ever something this caller may read.
        """
        requested = int(kw.get("limit") or 0) or _DEFAULT_ENTITY_LIMIT
        raw = target(*a, **kw)
        rows = self._filter_entities(raw)
        if not self.enabled or self.allowed is None:
            return rows

        # Two ways the backend can hand back too little to filter usefully:
        #
        #   *empty* — everything it ranked highest is above this caller's line (the exact resolver
        #   returning one restricted record), and
        #
        #   *truncated* — it applied the limit first and filtering happened second, so a caller
        #   scoped to one document gets whatever few of its records survived a corpus-wide slice.
        #   A conversation scoped to an attachment saw five of its own seventy entities this way,
        #   and the inventory answer went on to say the document contained almost nothing.
        #
        # Either way the fix is the same: ask for far more and filter *that*. Every record still
        # passes the same check against the same allowlist and the same grant.
        truncated = len(raw) >= requested and len(rows) < requested
        if rows and not truncated:
            return rows

        before = dict(self.withheld)          # the retry must not double-count what was withheld
        mention = next((x for x in a if isinstance(x, str)), "") or str(kw.get("mention") or kw.get("query") or "")
        wider = max(requested * 20, _WIDE_ENTITY_LIMIT)
        try:
            if item == "resolve_entity" and mention:
                wide_rows = self._filter_entities(self.inner.search_entities(mention, limit=wider))
            else:
                wide_rows = self._filter_entities(target(*a, **{**kw, "limit": wider}))
        except Exception as exc:
            logger.debug("widened entity lookup failed (%s): %s", item, exc)
            wide_rows = []
        self.withheld = before
        return (wide_rows or rows)[:requested]

    # ------------------------------------------------------------------ mutation passthrough
    def add(self, service) -> None:
        self.inner.add(service)

    def remove(self, service) -> None:
        self.inner.remove(service)

    @property
    def primary(self):
        return getattr(self.inner, "primary", self.inner)


class SealedKnowledgeView:
    """A view over the *withheld* documents, used only to size an access request.

    Nothing this returns is ever rendered, cited or handed to the model on behalf of the person
    who triggered it. It exists so ``scope_probe`` can answer one question — "which records would
    answer this?" — and reduce the result to a list of opaque ids before anything leaves.

    It is a separate class rather than a flag on the guard because the distinction matters when
    reading the code: if an object of this type reaches a renderer, that is a bug, and it should
    be obvious at the call site.
    """

    def __init__(self, inner, document_ids: list[str] | set[str]) -> None:
        self.inner = inner
        self.documents_in_scope = set(document_ids)

    def _mine(self, rows: list) -> list:
        return [r for r in rows if getattr(r, "document_id", None) in self.documents_in_scope]

    def claims_for_query(self, entity_uids: list[str], text: str, limit: int = 20) -> list:
        out: list = []
        for uid in entity_uids[:4]:
            out.extend(self._mine(self.inner.entity_claims(uid)))
        if text:
            out.extend(self._mine(self.inner.search_claims(text=text, limit=limit)))
        return out[:limit]

    def chunks_for_query(self, text: str, entity_uids: list[str], limit: int = 8) -> list:
        rows = self.inner.search_chunks(text, k=limit * 2, document_ids=sorted(self.documents_in_scope),
                                        entity_uids=entity_uids or None)
        return self._mine(rows)[:limit]

    def chunks_of_entities(self, entity_uids: list[str], limit: int = 6) -> list:
        """Chunks this equipment is actually attached to — membership, not a relevance boost.

        ``search_chunks(entity_uids=...)`` treats the uids as a ranking signal, so a document that
        merely shares a word with the question comes back too. For deciding *which tier holds the
        answer*, that is the wrong question; this one is a hard filter.
        """
        out: list = []
        for uid in (entity_uids or [])[:4]:
            out.extend(self._mine(self.inner.chunks_for_entity(uid, limit=limit)))
        return out[:limit]

    def procedures_for_query(self, text: str, entity_uids: list[str], limit: int = 4) -> list:
        out: list = []
        for uid in (entity_uids or [None])[:2]:
            out.extend(self._mine(self.inner.procedures(entity_uid=uid, query=text, limit=limit)))
        return out[:limit]

    def relations_for(self, entity_uids: list[str], limit: int = 10) -> list:
        out: list = []
        for uid in entity_uids[:3]:
            out.extend(self._mine(self.inner.entity_neighbors(uid)))
        return out[:limit]

    def entities_matching(self, text: str, limit: int = 4) -> list:
        """Equipment *living in these documents* whose name matches the question.

        The uid a requester's resolver produced is no use here: it points at whatever their own
        tier could see, which is by definition not this material. So the question is asked again
        inside the sealed scope. Only records whose own document is already in scope come back,
        which is what keeps the approver's preview inside their clearance.
        """
        if not text:
            return []
        out: list = []
        try:
            rows = self.inner.search_entities(text, limit=limit * 4)
        except Exception:
            return []
        for e in rows:
            if set(e.document_ids or []) & self.documents_in_scope:
                out.append(e)
            if len(out) >= limit:
                break
        return out

    def entities(self, entity_uids: list[str]) -> list:
        """The equipment records themselves.

        A grant that opens claims but not the entity they hang off is useless: the resolver
        looks the equipment up first, fails to find it, and the run stops at a clarification
        before it ever reaches the approved material. The entity is part of the scope.
        """
        out: list = []
        for uid in entity_uids[:6]:
            rec = self.inner.get_entity(uid)
            if rec is not None and set(rec.document_ids or []) & self.documents_in_scope:
                # narrow it to the documents this scope covers, so the id is stable
                out.append(rec)
        return out


def _entity_links(record) -> list[str]:
    """The equipment uids one knowledge record hangs off, whatever shape the record is."""
    out: list[str] = []
    for attr in ("entity_uid", "subject_uid", "source_uid", "target_uid"):
        value = getattr(record, attr, None)
        if isinstance(value, str) and value:
            out.append(value)
    for attr in ("applies_to", "entity_uids"):
        values = getattr(record, attr, None)
        if isinstance(values, list):
            out.extend(v for v in values if isinstance(v, str) and v)
    return out


def scope_probe(inner, *, withheld_document_ids: list[str], question: str, entity_uids: list[str],
                registry=None, limit: int = 40, entities_only: bool = False) -> tuple[dict[str, list[str]], dict]:
    """Find which restricted records bear on a question, as ids only.

    Returns ``({document_id: [record_id, ...]}, tags_by_document)``. The caller turns that into a
    ``ScopeManifest``. No text, value, page or title crosses this boundary — by construction, the
    only thing built here is ``record_id(...)``, which is a hash or a database key.

    ``entities_only`` restricts the probe to records attached to the equipment the question named,
    and drops the free-text sweep. That distinction matters for routing an escalation: a
    full-text search for "crude charge pump" also hits the desalter manual, which mentions crude
    in passing, and scoping a request to a document that merely shares a word would send it to
    the wrong approver and then fail to answer once approved.
    """
    view = SealedKnowledgeView(inner, withheld_document_ids)
    found: dict[str, list[str]] = {d: [] for d in withheld_document_ids}
    seen: set[str] = set()
    # Equipment that the collected records hang off, and the in-scope document that linked it.
    linked: dict[str, str] = {}

    def collect(rows) -> None:
        for r in rows:
            doc = getattr(r, "document_id", None) or next(iter(getattr(r, "document_ids", []) or []), None)
            rid = record_id(r)
            if not doc or not rid or rid in seen or doc not in found:
                continue
            seen.add(rid)
            found[doc].append(rid)
            for uid in _entity_links(r):
                linked.setdefault(uid, doc)

    try:
        collect(view.entities(entity_uids))         # the equipment itself, or the rest is unreachable
        if entities_only:
            if not entity_uids:
                return {}, {}
            collect(view.claims_for_query(entity_uids, "", limit=limit))
            collect(view.procedures_for_query("", entity_uids, limit=4))
            collect(view.relations_for(entity_uids, limit=10))
            collect(view.chunks_of_entities(entity_uids, limit=6))
        else:
            collect(view.claims_for_query(entity_uids, question, limit=limit))
            collect(view.chunks_for_query(question, entity_uids, limit=8))
            collect(view.procedures_for_query(question, entity_uids, limit=4))
            collect(view.relations_for(entity_uids, limit=10))
    except Exception as exc:                        # a probe failure must never fail the request
        logger.warning("scope probe failed: %s", exc)

    # The equipment itself, last, and only where something was actually found. Without it a grant
    # is a list of passages about a thing the requester's resolver cannot name, and the approved
    # answer comes back identical to the refused one.
    #
    # Two sources, and both are filtered the same way: an entity joins the scope only when its own
    # document is already in the scope. That is the invariant that keeps this safe — the approver
    # is cleared for every document in the scope, so nothing they preview is above their line.
    try:
        if any(found.values()):
            candidates = {record_id(e): e for e in view.entities_matching(question)}
            for uid in linked:
                rec = inner.get_entity(uid)
                if rec is not None:
                    candidates.setdefault(record_id(rec), rec)
            for rid, rec in candidates.items():
                docs = [d for d in (rec.document_ids or []) if found.get(d)]
                if not rid or rid in seen or not docs:
                    continue
                seen.add(rid)
                found[docs[0]].append(rid)
    except Exception as exc:
        logger.warning("scope entity pass failed: %s", exc)

    tags = {}
    if registry is not None:
        tags = {d: registry.tag_of(d) for d in withheld_document_ids}
    return {d: ids for d, ids in found.items() if ids}, tags

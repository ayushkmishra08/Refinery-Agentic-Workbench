"""EvidenceStore: collects Evidence across agents, de-duplicates, assigns citation labels.

Agents cite evidence by *key* (Evidence.key()) inside their blocks. At governance time the
store maps keys to stable labels ([1], [2], ...) in order of first use and rewrites the blocks.
"""
from __future__ import annotations

from workbench.core.blocks import EvidenceBlock, EvidenceItem
from workbench.core.evidence import Evidence
from workbench.core.knowledge import ChunkRecord, ClaimRecord, ProcedureRecord, RelationRecord, StepRecord


def evidence_from_claim(c: ClaimRecord) -> Evidence:
    return Evidence(document_id=c.document_id, text=c.evidence or f"{c.subject} {c.predicate} = {c.value} {c.unit or ''}".strip(),
                    page=c.page, chunk_id=c.chunk_id, claim_id=c.claim_id, section_path=c.section_path,
                    source=c.source if c.source in ("table", "rule", "specification", "prose") else "table", revision=c.revision)


def evidence_from_chunk(ch: ChunkRecord, excerpt: str | None = None, max_chars: int = 700) -> Evidence:
    text = (excerpt or ch.text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " ..."
    return Evidence(document_id=ch.document_id, text=text, page=ch.page_start, chunk_id=ch.chunk_id, section_path=ch.section_path,
                    source=ch.chunk_type if ch.chunk_type in ("procedure", "table", "safety", "upset", "control", "equipment", "narrative") else "narrative")


def evidence_from_step(p: ProcedureRecord, s: StepRecord) -> Evidence:
    return Evidence(document_id=p.document_id, text=s.text, page=s.page or p.page_start, chunk_id=(p.chunk_ids[0] if p.chunk_ids else None),
                    claim_id=f"{p.procedure_id}#s{s.sequence:03d}", section_path=p.section_path, source="procedure")


def evidence_from_relation(r: RelationRecord) -> Evidence:
    return Evidence(document_id=r.document_id, text=r.evidence or f"{r.source_name} {r.rel_type} {r.target_name}", page=r.page,
                    chunk_id=r.chunk_id, claim_id=f"rel:{r.source_uid}:{r.rel_type}:{r.target_uid}", source="rule" if r.source == "rule" else "graph",
                    grounding=1.0 if r.source == "rule" else 0.6)


class EvidenceStore:
    def __init__(self) -> None:
        self._by_key: dict[str, Evidence] = {}
        self._labels: dict[str, str] = {}
        self._order: list[str] = []

    def add(self, ev: Evidence) -> str:
        k = ev.key()
        if k not in self._by_key:
            self._by_key[k] = ev
            self._order.append(k)
        return k

    def add_all(self, items: list[Evidence]) -> None:
        for e in items:
            self.add(e)

    def label(self, key: str) -> str | None:
        if key in self._labels:
            return self._labels[key]
        if key not in self._by_key:
            return None
        self._labels[key] = f"[{len(self._labels) + 1}]"
        self._by_key[key].ref = self._labels[key]
        return self._labels[key]

    def relabel_citations(self, refs: list[str]) -> list[str]:
        out = []
        for r in refs:
            lab = self.label(r) if not r.startswith("[") else r
            if lab and lab not in out:
                out.append(lab)
        return out

    def has(self, key: str) -> bool:
        return key in self._by_key

    def get(self, key: str) -> Evidence | None:
        return self._by_key.get(key)

    def labelled(self) -> list[Evidence]:
        """Evidence that received a label, in label order."""
        items = [(lab, self._by_key[k]) for k, lab in self._labels.items()]
        items.sort(key=lambda x: int(x[0].strip("[]")))
        return [e for _, e in items]

    def all(self) -> list[Evidence]:
        return [self._by_key[k] for k in self._order]

    def to_block(self, title: str = "Evidence") -> EvidenceBlock:
        return EvidenceBlock(id="evidence", title=title, items=[
            EvidenceItem(ref=e.ref or "", document_id=e.document_id, page=e.page, chunk_id=e.chunk_id, claim_id=e.claim_id,
                         section_path=e.section_path, text=e.text, source=e.source, revision=e.revision)
            for e in self.labelled()
        ])

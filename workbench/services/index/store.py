"""IndexStore: KnowledgeService over one or more DocumentIndex objects.

Retrieval = BM25 (rank_bm25) + vector similarity (knowledge-layer chunk embeddings, bge-small
query encoder on CPU) fused with reciprocal rank fusion, then an optional cross-encoder
reranker. Entity resolution = exact tag -> alias -> fuzzy token overlap. Everything is
in-memory and deterministic; the store is safe to share across requests.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import defaultdict

import numpy as np

from knowledge_layer.entity_identity import parse_tag
from workbench.core.knowledge import (
    ChunkRecord,
    ClaimRecord,
    ConflictRecord,
    CrossReferenceRecord,
    DocumentInfo,
    DocumentReferenceRecord,
    EntityRecord,
    GlossaryRecord,
    ProcedureRecord,
    RelationRecord,
    SectionRecord,
    StandingInstructionRecord,
)
from workbench.services.index.builder import DocumentIndex, canonical_tag, norm_alias

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")
STOP = {"the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "by", "with", "from", "at", "is", "are", "be", "this", "that",
        "it", "as", "its", "what", "which", "how", "do", "i", "we", "you", "should", "can", "could", "would", "when", "before", "after",
        "into", "than", "then", "there", "their", "these", "those", "has", "have", "had", "was", "were", "will", "shall", "may", "must",
        "not", "no", "yes", "if", "so", "up", "down", "out", "about", "please", "me", "my", "our", "all", "any", "also", "very"}
PROCEDURE_TYPE_SYNONYMS = {
    "startup": {"startup", "start-up", "start up", "start", "starting", "light-off", "light off", "commissioning", "restart", "re-start", "re-startup"},
    "shutdown": {"shutdown", "shut-down", "shut down", "stop", "stopping", "decommission", "de-commission"},
    "changeover": {"changeover", "change over", "change-over", "switch over", "switchover", "swap", "standby"},
    "emergency": {"emergency", "esd", "trip", "power failure", "fire"},
    "isolation": {"isolation", "isolate", "blind", "blinding", "maintenance", "handover", "hand over"},
    "upset_response": {"upset", "troubleshoot", "deviation", "stabilization", "stabilisation"},
    "safety": {"safety", "precaution", "ppe", "hazard"},
    "sampling": {"sampling", "sample"},
    "normal_operation": {"normal operation", "steam out", "steam-out", "water wash", "cleaning"},
}


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower().replace("–", "-")) if t not in STOP and len(t) > 1]


class _Embedder:
    """Lazy bge-small query encoder (CPU by default). Only loaded when vector search is used."""

    def __init__(self, model_name: str, device: str) -> None:
        self.model_name, self.device = model_name, device
        self._model = None
        self._lock = threading.Lock()

    def encode(self, text: str) -> np.ndarray | None:
        try:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(self.model_name, device=self.device)
                v = self._model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
            return np.asarray(v, dtype=np.float32)
        except Exception as exc:
            logger.warning("query embedding unavailable (%s); vector search disabled", exc)
            return None


class _Reranker:
    def __init__(self, model_name: str, device: str) -> None:
        self.model_name, self.device = model_name, device
        self._model = None
        self._failed = False
        self._lock = threading.Lock()

    def scores(self, query: str, passages: list[str]) -> list[float] | None:
        if self._failed or not passages:
            return None
        try:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder

                    self._model = CrossEncoder(self.model_name, device=self.device, max_length=512)
                s = self._model.predict([(query, p) for p in passages], show_progress_bar=False)
            return [float(x) for x in s]
        except Exception as exc:
            logger.warning("reranker unavailable (%s); using fused ranking", exc)
            self._failed = True
            return None


class IndexStore:
    name = "index"

    def __init__(self, indexes: list[DocumentIndex], embeddings: dict[str, dict[str, list[float]]] | None = None,
                 embedding_model: str = "BAAI/bge-small-en-v1.5", embedding_device: str = "cpu",
                 reranker_model: str | None = None, reranker_device: str = "cpu", use_vectors: bool = True,
                 use_reranker: bool = True, rrf_k: int = 60) -> None:
        self.indexes: dict[str, DocumentIndex] = {ix.info.document_id: ix for ix in indexes}
        self.rrf_k = rrf_k
        self.last_scores: dict[str, float] = {}      # uid -> score of the most recent resolve_entity call
        self.use_vectors = use_vectors
        self.use_reranker = use_reranker and bool(reranker_model)
        self._embedder = _Embedder(embedding_model, embedding_device) if use_vectors else None
        self._reranker = _Reranker(reranker_model, reranker_device) if self.use_reranker and reranker_model else None
        # flat chunk table
        self._chunk_ids: list[str] = []
        self._chunks: dict[str, ChunkRecord] = {}
        self._chunk_doc: dict[str, str] = {}
        corpus: list[list[str]] = []
        for ix in indexes:
            for cid in ix.chunk_order:
                ch = ix.chunks[cid]
                self._chunk_ids.append(cid)
                self._chunks[cid] = ch
                self._chunk_doc[cid] = ix.info.document_id
                corpus.append(tokenize(f"{ch.section_path}\n{ch.text}"))
        from rank_bm25 import BM25Okapi

        self._bm25 = BM25Okapi(corpus) if corpus else None
        # vectors
        self._vec_ids: list[str] = []
        self._vecs: np.ndarray | None = None
        if embeddings:
            rows = []
            for ix in indexes:
                emb = embeddings.get(ix.info.document_id) or {}
                for cid in ix.chunk_order:
                    v = emb.get(cid)
                    if v is not None:
                        self._vec_ids.append(cid)
                        rows.append(np.asarray(v, dtype=np.float32))
            if rows:
                self._vecs = np.vstack(rows)
        # entity tables
        self._entities: dict[str, EntityRecord] = {}
        self._alias: dict[str, list[str]] = defaultdict(list)
        self._entity_doc: dict[str, str] = {}
        for ix in indexes:
            for uid, e in ix.entities.items():
                self._entities[uid] = e
                self._entity_doc[uid] = ix.info.document_id
            for k, uids in ix.alias_index.items():
                self._alias[k].extend(uids)
        self._alias_tokens: dict[str, set[str]] = {k: set(k.replace("-", " ").split()) for k in self._alias}
        # claims by subject
        self._claims_by_subject: dict[str, list[ClaimRecord]] = defaultdict(list)
        self._claims: list[ClaimRecord] = []
        for ix in indexes:
            for c in ix.claims:
                self._claims.append(c)
                if c.subject_uid:
                    self._claims_by_subject[c.subject_uid].append(c)
        # relations by endpoint
        self._rel_out: dict[str, list[RelationRecord]] = defaultdict(list)
        self._rel_in: dict[str, list[RelationRecord]] = defaultdict(list)
        for ix in indexes:
            for r in ix.relations:
                self._rel_out[r.source_uid].append(r)
                self._rel_in[r.target_uid].append(r)
        # procedures
        self._procedures: dict[str, ProcedureRecord] = {}
        for ix in indexes:
            self._procedures.update(ix.procedures)
        # entity -> chunks
        self._entity_chunks: dict[str, list[str]] = defaultdict(list)
        for ix in indexes:
            for cid, uids in ix.chunk_entities.items():
                for u in uids:
                    self._entity_chunks[u].append(cid)
        logger.info("IndexStore: %d docs, %d chunks, %d entities, %d claims, %d relations, %d procedures, vectors=%s",
                    len(indexes), len(self._chunk_ids), len(self._entities), len(self._claims), sum(len(v) for v in self._rel_out.values()),
                    len(self._procedures), self._vecs is not None)

    # ------------------------------------------------------------------ documents
    def documents(self) -> list[DocumentInfo]:
        return [ix.info for ix in self.indexes.values()]

    def document_profile(self, document_id: str) -> dict:
        ix = self.indexes.get(document_id)
        return ix.profile if ix else {}

    def chapters(self, document_id: str | None = None) -> list[dict]:
        out: list[dict] = []
        for ix in self.indexes.values():
            if document_id and ix.info.document_id != document_id:
                continue
            out.extend(ix.chapters)
        return out

    # ------------------------------------------------------------------ entities
    def get_entity(self, entity_uid: str) -> EntityRecord | None:
        return self._entities.get(entity_uid)

    def resolve_entity(self, mention: str, limit: int = 5) -> list[EntityRecord]:
        mention = mention.strip()
        if not mention:
            return []
        scored: dict[str, float] = {}
        tag = canonical_tag(mention)
        if tag:
            ident = parse_tag(tag)
            for k in (norm_alias(tag), norm_alias(ident.parent) if ident and ident.parent else None):
                if k and k in self._alias:
                    for uid in self._alias[k]:
                        scored[uid] = max(scored.get(uid, 0), 1.0 if k == norm_alias(tag) else 0.9)
            if not scored and ident and ident.plant is None:
                # user typed P-01 without the plant prefix: match tags ending with -P-01
                suffix = norm_alias(tag)
                for k, uids in self._alias.items():
                    if k.endswith("-" + suffix) or k == suffix:
                        for uid in uids:
                            scored[uid] = max(scored.get(uid, 0), 0.8)
        key = norm_alias(mention)
        if key in self._alias:
            for uid in self._alias[key]:
                scored[uid] = max(scored.get(uid, 0), 0.95)
        if not scored:
            qtoks = set(key.replace("-", " ").split()) - STOP
            if qtoks:
                for k, ktoks in self._alias_tokens.items():
                    if not ktoks or "-" in k and len(ktoks) <= 2:
                        continue
                    inter = len(qtoks & ktoks)
                    if inter == 0:
                        continue
                    jacc = inter / len(qtoks | ktoks)
                    if jacc >= 0.5 or (inter >= 2 and inter == len(qtoks)):
                        for uid in self._alias[k]:
                            scored[uid] = max(scored.get(uid, 0), 0.5 + 0.4 * jacc)
        def rank_key(kv: tuple[str, float]) -> tuple:
            uid, score = kv
            e = self._entities.get(uid)
            tagged = 1 if e and e.canonical_tag else 0
            mentions = e.mention_count if e else 0
            # a tagged entity beats an untagged twin at equal alias score; mentions break remaining ties
            return (-(score + 0.02 * tagged + min(mentions, 200) / 10000.0),)

        ranked = sorted(scored.items(), key=rank_key)
        out: list[EntityRecord] = []
        for uid, score in ranked[:limit]:
            e = self._entities.get(uid)
            if e is None:
                continue
            out.append(e.model_copy(update={"mention_count": e.mention_count}))
            self.last_scores[uid] = round(score, 3)
        return out

    def search_entities(self, query: str, entity_type: str | None = None, limit: int = 10) -> list[EntityRecord]:
        qtoks = set(tokenize(query))
        hits: list[tuple[float, EntityRecord]] = []
        for e in self._entities.values():
            if entity_type and (e.entity_type or "").lower() != entity_type.lower():
                continue
            etoks = set(tokenize(" ".join([e.name] + e.aliases[:10])))
            inter = len(qtoks & etoks)
            if inter:
                hits.append((inter / max(1, len(qtoks)) + min(e.mention_count, 50) / 500, e))
        hits.sort(key=lambda x: -x[0])
        return [e for _, e in hits[:limit]]

    # ------------------------------------------------------------------ claims
    def entity_claims(self, entity_uid: str, predicate: str | None = None, context: dict | None = None) -> list[ClaimRecord]:
        rows = list(self._claims_by_subject.get(entity_uid, []))
        e = self._entities.get(entity_uid)
        if e and e.canonical_tag:
            # include train children / parent claims (11-PM-01A/B <-> 11-PM-01)
            ident = parse_tag(e.canonical_tag)
            related = set(ident.children if ident else [])
            if ident and ident.parent:
                related.add(ident.parent)
            for tag in related:
                k = norm_alias(tag)
                for uid in self._alias.get(k, []):
                    if uid != entity_uid:
                        rows.extend(self._claims_by_subject.get(uid, []))
        if predicate:
            rows = [c for c in rows if c.predicate == predicate or predicate in c.predicate]
        for k, v in (context or {}).items():
            if v is None:
                continue
            rows = [c for c in rows if (getattr(c, k, None) or "").lower() == str(v).lower()]
        seen: set[str] = set()
        out = []
        for c in rows:
            if c.claim_id in seen:
                continue
            seen.add(c.claim_id)
            out.append(c)
        return out

    def search_claims(self, subject: str | None = None, predicate: str | None = None, scenario: str | None = None,
                      text: str | None = None, limit: int = 50) -> list[ClaimRecord]:
        rows = self._claims
        if subject:
            s = norm_alias(subject)
            rows = [c for c in rows if s in norm_alias(c.subject)]
        if predicate:
            rows = [c for c in rows if predicate in c.predicate]
        if scenario:
            sc = scenario.lower()
            rows = [c for c in rows if sc in (c.scenario or "").lower() or sc in (c.qualifier or "").lower()]
        if text:
            toks = set(tokenize(text))
            rows = [c for c in rows if toks & set(tokenize(c.evidence + " " + c.subject + " " + (c.qualifier or "")))]
        return rows[:limit]

    def conflicts_for(self, entity_uid: str, predicate: str | None = None) -> list[ConflictRecord]:
        claims = self.entity_claims(entity_uid, predicate)
        groups: dict[str, list[ClaimRecord]] = defaultdict(list)
        for c in claims:
            if c.numeric_value is None:
                continue
            groups[c.context_key].append(c)
        out: list[ConflictRecord] = []
        e = self._entities.get(entity_uid)
        subject = e.name if e else entity_uid
        for key, rows in groups.items():
            if len(rows) < 2:
                continue
            distinct_sources = {(c.document_id, c.page, c.chunk_id) for c in rows}
            if len(distinct_sources) < 2:
                continue
            values = {round(c.numeric_value, 4) for c in rows if c.numeric_value is not None}
            status = "potential_conflict" if len(values) > 1 else "corroborated"
            out.append(ConflictRecord(subject=subject, predicate=rows[0].predicate, context_key=key, claims=rows, status=status))
        return out

    # ------------------------------------------------------------------ relations
    def entity_neighbors(self, entity_uid: str, rel_types: list[str] | None = None, direction: str = "both", hops: int = 1) -> list[RelationRecord]:
        frontier = {entity_uid}
        seen_rel: set[tuple[str, str, str]] = set()
        out: list[RelationRecord] = []
        visited: set[str] = set()
        for _ in range(max(1, hops)):
            next_frontier: set[str] = set()
            for uid in frontier:
                if uid in visited:
                    continue
                visited.add(uid)
                rels: list[RelationRecord] = []
                if direction in ("both", "out"):
                    rels.extend(self._rel_out.get(uid, []))
                if direction in ("both", "in"):
                    rels.extend(self._rel_in.get(uid, []))
                for r in rels:
                    if rel_types and r.rel_type not in rel_types:
                        continue
                    key = (r.source_uid, r.target_uid, r.rel_type)
                    if key in seen_rel:
                        continue
                    seen_rel.add(key)
                    out.append(r)
                    next_frontier.add(r.target_uid if r.source_uid == uid else r.source_uid)
            frontier = next_frontier - visited
            if not frontier:
                break
        return out

    # ------------------------------------------------------------------ procedures
    def get_procedure(self, procedure_id: str) -> ProcedureRecord | None:
        return self._procedures.get(procedure_id)

    def procedures(self, entity_uid: str | None = None, query: str | None = None, procedure_type: str | None = None, limit: int = 10) -> list[ProcedureRecord]:
        ent = self._entities.get(entity_uid) if entity_uid else None
        ent_aliases = set()
        if ent:
            ent_aliases = {norm_alias(a) for a in ent.aliases} | {norm_alias(ent.name)}
            if ent.canonical_tag:
                ident = parse_tag(ent.canonical_tag)
                if ident:
                    ent_aliases.update(norm_alias(c) for c in ident.children)
        qtoks = set(tokenize(query)) if query else set()
        wanted_types: set[str] = set()
        if procedure_type:
            wanted_types.add(procedure_type)
        if query:
            ql = query.lower()
            for ptype, syns in PROCEDURE_TYPE_SYNONYMS.items():
                if any(s in ql for s in syns):
                    wanted_types.add(ptype)
        scored: list[tuple[float, ProcedureRecord]] = []
        for p in self._procedures.values():
            score = 0.0
            steps_text = " ".join(s.text for s in p.steps)
            head = f"{p.title} {p.section_path}"
            head_norm = norm_alias(head)
            steps_norm = norm_alias(steps_text[:6000])
            if ent:
                if entity_uid in p.applies_to:
                    score += 2.0
                tag_hits = sum(1 for s in p.steps for t in s.tags if norm_alias(t) in ent_aliases)
                if tag_hits:
                    score += 1.0 + min(tag_hits, 5) * 0.2
                if any(a and len(a) >= 4 and a in head_norm for a in ent_aliases):
                    score += 3.5                      # the procedure sits in the equipment's own section
                elif any(a and len(a) >= 6 and a in steps_norm for a in ent_aliases):
                    score += 1.0
            if wanted_types:
                if p.procedure_type in wanted_types:
                    score += 4.0                      # the requested action type dominates
                elif p.procedure_type in ("procedure", "normal_operation") and "commissioning" not in wanted_types:
                    score += 0.2
                else:
                    score -= 1.0
            if qtoks:
                htoks = set(tokenize(head))
                stoks = set(tokenize(steps_text[:4000]))
                score += 1.2 * len(qtoks & htoks) / max(1, len(qtoks)) + 0.5 * len(qtoks & stoks) / max(1, len(qtoks))
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda x: (-x[0], x[1].page_start or 0))
        out = []
        for score, p in scored[:limit]:
            out.append(p.model_copy(update={"score": round(score, 3)}))
        return out

    # ------------------------------------------------------------------ text retrieval
    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        return self._chunks.get(chunk_id)

    def chunks_for_entity(self, entity_uid: str, limit: int = 20) -> list[ChunkRecord]:
        ids = self._entity_chunks.get(entity_uid, [])
        e = self._entities.get(entity_uid)
        if e and e.canonical_tag:
            ident = parse_tag(e.canonical_tag)
            for tag in (ident.children if ident else []):
                for uid in self._alias.get(norm_alias(tag), []):
                    ids = ids + self._entity_chunks.get(uid, [])
        out = []
        for cid in dict.fromkeys(ids):
            ch = self._chunks.get(cid)
            if ch:
                out.append(ch.model_copy(update={"match_reason": "entity"}))
            if len(out) >= limit:
                break
        return out

    def _bm25_rank(self, query: str, k: int) -> list[tuple[str, float]]:
        if self._bm25 is None:
            return []
        toks = tokenize(query)
        if not toks:
            return []
        scores = self._bm25.get_scores(toks)
        top = np.argsort(-scores)[:k]
        return [(self._chunk_ids[i], float(scores[i])) for i in top if scores[i] > 0]

    def _vector_rank(self, query: str, k: int) -> list[tuple[str, float]]:
        if self._vecs is None or self._embedder is None:
            return []
        q = self._embedder.encode(query)
        if q is None:
            return []
        sims = self._vecs @ q
        top = np.argsort(-sims)[:k]
        return [(self._vec_ids[i], float(sims[i])) for i in top]

    def search_chunks(self, query: str, k: int = 8, chunk_types: list[str] | None = None, chapters: list[int] | None = None,
                      document_ids: list[str] | None = None, entity_uids: list[str] | None = None, rerank: bool = False) -> list[ChunkRecord]:
        """Hybrid BM25 + vector retrieval. ``rerank=True`` (Phase 2 only) adds the cross-encoder pass; agents' internal searches skip it."""
        pool = max(k * 4, 24)
        fused: dict[str, float] = defaultdict(float)
        reasons: dict[str, set[str]] = defaultdict(set)
        for rank, (cid, _) in enumerate(self._bm25_rank(query, pool)):
            fused[cid] += 1.0 / (self.rrf_k + rank)
            reasons[cid].add("keyword")
        for rank, (cid, _) in enumerate(self._vector_rank(query, pool)):
            fused[cid] += 1.0 / (self.rrf_k + rank)
            reasons[cid].add("vector")
        # entity anchoring: chunks that mention the resolved entities get a bonus
        if entity_uids:
            anchored = set()
            for uid in entity_uids:
                anchored.update(self._entity_chunks.get(uid, []))
                e = self._entities.get(uid)
                if e and e.canonical_tag:
                    ident = parse_tag(e.canonical_tag)
                    for tag in (ident.children if ident else []):
                        for cu in self._alias.get(norm_alias(tag), []):
                            anchored.update(self._entity_chunks.get(cu, []))
            for cid in anchored:
                if cid in fused:
                    fused[cid] += 1.0 / self.rrf_k
                else:
                    fused[cid] += 0.5 / self.rrf_k
                reasons[cid].add("entity")

        def allowed(cid: str) -> bool:
            ch = self._chunks[cid]
            if chunk_types and ch.chunk_type not in chunk_types:
                return False
            if chapters and ch.chapter_number not in chapters:
                return False
            if document_ids and ch.document_id not in document_ids:
                return False
            return True

        candidates = [cid for cid, _ in sorted(fused.items(), key=lambda kv: -kv[1]) if allowed(cid)]
        if chapters and len(candidates) < k:
            # soft chapter preference: fall back to the unfiltered ranking
            candidates += [cid for cid, _ in sorted(fused.items(), key=lambda kv: -kv[1])
                           if cid not in candidates and (not chunk_types or self._chunks[cid].chunk_type in chunk_types)]
        candidates = candidates[: max(k * 2, 12)]
        if rerank and self._reranker is not None and len(candidates) > 1:
            passages = [self._chunks[c].text[:900] for c in candidates]
            scores = self._reranker.scores(query, passages)
            if scores is not None:
                order = np.argsort(-np.asarray(scores))
                ranked = [(candidates[i], float(scores[i])) for i in order]
                for cid in candidates:
                    reasons[cid].add("rerank")
            else:
                ranked = [(c, fused[c]) for c in candidates]
        else:
            ranked = [(c, fused[c]) for c in candidates]
        out = []
        for cid, score in ranked[:k]:
            ch = self._chunks[cid]
            out.append(ch.model_copy(update={"score": round(score, 4), "match_reason": "+".join(sorted(reasons[cid]))}))
        return out

    def sections(self, query: str, limit: int = 10) -> list[SectionRecord]:
        qtoks = set(tokenize(query))
        hits: list[tuple[float, SectionRecord]] = []
        for ix in self.indexes.values():
            for s in ix.sections.values():
                stoks = set(tokenize(s.title))
                inter = len(qtoks & stoks)
                if inter:
                    hits.append((inter / max(1, len(qtoks)) + (0.1 if s.level and s.level <= 2 else 0), s))
        hits.sort(key=lambda x: -x[0])
        return [s for _, s in hits[:limit]]

    # ------------------------------------------------------------------ structure
    def glossary(self, term: str) -> list[GlossaryRecord]:
        t = term.strip().lower()
        out = []
        for ix in self.indexes.values():
            for g in ix.glossary:
                if t == g.term.lower() or t == (g.abbreviation or "").lower() or (len(t) > 3 and t in g.meaning.lower()):
                    out.append(g)
        return out

    def document_references(self, query: str | None = None) -> list[DocumentReferenceRecord]:
        out = []
        q = (query or "").lower()
        for ix in self.indexes.values():
            for r in ix.document_references:
                if not q or q in (r.reference_text + " " + r.title + " " + r.evidence + " " + r.document_type).lower():
                    out.append(r)
        return out

    def standing_instructions(self, query: str | None = None) -> list[StandingInstructionRecord]:
        out = []
        qtoks = set(tokenize(query)) if query else set()
        for ix in self.indexes.values():
            for s in ix.standing_instructions:
                if not qtoks or qtoks & set(tokenize(s.title + " " + (s.remark or ""))):
                    out.append(s)
        return out

    def cross_references(self, section_query: str | None = None) -> list[CrossReferenceRecord]:
        out = []
        q = (section_query or "").lower()
        for ix in self.indexes.values():
            for x in ix.cross_references:
                if not q or q in (x.source_section + " " + x.evidence).lower():
                    out.append(x)
        return out

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        return {
            "documents": len(self.indexes), "chunks": len(self._chunk_ids), "entities": len(self._entities),
            "claims": len(self._claims), "relations": sum(len(v) for v in self._rel_out.values()),
            "procedures": len(self._procedures), "vectors": int(self._vecs.shape[0]) if self._vecs is not None else 0,
            "reranker": bool(self._reranker and not self._reranker._failed),
        }

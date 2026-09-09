"""Neo4j document memory layer.

Neo4j serves as BOTH:
  1. The final knowledge representation (knowledge graph)
  2. The external memory used while processing later chunks

This module handles connection management, schema setup, and CRUD operations.
The graph is initialized BEFORE full semantic extraction begins.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Generator

from src.config import PipelineConfig

logger = logging.getLogger(__name__)


class Neo4jMemory:
    """Neo4j document memory layer for structured knowledge storage and retrieval."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self._driver = None

    def connect(self) -> None:
        """Establish connection to Neo4j."""
        try:
            from neo4j import GraphDatabase
            # Server deprecation notices are not actionable here; keep logs readable.
            logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
            self._driver = GraphDatabase.driver(
                self.config.neo4j.uri,
                auth=(self.config.neo4j.username, self.config.neo4j.password),
            )
            self._driver.verify_connectivity()
            logger.info(f"Connected to Neo4j at {self.config.neo4j.uri}")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            raise

    def close(self) -> None:
        """Close Neo4j connection."""
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed")

    @contextmanager
    def session(self) -> Generator:
        """Get a Neo4j session."""
        if not self._driver:
            raise RuntimeError("Not connected to Neo4j. Call connect() first.")
        session = self._driver.session(database=self.config.neo4j.database)
        try:
            yield session
        finally:
            session.close()

    def setup_schema(self) -> None:
        """Create constraints, indexes, and vector index in Neo4j."""
        constraints = [
            "CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.document_id IS UNIQUE",
            "CREATE CONSTRAINT entity_uid IF NOT EXISTS FOR (e:Entity) REQUIRE e.uid IS UNIQUE",
            "CREATE CONSTRAINT claim_uid IF NOT EXISTS FOR (c:Claim) REQUIRE c.uid IS UNIQUE",
            "CREATE CONSTRAINT term_uid IF NOT EXISTS FOR (t:Term) REQUIRE t.uid IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (ch:Chunk) REQUIRE ch.chunk_id IS UNIQUE",
            "CREATE CONSTRAINT procedure_id IF NOT EXISTS FOR (p:Procedure) REQUIRE p.procedure_id IS UNIQUE",
        ]

        indexes = [
            "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
            "CREATE INDEX entity_canonical IF NOT EXISTS FOR (e:Entity) ON (e.canonical_name)",
            "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)",
            "CREATE INDEX claim_subject IF NOT EXISTS FOR (c:Claim) ON (c.subject_uid)",
            "CREATE INDEX term_term IF NOT EXISTS FOR (t:Term) ON (t.term)",
            "CREATE INDEX chunk_doc IF NOT EXISTS FOR (ch:Chunk) ON (ch.document_id)",
            "CREATE INDEX entity_tag IF NOT EXISTS FOR (e:Entity) ON (e.canonical_tag)",
            "CREATE INDEX claim_predicate IF NOT EXISTS FOR (c:Claim) ON (c.predicate)",
        ]

        # Vector index for semantic chunk retrieval
        vector_index = (
            "CREATE VECTOR INDEX chunk_embeddings IF NOT EXISTS "
            "FOR (c:Chunk) ON (c.embedding) "
            "OPTIONS {indexConfig: {"
            f"`vector.dimensions`: {self.config.embedding.dimensions}, "
            "`vector.similarity_function`: 'cosine'"
            "}}"
        )

        with self.session() as session:
            for constraint in constraints:
                try:
                    session.run(constraint)
                except Exception as e:
                    logger.debug(f"Constraint note: {e}")

            for index in indexes:
                try:
                    session.run(index)
                except Exception as e:
                    logger.debug(f"Index note: {e}")

            try:
                session.run(vector_index)
                logger.info("Vector index created/verified")
            except Exception as e:
                logger.debug(f"Vector index note: {e}")

        logger.info("Neo4j schema setup complete")

    def clear_all_data(self) -> None:
        """Delete all nodes and relationships from the database.
        
        Used for --clean runs to start fresh without corrupt data.
        """
        with self.session() as session:
            # Delete in batches to avoid memory issues
            session.run("MATCH (n) DETACH DELETE n")
            logger.info("All Neo4j data cleared")

    # ── Document Operations ──────────────────────────────────────────

    def upsert_document(self, doc_data: dict[str, Any]) -> None:
        """Insert or update a document node."""
        query = """
        MERGE (d:Document {document_id: $document_id})
        SET d.title = $title,
            d.doc_type = $doc_type,
            d.revision = $revision,
            d.status = $status,
            d.plant = $plant,
            d.unit = $unit,
            d.source_filename = $source_filename,
            d.total_pages = $total_pages,
            d.last_updated = datetime()
        """
        with self.session() as session:
            session.run(query, doc_data)

    def document_exists(self, document_id: str) -> bool:
        with self.session() as session:
            rec = session.run("MATCH (d:Document {document_id: $id}) RETURN count(d) AS n", {"id": document_id}).single()
            return bool(rec and rec["n"])

    def count_chunks(self, document_id: str) -> int:
        with self.session() as session:
            rec = session.run("MATCH (c:Chunk {document_id: $id}) RETURN count(c) AS n", {"id": document_id}).single()
            return int(rec["n"]) if rec else 0

    # ── Entity Operations ────────────────────────────────────────────

    def upsert_entity(self, entity_data: dict[str, Any]) -> None:
        """Insert or update an entity node (global identity) and its per-document provenance.

        Expected keys: uid, name, canonical_name, entity_type, domain, document_id,
        page, section, evidence, confidence, chunk_id; optional: canonical_tag,
        plant, unit, aliases, entity_type_raw, is_stub, resolution_method.
        Creates one (:Document)-[:HAS_ENTITY {page, evidence, confidence}]->(:Entity)
        per (document, entity).
        """
        params = {
            "canonical_tag": None, "plant": None, "unit": None, "aliases": [],
            "entity_type_raw": "", "is_stub": False, "resolution_method": "",
            "section": "", "evidence": "", "confidence": 0.0, "page": 0, "chunk_id": "",
            "grounding": "strong", "source": "llm_pass1",
        } | dict(entity_data)
        params["aliases"] = sorted({str(a) for a in (params.get("aliases") or []) if a})
        query = """
        MERGE (e:Entity {uid: $uid})
        ON CREATE SET
            e.name = $name,
            e.canonical_name = $canonical_name,
            e.canonical_tag = $canonical_tag,
            e.entity_type = $entity_type,
            e.entity_type_raw = $entity_type_raw,
            e.domain = $domain,
            e.plant = $plant,
            e.unit = $unit,
            e.aliases = $aliases,
            e.first_seen_document = $document_id,
            e.document_id = $document_id,
            e.document_ids = [$document_id],
            e.page = $page,
            e.section = $section,
            e.evidence = $evidence,
            e.confidence = $confidence,
            e.chunk_id = $chunk_id,
            e.mention_count = 1,
            e.is_stub = $is_stub,
            e.resolution_method = $resolution_method,
            e.grounding = $grounding,
            e.source = $source,
            e.created = datetime()
        ON MATCH SET
            e.grounding = CASE WHEN $grounding = 'strong' THEN 'strong' ELSE coalesce(e.grounding, $grounding) END,
            e.last_seen_document = $document_id,
            e.last_updated = datetime(),
            e.mention_count = coalesce(e.mention_count, 1) + 1,
            e.document_ids = CASE WHEN $document_id IN coalesce(e.document_ids, [])
                                  THEN e.document_ids ELSE coalesce(e.document_ids, []) + $document_id END,
            e.aliases = [a IN coalesce(e.aliases, []) + $aliases + [$name] WHERE a <> e.name | a],
            e.is_stub = CASE WHEN $is_stub THEN coalesce(e.is_stub, false) ELSE false END,
            e.entity_type = CASE WHEN e.entity_type IS NULL OR e.entity_type IN ['unknown', 'other', 'generic']
                                 THEN $entity_type ELSE e.entity_type END,
            e.entity_type_raw = CASE WHEN e.entity_type_raw IS NULL OR e.entity_type_raw = ''
                                     THEN $entity_type_raw ELSE e.entity_type_raw END,
            e.domain = CASE WHEN e.domain IS NULL OR e.domain = 'unknown' THEN $domain ELSE e.domain END,
            e.canonical_tag = coalesce(e.canonical_tag, $canonical_tag),
            e.plant = coalesce(e.plant, $plant),
            e.unit = coalesce(e.unit, $unit),
            e.evidence = CASE WHEN e.evidence IS NULL OR e.evidence = '' THEN $evidence ELSE e.evidence END,
            e.confidence = CASE WHEN $confidence > coalesce(e.confidence, 0.0) THEN $confidence ELSE e.confidence END,
            e.page = coalesce(e.page, $page)
        WITH e
        SET e.aliases = [a IN e.aliases WHERE a IS NOT NULL AND a <> '' AND a <> e.name]
        WITH e
        MATCH (d:Document {document_id: $document_id})
        MERGE (d)-[h:HAS_ENTITY]->(e)
        ON CREATE SET h.page = $page, h.evidence = $evidence, h.confidence = $confidence,
                      h.chunk_id = $chunk_id, h.created = datetime()
        ON MATCH SET h.confidence = CASE WHEN $confidence > coalesce(h.confidence, 0.0)
                                         THEN $confidence ELSE h.confidence END,
                     h.mentions = coalesce(h.mentions, 1) + 1
        """
        with self.session() as session:
            session.run(query, params)
            # De-duplicate aliases (Cypher has no set type)
            session.run(
                "MATCH (e:Entity {uid: $uid}) WITH e, e.aliases AS al "
                "UNWIND CASE WHEN size(al) = 0 THEN [null] ELSE al END AS a "
                "WITH e, collect(DISTINCT a) AS al SET e.aliases = [x IN al WHERE x IS NOT NULL]",
                {"uid": params["uid"]},
            )

    def find_entity_by_tag(self, canonical_tag: str) -> dict | None:
        """Exact canonical-tag lookup."""
        query = "MATCH (e:Entity {canonical_tag: $tag}) RETURN e LIMIT 1"
        with self.session() as session:
            rec = session.run(query, {"tag": canonical_tag}).single()
            return self._entity_dict(rec["e"]) if rec else None

    def find_entities_by_names_scoped(
        self, names: list[str], plant: str | None = None, unit: str | None = None,
    ) -> list[dict]:
        """Name / canonical_name / alias match, optionally restricted to a plant or unit."""
        if not names:
            return []
        lowered = [n.strip().lower() for n in names if n and n.strip()]
        query = """
        MATCH (e:Entity)
        WHERE (toLower(e.name) IN $names OR toLower(e.canonical_name) IN $names
               OR any(a IN coalesce(e.aliases, []) WHERE toLower(a) IN $names))
          AND ($plant IS NULL OR e.plant IS NULL OR e.plant = $plant)
          AND ($unit IS NULL OR e.unit IS NULL OR e.unit = $unit)
        RETURN e LIMIT 50
        """
        with self.session() as session:
            return [self._entity_dict(r["e"]) for r in session.run(query, {"names": lowered, "plant": plant, "unit": unit})]

    def get_entities_in_chunks(self, chunk_ids: list[str]) -> list[dict]:
        """Entities mentioned by the given chunks (used after vector search)."""
        if not chunk_ids:
            return []
        query = """
        MATCH (ch:Chunk)-[:MENTIONS]->(e:Entity)
        WHERE ch.chunk_id IN $ids
        RETURN DISTINCT e LIMIT 60
        """
        with self.session() as session:
            return [self._entity_dict(r["e"]) for r in session.run(query, {"ids": chunk_ids})]

    def get_entity_identities(self, names_or_tags: list[str]) -> list[dict]:
        """Identity context for the LLM: canonical tag, name, aliases, type, documents."""
        if not names_or_tags:
            return []
        lowered = [n.strip().lower() for n in names_or_tags if n and n.strip()]
        query = """
        MATCH (e:Entity)
        WHERE toLower(coalesce(e.canonical_tag, '')) IN $keys OR toLower(e.name) IN $keys
              OR toLower(e.canonical_name) IN $keys
              OR any(a IN coalesce(e.aliases, []) WHERE toLower(a) IN $keys)
        RETURN e LIMIT 40
        """
        with self.session() as session:
            return [self._entity_dict(r["e"]) for r in session.run(query, {"keys": lowered})]

    def upsert_same_as(
        self, uid_a: str, uid_b: str, confidence: float, method: str, evidence: str = "",
    ) -> None:
        """Record a probable identity link between two entity nodes (never a merge)."""
        if uid_a == uid_b:
            return
        query = """
        MATCH (a:Entity {uid: $a}), (b:Entity {uid: $b})
        MERGE (a)-[r:SAME_AS]->(b)
        ON CREATE SET r.confidence = $confidence, r.method = $method, r.evidence = $evidence,
                      r.created = datetime()
        ON MATCH SET r.confidence = CASE WHEN $confidence > r.confidence THEN $confidence ELSE r.confidence END,
                     r.method = CASE WHEN $confidence > r.confidence THEN $method ELSE r.method END
        """
        with self.session() as session:
            session.run(query, {"a": uid_a, "b": uid_b, "confidence": confidence,
                                "method": method, "evidence": evidence[:500]})

    def upsert_train_link(self, parent: dict[str, Any], child: dict[str, Any]) -> None:
        """Create parent and child asset nodes (if needed) and (parent)-[:HAS_TRAIN]->(child)."""
        for node in (parent, child):
            if node.get("uid"):
                self.upsert_entity({**node, "is_stub": node.get("is_stub", True)})
        query = """
        MATCH (p:Entity {uid: $p}), (c:Entity {uid: $c})
        MERGE (p)-[:HAS_TRAIN]->(c)
        """
        with self.session() as session:
            session.run(query, {"p": parent["uid"], "c": child["uid"]})

    @staticmethod
    def _entity_dict(node) -> dict:
        return {
            "uid": node.get("uid", ""),
            "name": node.get("name", ""),
            "canonical_name": node.get("canonical_name", ""),
            "canonical_tag": node.get("canonical_tag"),
            "entity_type": node.get("entity_type", ""),
            "domain": node.get("domain", ""),
            "plant": node.get("plant"),
            "unit": node.get("unit"),
            "aliases": list(node.get("aliases", []) or []),
            "document_ids": list(node.get("document_ids", []) or []),
            "document_id": node.get("document_id", ""),
            "confidence": node.get("confidence", 0.0),
            "mention_count": node.get("mention_count", 0),
            "is_stub": node.get("is_stub", False),
            "evidence": node.get("evidence", ""),
        }

    def find_entities_by_name(self, names: list[str]) -> list[dict]:
        """Find entities by name, canonical name or alias (case-insensitive)."""
        return self.find_entities_by_names_scoped(names)

    # ── Term/Glossary Operations ─────────────────────────────────────

    def upsert_term(self, term_data: dict[str, Any]) -> None:
        """Insert or update a glossary term."""
        query = """
        MERGE (t:Term {uid: $uid})
        ON CREATE SET
            t.term = $term,
            t.canonical_meaning = $canonical_meaning,
            t.abbreviation = $abbreviation,
            t.full_form = $full_form,
            t.document_id = $document_id,
            t.page = $page,
            t.evidence = $evidence,
            t.confidence = $confidence
        """
        with self.session() as session:
            session.run(query, term_data)

    def get_glossary_terms(self, terms: list[str]) -> list[dict]:
        """Get glossary entries matching given terms."""
        if not terms:
            return []
        query = """
        MATCH (t:Term)
        WHERE t.term IN $terms OR t.abbreviation IN $terms
        RETURN t
        """
        with self.session() as session:
            result = session.run(query, {"terms": terms})
            entries = []
            for record in result:
                node = record["t"]
                entries.append({
                    "uid": node.get("uid", ""),
                    "term": node.get("term", ""),
                    "canonical_meaning": node.get("canonical_meaning", ""),
                    "abbreviation": node.get("abbreviation", ""),
                    "full_form": node.get("full_form", ""),
                    "document_id": node.get("document_id", ""),
                })
            return entries

    # ── Claim Operations ─────────────────────────────────────────────

    def upsert_claim(self, claim_data: dict[str, Any]) -> list[str]:
        """Insert a claim node (per document/page) and link it to its subject entity.

        Conflicting claims (same subject + predicate, different value) are never
        overwritten: a second Claim node exists and both are linked with
        (:Claim)-[:CONFLICTS_WITH {cross_document}]->(:Claim).  Returns the UIDs
        of the conflicting claims found.
        """
        params = {
            "subject_name": "", "unit_raw": claim_data.get("unit", ""), "predicate_raw": "",
            "page": 0, "chunk_id": "", "confidence": 0.0, "evidence": "",
            "canonical_tag": None, "plant": None, "unit_scope": None,
            "grounding": "strong", "source": "llm_pass1", "is_from_table": False, "table_id": None,
            "sentence_index": None,
        } | dict(claim_data)
        query = """
        MERGE (c:Claim {uid: $uid})
        ON CREATE SET
            c.subject_uid = $subject_uid,
            c.predicate = $predicate,
            c.predicate_raw = $predicate_raw,
            c.value = $value,
            c.unit = $unit,
            c.unit_raw = $unit_raw,
            c.claim_category = $claim_category,
            c.document_id = $document_id,
            c.page = $page,
            c.evidence = $evidence,
            c.confidence = $confidence,
            c.source_text = $source_text,
            c.chunk_id = $chunk_id,
            c.grounding = $grounding,
            c.source = $source,
            c.is_from_table = $is_from_table,
            c.table_id = $table_id,
            c.sentence_index = $sentence_index,
            c.created = datetime()
        WITH c
        MERGE (e:Entity {uid: $subject_uid})
        ON CREATE SET
            e.name = $subject_name,
            e.canonical_name = $subject_name,
            e.canonical_tag = $canonical_tag,
            e.entity_type = 'unknown',
            e.domain = 'unknown',
            e.plant = $plant,
            e.unit = $unit_scope,
            e.first_seen_document = $document_id,
            e.document_id = $document_id,
            e.document_ids = [$document_id],
            e.aliases = [],
            e.page = $page,
            e.chunk_id = $chunk_id,
            e.confidence = $confidence,
            e.evidence = $evidence,
            e.mention_count = 1,
            e.is_stub = true,
            e.resolution_method = 'claim_subject',
            e.created = datetime()
        ON MATCH SET
            e.document_ids = CASE WHEN $document_id IN coalesce(e.document_ids, [])
                                  THEN e.document_ids ELSE coalesce(e.document_ids, []) + $document_id END
        MERGE (e)-[:HAS_CLAIM]->(c)
        WITH e, c
        MATCH (d:Document {document_id: $document_id})
        MERGE (d)-[h:HAS_ENTITY]->(e)
        ON CREATE SET h.page = $page, h.evidence = $evidence, h.confidence = $confidence, h.created = datetime()
        WITH e, c
        OPTIONAL MATCH (e)-[:HAS_CLAIM]->(o:Claim)
        WHERE o.uid <> c.uid AND o.predicate = c.predicate
          AND toLower(trim(o.value)) <> toLower(trim(c.value))
          AND coalesce(o.unit, '') = coalesce(c.unit, '')
        FOREACH (x IN CASE WHEN o IS NULL THEN [] ELSE [o] END |
            MERGE (c)-[k:CONFLICTS_WITH]->(x)
            ON CREATE SET k.cross_document = (x.document_id <> c.document_id), k.created = datetime())
        RETURN collect(DISTINCT o.uid) AS conflicts
        """
        with self.session() as session:
            rec = session.run(query, params).single()
            return [u for u in (rec["conflicts"] if rec else []) if u]

    def get_claims(self, entity_uids: list[str]) -> list[dict]:
        """Get existing claims for given entity UIDs."""
        if not entity_uids:
            return []
        query = """
        MATCH (e:Entity)-[:HAS_CLAIM]->(c:Claim)
        WHERE e.uid IN $uids
        RETURN e.uid AS entity_uid, c
        """
        with self.session() as session:
            result = session.run(query, {"uids": entity_uids})
            claims = []
            for record in result:
                node = record["c"]
                claims.append({
                    "entity_uid": record["entity_uid"],
                    "claim": {
                        "uid": node.get("uid", ""),
                        "predicate": node.get("predicate", ""),
                        "value": node.get("value", ""),
                        "unit": node.get("unit", ""),
                        "document_id": node.get("document_id", ""),
                        "page": node.get("page", 0),
                        "confidence": node.get("confidence", 0.0),
                        "evidence": node.get("evidence", ""),
                    },
                })
            return claims

    # ── Relationship Operations ──────────────────────────────────────

    def upsert_relationship(self, rel_data: dict[str, Any]) -> None:
        """Insert a typed engineering relationship, idempotent per
        (subject, object, predicate, document_id); stores document_id, page,
        evidence, chunk_id, confidence on the relationship."""
        stub = """
        MERGE (e:Entity {uid: $uid})
        ON CREATE SET
            e.name = $name, e.canonical_name = $name, e.canonical_tag = $canonical_tag,
            e.entity_type = 'unknown', e.domain = 'unknown', e.plant = $plant, e.unit = $unit_scope,
            e.first_seen_document = $document_id, e.document_id = $document_id,
            e.document_ids = [$document_id], e.aliases = [],
            e.page = $page, e.chunk_id = $chunk_id,
            e.confidence = $confidence, e.evidence = $evidence,
            e.mention_count = 1, e.is_stub = true, e.resolution_method = 'relationship_endpoint',
            e.created = datetime()
        ON MATCH SET
            e.document_ids = CASE WHEN $document_id IN coalesce(e.document_ids, [])
                                  THEN e.document_ids ELSE coalesce(e.document_ids, []) + $document_id END
        WITH e
        MATCH (d:Document {document_id: $document_id})
        MERGE (d)-[h:HAS_ENTITY]->(e)
        ON CREATE SET h.page = $page, h.evidence = $evidence, h.confidence = $confidence, h.created = datetime()
        """
        # Relationship type = predicate (FEEDS, CONTROLLED_BY, ...). Predicates are
        # restricted to the RelationshipType enum upstream, so the type is safe.
        predicate = re.sub(r"[^A-Z0-9_]", "_", str(rel_data["predicate"]).upper()) or "ASSOCIATED_WITH"
        query = f"""
        MATCH (s:Entity {{uid: $subject_uid}})
        MATCH (o:Entity {{uid: $object_uid}})
        MERGE (s)-[r:{predicate} {{document_id: $document_id}}]->(o)
        ON CREATE SET r.created = datetime(), r.mention_count = 1
        ON MATCH SET r.mention_count = coalesce(r.mention_count, 1) + 1
        SET r.predicate = $predicate,
            r.predicate_raw = $predicate_raw,
            r.evidence = $evidence,
            r.page = $page,
            r.confidence = $confidence,
            r.chunk_id = $chunk_id,
            r.grounding = $grounding,
            r.source = $source,
            r.sentence_index = $sentence_index
        """
        params = {"predicate_raw": "", "page": 0, "chunk_id": "", "confidence": 0.0, "evidence": "",
                  "grounding": "strong", "source": "llm_pass1", "sentence_index": None} | dict(rel_data)
        with self.session() as session:
            for uid_key, name_key, tag_key in (
                ("subject_uid", "subject_name", "subject_tag"), ("object_uid", "object_name", "object_tag"),
            ):
                session.run(stub, {
                    "uid": params[uid_key], "name": params.get(name_key, ""),
                    "canonical_tag": params.get(tag_key), "plant": params.get("plant"),
                    "unit_scope": params.get("unit_scope"),
                    "document_id": params["document_id"], "page": params["page"],
                    "chunk_id": params["chunk_id"], "confidence": params["confidence"],
                    "evidence": params["evidence"],
                })
            session.run(query, params)

    def link_chunk_provenance(
        self,
        chunk_id: str,
        entity_mentions: list[dict[str, Any]],
        claim_uids: list[str],
    ) -> None:
        """(:Chunk)-[:MENTIONS {evidence, confidence}]->(:Entity), one per (chunk, entity),
        and (:Chunk)-[:SUPPORTS]->(:Claim)."""
        mentions = [
            {"uid": m["uid"], "evidence": (m.get("evidence") or "")[:500],
             "confidence": float(m.get("confidence") or 0.0)}
            for m in entity_mentions if m.get("uid")
        ]
        query = """
        MATCH (ch:Chunk {chunk_id: $chunk_id})
        WITH ch
        UNWIND $mentions AS m
        MATCH (e:Entity {uid: m.uid})
        MERGE (ch)-[r:MENTIONS]->(e)
        ON CREATE SET r.evidence = m.evidence, r.confidence = m.confidence, r.created = datetime()
        ON MATCH SET r.confidence = CASE WHEN m.confidence > coalesce(r.confidence, 0.0)
                                         THEN m.confidence ELSE r.confidence END
        """
        claims_q = """
        MATCH (ch:Chunk {chunk_id: $chunk_id})
        UNWIND $claim_uids AS cu
        MATCH (c:Claim {uid: cu})
        MERGE (ch)-[:SUPPORTS]->(c)
        """
        with self.session() as session:
            if mentions:
                session.run(query, {"chunk_id": chunk_id, "mentions": mentions})
            if claim_uids:
                session.run(claims_q, {"chunk_id": chunk_id, "claim_uids": claim_uids})

    def get_relationships(self, entity_uids: list[str]) -> list[dict]:
        """Get existing relationships for given entity UIDs."""
        if not entity_uids:
            return []
        query = """
        MATCH (s:Entity)-[r]->(o:Entity)
        WHERE r.predicate IS NOT NULL AND (s.uid IN $uids OR o.uid IN $uids)
        RETURN s.uid AS subject_uid, s.name AS subject_name,
               r.predicate AS predicate, r.evidence AS evidence,
               o.uid AS object_uid, o.name AS object_name,
               r.confidence AS confidence
        """
        with self.session() as session:
            result = session.run(query, {"uids": entity_uids})
            return [dict(record) for record in result]

    # ── Chunk Operations ─────────────────────────────────────────────

    def upsert_chunk(self, chunk_data: dict[str, Any]) -> None:
        """Insert or update a chunk node."""
        query = """
        MERGE (ch:Chunk {chunk_id: $chunk_id})
        SET ch.document_id = $document_id,
            ch.page_start = $page_start,
            ch.page_end = $page_end,
            ch.section = $section,
            ch.sequence = $sequence,
            ch.text = $text,
            ch.contains_table = $contains_table,
            ch.table_ids = $table_ids
        FOREACH (_ IN CASE WHEN $embedding IS NULL THEN [] ELSE [1] END |
            SET ch.embedding = $embedding)
        WITH ch
        MATCH (d:Document {document_id: $document_id})
        MERGE (d)-[:HAS_CHUNK]->(ch)
        """
        params = {
            "text": None, "contains_table": False, "table_ids": [], "embedding": None,
        } | dict(chunk_data)
        with self.session() as session:
            session.run(query, params)

    def vector_search(self, embedding: list[float], k: int = 3) -> list[dict]:
        """Search for similar chunks using vector index."""
        query = """
        CALL db.index.vector.queryNodes('chunk_embeddings', $k, $embedding)
        YIELD node, score
        RETURN node.chunk_id AS chunk_id,
               node.document_id AS document_id,
               node.section AS section,
               score
        """
        with self.session() as session:
            try:
                result = session.run(query, {"k": k, "embedding": embedding})
                return [dict(record) for record in result]
            except Exception as e:
                logger.debug(f"Vector search note (index may not be ready): {e}")
                return []

    # ── Section Entity Operations ────────────────────────────────────

    def get_section_entities(self, section_path: str) -> list[dict]:
        """Get entities associated with a section."""
        query = """
        MATCH (e:Entity)
        WHERE e.section = $section
        RETURN e
        LIMIT 20
        """
        with self.session() as session:
            result = session.run(query, {"section": section_path})
            entities = []
            for record in result:
                node = record["e"]
                entities.append({
                    "uid": node.get("uid", ""),
                    "name": node.get("name", ""),
                    "canonical_name": node.get("canonical_name", ""),
                    "entity_type": node.get("entity_type", ""),
                    "domain": node.get("domain", ""),
                })
            return entities

    def get_related_equipment(
        self, entity_uids: list[str], depth: int = 1,
    ) -> list[dict]:
        """Get equipment entities connected to given entities.
        
        Uses a fixed-depth traversal (1..2 hops) instead of parameterized
        depth, since Neo4j does not allow parameters in variable-length
        relationship patterns.
        """
        if not entity_uids:
            return []
        # Use literal depth instead of $depth parameter
        query = """
        MATCH (e:Entity)-[r*1..2]-(related:Entity)
        WHERE e.uid IN $uids AND ALL(x IN r WHERE x.predicate IS NOT NULL)
        RETURN DISTINCT related.uid AS uid,
               related.name AS name,
               related.entity_type AS entity_type,
               related.domain AS domain
        LIMIT 15
        """
        with self.session() as session:
            try:
                result = session.run(query, {"uids": entity_uids})
                return [dict(record) for record in result]
            except Exception as e:
                logger.debug(f"Related equipment query note: {e}")
                return []

    def get_procedures(self, text: str) -> list[dict]:
        """Find procedures that might be referenced in given text."""
        query = """
        MATCH (p:Procedure)
        WHERE p.title CONTAINS $search_term
        RETURN p
        LIMIT 5
        """
        # Extract potential procedure references
        import re
        proc_refs = re.findall(r"(?:procedure|SOP|step)\s+[\w.-]+", text, re.IGNORECASE)
        if not proc_refs:
            return []

        results = []
        with self.session() as session:
            for ref in proc_refs[:3]:
                result = session.run(query, {"search_term": ref})
                for record in result:
                    node = record["p"]
                    results.append({
                        "procedure_id": node.get("procedure_id", ""),
                        "title": node.get("title", ""),
                        "procedure_type": node.get("procedure_type", ""),
                    })
        return results

    # ── Contradiction Check ──────────────────────────────────────────

    def check_claim_conflict(
        self, subject_uid: str, predicate: str, new_value: str,
    ) -> list[dict]:
        """Check if a conflicting claim exists for the same subject+predicate."""
        query = """
        MATCH (e:Entity {uid: $subject_uid})-[:HAS_CLAIM]->(c:Claim)
        WHERE c.predicate = $predicate AND c.value <> $new_value
        RETURN c
        """
        with self.session() as session:
            result = session.run(query, {
                "subject_uid": subject_uid,
                "predicate": predicate,
                "new_value": new_value,
            })
            conflicts = []
            for record in result:
                node = record["c"]
                conflicts.append({
                    "uid": node.get("uid", ""),
                    "predicate": node.get("predicate", ""),
                    "value": node.get("value", ""),
                    "unit": node.get("unit", ""),
                    "document_id": node.get("document_id", ""),
                    "page": node.get("page", 0),
                    "evidence": node.get("evidence", ""),
                    "confidence": node.get("confidence", 0.0),
                })
            return conflicts

    # ── Schema Introspection ─────────────────────────────────────────

    def get_schema_info(self) -> dict:
        """Get current Neo4j schema information for integrity checks."""
        info = {"labels": [], "rel_types": [], "constraints": [], "indexes": []}
        with self.session() as session:
            # Node labels
            result = session.run("CALL db.labels() YIELD label RETURN label")
            info["labels"] = [r["label"] for r in result]

            # Relationship types
            result = session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
            info["rel_types"] = [r["relationshipType"] for r in result]

            # Node counts per label
            counts = {}
            for label in info["labels"]:
                result = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                record = result.single()
                counts[label] = record["cnt"] if record else 0
            info["node_counts"] = counts

        return info

"""Neo4j document memory layer.

Neo4j serves as BOTH:
  1. The final knowledge representation (knowledge graph)
  2. The external memory used while processing later chunks

This module handles connection management, schema setup, and CRUD operations.
The graph is initialized BEFORE full semantic extraction begins.
"""

from __future__ import annotations

import logging
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

    # ── Entity Operations ────────────────────────────────────────────

    def upsert_entity(self, entity_data: dict[str, Any]) -> None:
        """Insert or update an entity node."""
        query = """
        MERGE (e:Entity {uid: $uid})
        ON CREATE SET
            e.name = $name,
            e.canonical_name = $canonical_name,
            e.entity_type = $entity_type,
            e.domain = $domain,
            e.document_id = $document_id,
            e.page = $page,
            e.section = $section,
            e.evidence = $evidence,
            e.confidence = $confidence,
            e.chunk_id = $chunk_id,
            e.created = datetime()
        ON MATCH SET
            e.last_seen_document = $document_id,
            e.last_updated = datetime()
        """
        with self.session() as session:
            session.run(query, entity_data)

    def find_entities_by_name(self, names: list[str]) -> list[dict]:
        """Find entities by name or canonical name.
        
        Returns raw node data extracted safely in Python to avoid
        Neo4j map projection warnings for properties that may not exist yet.
        """
        if not names:
            return []
        query = """
        MATCH (e:Entity)
        WHERE e.name IN $names OR e.canonical_name IN $names
        RETURN e
        """
        with self.session() as session:
            result = session.run(query, {"names": names})
            entities = []
            for record in result:
                node = record["e"]
                entities.append({
                    "uid": node.get("uid", ""),
                    "name": node.get("name", ""),
                    "canonical_name": node.get("canonical_name", ""),
                    "entity_type": node.get("entity_type", ""),
                    "domain": node.get("domain", ""),
                    "document_id": node.get("document_id", ""),
                    "page": node.get("page", 0),
                    "confidence": node.get("confidence", 0.0),
                })
            return entities

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

    def upsert_claim(self, claim_data: dict[str, Any]) -> None:
        """Insert or update a claim node and link to its subject entity."""
        query = """
        MERGE (c:Claim {uid: $uid})
        ON CREATE SET
            c.subject_uid = $subject_uid,
            c.predicate = $predicate,
            c.value = $value,
            c.unit = $unit,
            c.claim_category = $claim_category,
            c.document_id = $document_id,
            c.page = $page,
            c.evidence = $evidence,
            c.confidence = $confidence,
            c.source_text = $source_text,
            c.chunk_id = $chunk_id
        WITH c
        MATCH (e:Entity {uid: $subject_uid})
        MERGE (e)-[:HAS_CLAIM]->(c)
        """
        with self.session() as session:
            session.run(query, claim_data)

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
        """Insert an engineering relationship between entities."""
        query = """
        MATCH (s:Entity {uid: $subject_uid})
        MATCH (o:Entity {uid: $object_uid})
        MERGE (s)-[r:ENGINEERING_REL {predicate: $predicate}]->(o)
        SET r.evidence = $evidence,
            r.page = $page,
            r.document_id = $document_id,
            r.confidence = $confidence,
            r.chunk_id = $chunk_id
        """
        with self.session() as session:
            session.run(query, rel_data)

    def get_relationships(self, entity_uids: list[str]) -> list[dict]:
        """Get existing relationships for given entity UIDs."""
        if not entity_uids:
            return []
        query = """
        MATCH (s:Entity)-[r:ENGINEERING_REL]->(o:Entity)
        WHERE s.uid IN $uids OR o.uid IN $uids
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
            ch.embedding = $embedding
        """
        with self.session() as session:
            session.run(query, chunk_data)

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
        MATCH (e:Entity)-[r:ENGINEERING_REL*1..2]-(related:Entity)
        WHERE e.uid IN $uids
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

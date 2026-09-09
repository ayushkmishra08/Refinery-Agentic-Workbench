"""Graph integrity tests for Neo4j knowledge graph.

Validates:
  - Every Entity node has required properties
  - Every Claim node is linked to an Entity via HAS_CLAIM
  - Every predicate-typed engineering connects two valid Entity nodes
  - No orphan Claim or relationship nodes
  - No duplicate UIDs
  - All retrieval queries execute without errors
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.memory import Neo4jMemory

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


class IntegrityChecker:
    """Run integrity checks on the Neo4j knowledge graph."""

    def __init__(self, memory: Neo4jMemory):
        self.memory = memory
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def run_all(self) -> bool:
        """Run all integrity checks. Returns True if all pass."""
        print("=" * 60)
        print("Neo4j Knowledge Graph Integrity Checks")
        print("=" * 60)

        self._check_node_counts()
        self._check_entity_properties()
        self._check_claim_linkage()
        self._check_relationship_linkage()
        self._check_duplicate_uids()
        self._check_retrieval_queries()
        self._check_orphan_claims()
        self._check_orphan_chunks()
        self._check_identity_invariants()

        print(f"\n{'=' * 60}")
        print(f"Results: {self.passed} passed, {self.failed} failed, {self.warnings} warnings")
        print("=" * 60)
        return self.failed == 0

    def _pass(self, msg: str) -> None:
        self.passed += 1
        print(f"  [PASS] {msg}")

    def _fail(self, msg: str) -> None:
        self.failed += 1
        print(f"  [FAIL] {msg}")

    def _warn(self, msg: str) -> None:
        self.warnings += 1
        print(f"  [WARN] {msg}")

    def _check_node_counts(self) -> None:
        """Check that nodes exist."""
        print("\n--- Node Counts ---")
        with self.memory.session() as session:
            for label in ["Entity", "Claim", "Chunk", "Document", "Term"]:
                result = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                record = result.single()
                count = record["cnt"] if record else 0
                if count > 0:
                    self._pass(f"{label}: {count} nodes")
                else:
                    self._warn(f"{label}: 0 nodes (may be expected for new runs)")

    def _check_entity_properties(self) -> None:
        """Check that entities have required properties."""
        print("\n--- Entity Property Checks ---")
        required_props = ["uid", "name", "entity_type", "evidence", "first_seen_document"]

        with self.memory.session() as session:
            for prop in required_props:
                query = f"""
                MATCH (e:Entity)
                WHERE e.{prop} IS NULL OR e.{prop} = ''
                RETURN count(e) AS cnt
                """
                result = session.run(query)
                record = result.single()
                missing = record["cnt"] if record else 0
                if missing == 0:
                    self._pass(f"All entities have '{prop}'")
                else:
                    self._fail(f"{missing} entities missing '{prop}'")

    def _check_claim_linkage(self) -> None:
        """Check that every Claim is linked to an Entity via HAS_CLAIM."""
        print("\n--- Claim Linkage ---")
        with self.memory.session() as session:
            # Total claims
            result = session.run("MATCH (c:Claim) RETURN count(c) AS cnt")
            total = result.single()["cnt"]

            # Linked claims
            result = session.run(
                "MATCH (e:Entity)-[:HAS_CLAIM]->(c:Claim) RETURN count(DISTINCT c) AS cnt"
            )
            linked = result.single()["cnt"]

            orphan = total - linked
            if total == 0:
                self._warn("No Claim nodes exist")
            elif orphan == 0:
                self._pass(f"All {total} claims linked to entities via HAS_CLAIM")
            else:
                self._fail(f"{orphan}/{total} claims are orphaned (not linked via HAS_CLAIM)")

    def _check_relationship_linkage(self) -> None:
        """Check that all predicate-typed engineering relationships connect valid entities."""
        print("\n--- Relationship Linkage ---")
        with self.memory.session() as session:
            result = session.run(
                "MATCH (s:Entity)-[r]->(o:Entity) RETURN count(r) AS cnt"
            )
            count = result.single()["cnt"]
            if count > 0:
                self._pass(f"{count} predicate-typed engineering relationships exist")
            else:
                self._warn("No predicate-typed engineering relationships exist")

            # Check if rel has required properties
            result = session.run("""
                MATCH (s:Entity)-[r]->(o:Entity)
                WHERE r.predicate IS NULL
                RETURN count(r) AS cnt
            """)
            missing = result.single()["cnt"]
            if missing == 0:
                self._pass("All relationships have 'predicate' property")
            else:
                self._fail(f"{missing} relationships missing 'predicate' property")

    def _check_identity_invariants(self) -> None:
        """Global-identity invariants (canonical tags, provenance, SAME_AS, claim linkage)."""
        print("\n--- Identity Invariants ---")
        from src.config import load_config
        threshold = load_config().identity.same_as_threshold
        with self.memory.session() as session:
            n = session.run(
                "MATCH (e:Entity) WHERE NOT (:Document)-[:HAS_ENTITY]->(e) RETURN count(e) AS n"
            ).single()["n"]
            (self._pass if n == 0 else self._fail)(f"{n} Entity nodes without a HAS_ENTITY link")

            n = session.run(
                "MATCH (e:Entity) WHERE e.canonical_tag IS NOT NULL "
                "WITH e.canonical_tag AS t, count(*) AS c WHERE c > 1 RETURN count(*) AS n"
            ).single()["n"]
            (self._pass if n == 0 else self._fail)(f"{n} canonical_tag values shared by more than one Entity")

            n = session.run(
                "MATCH ()-[r:SAME_AS]->() WHERE r.confidence < $t RETURN count(r) AS n", {"t": threshold}
            ).single()["n"]
            (self._pass if n == 0 else self._fail)(f"{n} SAME_AS links below threshold {threshold}")

            n = session.run(
                "MATCH (c:Claim) WITH c, size([(e:Entity)-[:HAS_CLAIM]->(c) | e]) AS k "
                "WHERE k <> 1 RETURN count(c) AS n"
            ).single()["n"]
            (self._pass if n == 0 else self._fail)(f"{n} Claim nodes not linked to exactly one Entity")

            n = session.run(
                "MATCH (e:Entity) WHERE e.document_ids IS NULL OR size(e.document_ids) = 0 RETURN count(e) AS n"
            ).single()["n"]
            (self._pass if n == 0 else self._fail)(f"{n} Entity nodes without document_ids")

            rows = session.run(
                "MATCH (e:Entity) WHERE size(coalesce(e.document_ids, [])) > 1 "
                "RETURN e.canonical_tag AS tag, e.name AS name, size(e.document_ids) AS docs ORDER BY docs DESC LIMIT 5"
            ).data()
            if rows:
                self._pass("Cross-document entities: " + ", ".join(f"{r['tag'] or r['name']}({r['docs']})" for r in rows))
            else:
                self._warn("No entity is linked to more than one document yet")

    def _check_duplicate_uids(self) -> None:
        """Check for duplicate UIDs."""
        print("\n--- Duplicate UID Checks ---")
        with self.memory.session() as session:
            for label in ["Entity", "Claim"]:
                result = session.run(f"""
                    MATCH (n:{label})
                    WITH n.uid AS uid, count(*) AS cnt
                    WHERE cnt > 1
                    RETURN uid, cnt
                    LIMIT 5
                """)
                records = list(result)
                if not records:
                    self._pass(f"No duplicate UIDs in {label}")
                else:
                    for r in records:
                        self._fail(f"Duplicate {label} UID: {r['uid']} (count: {r['cnt']})")

    def _check_retrieval_queries(self) -> None:
        """Check that retrieval queries execute without errors."""
        print("\n--- Retrieval Query Checks ---")

        # find_entities_by_name
        try:
            result = self.memory.find_entities_by_name(["test_nonexistent"])
            self._pass("find_entities_by_name executes OK")
        except Exception as e:
            self._fail(f"find_entities_by_name failed: {e}")

        # get_claims
        try:
            result = self.memory.get_claims(["test_uid"])
            self._pass("get_claims executes OK")
        except Exception as e:
            self._fail(f"get_claims failed: {e}")

        # get_relationships
        try:
            result = self.memory.get_relationships(["test_uid"])
            self._pass("get_relationships executes OK")
        except Exception as e:
            self._fail(f"get_relationships failed: {e}")

        # get_related_equipment
        try:
            result = self.memory.get_related_equipment(["test_uid"], depth=1)
            self._pass("get_related_equipment executes OK")
        except Exception as e:
            self._fail(f"get_related_equipment failed: {e}")

        # get_section_entities
        try:
            result = self.memory.get_section_entities("test_section")
            self._pass("get_section_entities executes OK")
        except Exception as e:
            self._fail(f"get_section_entities failed: {e}")

        # get_procedures
        try:
            result = self.memory.get_procedures("test procedure SOP")
            self._pass("get_procedures executes OK")
        except Exception as e:
            self._fail(f"get_procedures failed: {e}")

        # check_claim_conflict
        try:
            result = self.memory.check_claim_conflict("test_uid", "test_pred", "test_val")
            self._pass("check_claim_conflict executes OK")
        except Exception as e:
            self._fail(f"check_claim_conflict failed: {e}")

    def _check_orphan_claims(self) -> None:
        """Check for claims with subject_uid not matching any Entity."""
        print("\n--- Orphan Subject References ---")
        with self.memory.session() as session:
            result = session.run("""
                MATCH (c:Claim)
                WHERE NOT EXISTS {
                    MATCH (e:Entity {uid: c.subject_uid})
                }
                RETURN count(c) AS cnt
            """)
            record = result.single()
            orphan = record["cnt"] if record else 0
            if orphan == 0:
                self._pass("All claims reference existing entities")
            else:
                self._warn(f"{orphan} claims reference non-existent entities (subject_uid mismatch)")

    def _check_orphan_chunks(self) -> None:
        """Check for chunks without document association."""
        print("\n--- Chunk-Document Association ---")
        with self.memory.session() as session:
            result = session.run("""
                MATCH (ch:Chunk)
                WHERE ch.document_id IS NULL OR ch.document_id = ''
                RETURN count(ch) AS cnt
            """)
            record = result.single()
            orphan = record["cnt"] if record else 0
            if orphan == 0:
                self._pass("All chunks have document_id")
            else:
                self._fail(f"{orphan} chunks missing document_id")


def main():
    config = load_config()
    memory = Neo4jMemory(config)
    
    try:
        memory.connect()
        checker = IntegrityChecker(memory)
        success = checker.run_all()
        memory.close()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"ERROR: Could not connect to Neo4j: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

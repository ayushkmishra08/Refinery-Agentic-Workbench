"""Benchmark report for the Refinery Knowledge Layer.

Reads real CDU operating manual Docling output and Neo4j data to produce
a comprehensive benchmark report. Uses ONLY real data — no synthetic data.

Usage:
    python scripts/benchmark.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_layer.config import load_config
from knowledge_layer.memory import Neo4jMemory


def main():
    config = load_config()

    print("=" * 70)
    print("Refinery Knowledge Layer — Benchmark Report")
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    # ── 1. Checkpoint data ────────────────────────────────────────────
    print("\n## Checkpoint Status")
    checkpoint_dir = config.paths.checkpoints_dir
    for cp_file in sorted(checkpoint_dir.glob("*_pipeline.json")):
        data = json.loads(cp_file.read_text(encoding="utf-8"))
        doc_id = data.get("document_id", cp_file.stem)
        completed = len(data.get("completed_chunks", []))
        failed = data.get("failed_chunks", [])
        failed_count = len(failed) if isinstance(failed, list) else 0
        phases = data.get("completed_phases", [])

        print(f"\n  Document: {doc_id}")
        print(f"  Completed phases: {', '.join(phases)}")
        print(f"  Completed chunks: {completed}")
        print(f"  Failed chunks:    {failed_count}")
        if failed_count > 0:
            print(f"  Failed chunk details:")
            for f in failed[:10]:
                if isinstance(f, dict):
                    print(f"    - {f.get('chunk_id', '?')}: {f.get('reason', '?')[:80]}")
                else:
                    print(f"    - {f}")

    # ── 2. Failure directory ──────────────────────────────────────────
    print("\n## Extraction Failures")
    failures_dir = config.paths.data_dir / "failures"
    if failures_dir.exists():
        failure_files = list(failures_dir.glob("*_failure.json"))
        print(f"  Total failure records: {len(failure_files)}")
        if failure_files:
            for ff in failure_files[:5]:
                data = json.loads(ff.read_text(encoding="utf-8"))
                chunk_id = data.get("chunk_id", "?")
                error = data.get("error", "?")[:100]
                print(f"    - {chunk_id}: {error}")
            if len(failure_files) > 5:
                print(f"    ... and {len(failure_files) - 5} more")
    else:
        print("  No failures directory found")

    # ── 3. Neo4j graph statistics ─────────────────────────────────────
    print("\n## Neo4j Graph Statistics")
    try:
        memory = Neo4jMemory(config)
        memory.connect()

        with memory.session() as session:
            # Node counts
            for label in ["Document", "Entity", "Claim", "Chunk", "Term", "Procedure"]:
                result = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                record = result.single()
                count = record["cnt"] if record else 0
                print(f"  {label} nodes: {count}")

            # Relationship counts
            print()
            for rel_type in ["HAS_CLAIM", "HAS_ENTITY", "HAS_CHUNK", "MENTIONS", "SUPPORTS"]:
                result = session.run(
                    f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS cnt"
                )
                record = result.single()
                count = record["cnt"] if record else 0
                print(f"  {rel_type} relationships: {count}")

            # Entity type distribution
            print("\n  Entity type distribution:")
            result = session.run("""
                MATCH (e:Entity)
                RETURN e.entity_type AS type, count(e) AS cnt
                ORDER BY cnt DESC
                LIMIT 20
            """)
            for record in result:
                print(f"    {record['type']}: {record['cnt']}")

            # Entity domain distribution
            print("\n  Entity domain distribution:")
            result = session.run("""
                MATCH (e:Entity)
                RETURN e.domain AS domain, count(e) AS cnt
                ORDER BY cnt DESC
                LIMIT 15
            """)
            for record in result:
                print(f"    {record['domain']}: {record['cnt']}")

            # Claim predicate distribution
            print("\n  Claim predicate distribution:")
            result = session.run("""
                MATCH (c:Claim)
                RETURN c.predicate AS predicate, count(c) AS cnt
                ORDER BY cnt DESC
                LIMIT 15
            """)
            for record in result:
                print(f"    {record['predicate']}: {record['cnt']}")

            # Relationship predicate distribution
            print("\n  Relationship predicate distribution:")
            result = session.run("""
                MATCH ()-[r]->()
                RETURN r.predicate AS predicate, count(r) AS cnt
                ORDER BY cnt DESC
                LIMIT 15
            """)
            for record in result:
                print(f"    {record['predicate']}: {record['cnt']}")

            # Linked vs orphan claims
            total_claims = session.run(
                "MATCH (c:Claim) RETURN count(c) AS cnt"
            ).single()["cnt"]
            linked_claims = session.run(
                "MATCH (e:Entity)-[:HAS_CLAIM]->(c:Claim) RETURN count(DISTINCT c) AS cnt"
            ).single()["cnt"]
            print(f"\n  Claim linkage: {linked_claims}/{total_claims} linked to entities")

            # Sample entities with claims
            print("\n  Sample entities with claims:")
            result = session.run("""
                MATCH (e:Entity)-[:HAS_CLAIM]->(c:Claim)
                RETURN e.name AS entity, e.entity_type AS type,
                       c.predicate AS claim_pred, c.value AS claim_val,
                       c.unit AS claim_unit
                LIMIT 10
            """)
            for record in result:
                unit = record['claim_unit'] or ''
                print(
                    f"    {record['entity']} ({record['type']}): "
                    f"{record['claim_pred']} = {record['claim_val']} {unit}"
                )

            # Sample relationships
            print("\n  Sample relationships:")
            result = session.run("""
                MATCH (s:Entity)-[r]->(o:Entity)
                RETURN s.name AS subject, r.predicate AS predicate, o.name AS object
                LIMIT 10
            """)
            for record in result:
                print(
                    f"    {record['subject']} --[{record['predicate']}]--> {record['object']}"
                )

            # Confidence statistics
            print("\n  Confidence statistics:")
            result = session.run("""
                MATCH (e:Entity)
                RETURN avg(e.confidence) AS avg_conf,
                       min(e.confidence) AS min_conf,
                       max(e.confidence) AS max_conf,
                       count(e) AS total
            """)
            record = result.single()
            if record and record["total"] > 0:
                print(
                    f"    Entity confidence: "
                    f"avg={record['avg_conf']:.3f}, "
                    f"min={record['min_conf']:.3f}, "
                    f"max={record['max_conf']:.3f}"
                )

        memory.close()

    except Exception as e:
        print(f"  ERROR: Could not connect to Neo4j: {e}")

    # ── 4. Ontology summary ──────────────────────────────────────────
    print("\n## Ontology Summary")
    ontology_path = config.paths.knowledge_dir / "ontology.json"
    if ontology_path.exists():
        data = json.loads(ontology_path.read_text(encoding="utf-8"))
        print(f"  Documents processed:    {data.get('total_documents_processed', 0)}")
        print(f"  Total entities seen:    {data.get('total_entities_seen', 0)}")
        print(f"  Total relationships:    {data.get('total_relationships_seen', 0)}")
        print(f"  Total claims seen:      {data.get('total_claims_seen', 0)}")
        print(f"  Domains:                {len(data.get('domains', []))}")
        print(f"  Relationship types:     {len(data.get('relationship_types', []))}")
        print(f"  Claim predicates:       {len(data.get('claim_predicates', []))}")
    else:
        print("  No ontology file found")

    print("\n" + "=" * 70)
    print("Benchmark complete.")


if __name__ == "__main__":
    main()

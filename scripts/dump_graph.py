"""Dump the Neo4j knowledge graph for review.

Usage: python scripts/dump_graph.py [--doc "<document_id>"] [--limit N]

Prints entities (canonical_tag, document_ids, mention_count), predicate-typed
relationships (subject, type, object, page, source, grounding, evidence
sentence) and claims, so weak-grounded items are identifiable.  Also writes
data/reports/<doc>/graph_dump.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.memory import Neo4jMemory  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    doc = args[args.index("--doc") + 1] if "--doc" in args else None
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 400
    config = load_config()
    m = Neo4jMemory(config)
    m.connect()
    where_doc = "WHERE $doc IS NULL OR $doc IN coalesce(e.document_ids, [e.document_id])"
    out: dict = {}
    with m.session() as s:
        out["counts"] = {
            "labels": s.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS c ORDER BY c DESC").data(),
            "rel_types": s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC").data(),
        }
        out["entities"] = s.run(f"""
            MATCH (e:Entity) {where_doc}
            RETURN e.name AS name, e.canonical_tag AS canonical_tag, e.entity_type AS type, e.entity_type_raw AS type_raw,
                   e.document_ids AS document_ids, e.mention_count AS mentions, e.is_stub AS stub,
                   e.grounding AS grounding, e.source AS source, e.page AS page
            ORDER BY coalesce(e.mention_count, 0) DESC, e.name LIMIT $limit
        """, {"doc": doc, "limit": limit}).data()
        out["relationships"] = s.run("""
            MATCH (a:Entity)-[r]->(b:Entity)
            WHERE r.predicate IS NOT NULL AND ($doc IS NULL OR r.document_id = $doc)
            RETURN a.name AS subject, type(r) AS type, b.name AS object, r.page AS page, r.source AS source,
                   r.grounding AS grounding, r.confidence AS confidence, r.evidence AS evidence, r.document_id AS document_id
            ORDER BY r.page, a.name LIMIT $limit
        """, {"doc": doc, "limit": limit}).data()
        out["claims"] = s.run("""
            MATCH (e:Entity)-[:HAS_CLAIM]->(c:Claim)
            WHERE $doc IS NULL OR c.document_id = $doc
            RETURN e.name AS subject, c.predicate AS predicate, c.value AS value, c.unit AS unit, c.page AS page,
                   c.source AS source, c.grounding AS grounding, c.is_from_table AS from_table, c.table_id AS table_id,
                   c.confidence AS confidence, c.evidence AS evidence
            ORDER BY c.page, e.name LIMIT $limit
        """, {"doc": doc, "limit": limit}).data()
        out["conflicts"] = s.run("""
            MATCH (a:Claim)-[k:CONFLICTS_WITH]->(b:Claim)
            RETURN a.subject_uid AS subject_uid, a.predicate AS predicate, a.value AS a_value, b.value AS b_value,
                   a.document_id AS a_doc, b.document_id AS b_doc, k.cross_document AS cross_document
        """).data()
        out["same_as"] = s.run("""
            MATCH (a:Entity)-[r:SAME_AS]->(b:Entity)
            RETURN a.name AS a, b.name AS b, r.confidence AS confidence, r.method AS method
        """).data()
    m.close()

    print("COUNTS:", json.dumps(out["counts"], default=str))
    print(f"\nENTITIES ({len(out['entities'])}):")
    for e in out["entities"]:
        print(f"  {e['name']!r:45s} tag={e['canonical_tag']!s:22s} type={e['type']:16s} docs={e['document_ids']} "
              f"mentions={e['mentions']} stub={e['stub']} grounding={e['grounding']}")
    print(f"\nRELATIONSHIPS ({len(out['relationships'])}):  [source/grounding] subject -TYPE-> object  p.page  | evidence")
    for r in out["relationships"]:
        print(f"  [{r['source']}/{r['grounding']}] {r['subject']!r} -{r['type']}-> {r['object']!r}  p.{r['page']}  | "
              f"{(r['evidence'] or '')[:90]!r}")
    print(f"\nCLAIMS ({len(out['claims'])}):  [source/grounding] subject predicate = value unit  p.page  | evidence")
    for c in out["claims"]:
        print(f"  [{c['source']}/{c['grounding']}] {c['subject']!r} {c['predicate']} = {c['value']} {c['unit']}  p.{c['page']}"
              f"{'  table=' + str(c['table_id']) if c['from_table'] else ''}  | {(c['evidence'] or '')[:80]!r}")
    print(f"\nCONFLICTS ({len(out['conflicts'])}):", out["conflicts"][:20])
    print(f"SAME_AS ({len(out['same_as'])}):", out["same_as"][:20])

    weak_r = sum(1 for r in out["relationships"] if r["grounding"] == "weak")
    weak_c = sum(1 for c in out["claims"] if c["grounding"] == "weak")
    by_src_r = {}
    for r in out["relationships"]:
        by_src_r[r["source"]] = by_src_r.get(r["source"], 0) + 1
    by_src_c = {}
    for c in out["claims"]:
        by_src_c[c["source"]] = by_src_c.get(c["source"], 0) + 1
    print(f"\nSUMMARY: relationships by source {by_src_r} (weak: {weak_r}); claims by source {by_src_c} (weak: {weak_c})")

    target = config.paths.reports_dir / (doc or "all") / "graph_dump.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("written:", target)


if __name__ == "__main__":
    main()

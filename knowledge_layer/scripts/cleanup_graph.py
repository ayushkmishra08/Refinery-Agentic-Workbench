"""Remove noise from a graph built before the qualifier-aware conflict rule.

What it removes (idempotent, safe to run while the pipeline is inserting):

  1. CONFLICTS_WITH edges between two claims of the same table.  Cells of one
     table are different measurements by construction, never contradictions.
  2. CONFLICTS_WITH edges between claims whose raw predicate text differs
     ("table:Vol % cut" vs "table:Wt % cut", "Sulfur" vs "Wax").  Different rows or
     columns were mapped to one predicate; the values were never comparable.
  3. Stub entities auto-created from junk claim subjects (distillation points
     "IBP" / "10%", bare numbers or ranges, generic header words such as
     "Specifications" / "Consumption", numbered section headings) together with
     their claims.  Nothing else references these nodes.
  4. CONFLICTS_WITH edges between tables that sit under different labels /
     operating cases (derived from data/normalized + data/parsed with
     src.table_context, i.e. the same rule the pipeline now applies on insert).

Usage:
    python scripts/cleanup_graph.py            # dry run: report what would change
    python scripts/cleanup_graph.py --apply    # perform the deletions
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from knowledge_layer.config import load_config  # noqa: E402
from knowledge_layer.memory import Neo4jMemory  # noqa: E402
from knowledge_layer.validator import is_non_entity_name  # noqa: E402


def _table_labels(config, document_id: str) -> dict[str, str]:
    """table_id -> context label for one document (empty when its files are missing)."""
    norm_path = config.paths.normalized_dir / f"{document_id}_normalized.json"
    tables_path = config.paths.parsed_dir / document_id / "tables.json"
    if not norm_path.exists() or not tables_path.exists():
        return {}
    from knowledge_layer.schemas.normalized_document import NormalizedDocument
    from knowledge_layer.schemas.parsed_document import ParsedTable
    from knowledge_layer.table_context import build_table_contexts
    normalized = NormalizedDocument.model_validate_json(norm_path.read_text(encoding="utf-8"))
    raw = json.loads(tables_path.read_text(encoding="utf-8"))
    raw_tables = raw.get("tables", raw) if isinstance(raw, dict) else raw
    if isinstance(raw_tables, dict):
        raw_tables = list(raw_tables.values())
    by_id = {t["table_id"]: ParsedTable.model_validate(t) for t in raw_tables}
    ctx = build_table_contexts(normalized, by_id)
    labels = {tid: (c.qualifier_label or "").lower() for tid, c in ctx.items()}
    for tid, tbl in by_id.items():
        if tbl.caption:
            labels[tid] = (labels.get(tid, "") + " / " + tbl.caption.strip().lower()).strip(" /")
    return labels

_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+)+\s+\S")
_LONG_HEADING_CHARS = 45


def _is_junk_subject(name: str) -> bool:
    if is_non_entity_name(name):
        return True
    if _NUMBERED_HEADING_RE.match(name or ""):
        return True
    words = (name or "").split()
    return len(name or "") > _LONG_HEADING_CHARS and words and all(w.isupper() or not w.isalpha() for w in words)


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    config = load_config()
    memory = Neo4jMemory(config)
    memory.connect()
    try:
        with memory.session() as s:
            def one(q, **p):
                rec = s.run(q, **p).single()
                return rec[0] if rec else 0

            print("Before:  claims=%d  entities=%d  CONFLICTS_WITH=%d" % (
                one("MATCH (c:Claim) RETURN count(c)"),
                one("MATCH (e:Entity) RETURN count(e)"),
                one("MATCH ()-[k:CONFLICTS_WITH]->() RETURN count(k)"),
            ))

            same_table = one(
                "MATCH (a:Claim)-[k:CONFLICTS_WITH]->(b:Claim) "
                "WHERE a.table_id IS NOT NULL AND a.table_id = b.table_id RETURN count(k)")
            diff_raw = one(
                "MATCH (a:Claim)-[k:CONFLICTS_WITH]->(b:Claim) "
                "WHERE toLower(replace(coalesce(a.predicate_raw,''),' ','')) <> "
                "      toLower(replace(coalesce(b.predicate_raw,''),' ','')) RETURN count(k)")
            print(f"1. same-table conflict edges:            {same_table}")
            print(f"2. different-property conflict edges:    {diff_raw}")

            stubs = [dict(r) for r in s.run(
                "MATCH (e:Entity) WHERE e.resolution_method = 'claim_subject' "
                "RETURN e.uid AS uid, e.name AS name")]
            junk = [e for e in stubs if _is_junk_subject(e["name"] or "")]
            junk_claims = 0
            for e in junk:
                junk_claims += one("MATCH (e:Entity {uid:$uid})-[:HAS_CLAIM]->(c:Claim) RETURN count(c)", uid=e["uid"])
            print(f"3. junk claim-subject entities:          {len(junk)} (with {junk_claims} claims)")
            for e in sorted(junk, key=lambda x: x["name"] or ""):
                print(f"     - {e['name']!r}")

            # 4. edges between tables under different labels / operating cases
            labels_by_doc: dict[str, dict[str, str]] = {}
            for r in s.run("MATCH (d:Document) RETURN d.document_id AS id"):
                labels_by_doc[r["id"]] = _table_labels(config, r["id"])
            cross_label_ids: list = []
            for r in s.run(
                    "MATCH (a:Claim)-[k:CONFLICTS_WITH]->(b:Claim) "
                    "WHERE a.table_id IS NOT NULL AND b.table_id IS NOT NULL "
                    "RETURN id(k) AS kid, a.document_id AS da, a.table_id AS ta, b.document_id AS db, b.table_id AS tb"):
                la = labels_by_doc.get(r["da"], {}).get(r["ta"])
                lb = labels_by_doc.get(r["db"], {}).get(r["tb"])
                if la is not None and lb is not None and la != lb:
                    cross_label_ids.append(r["kid"])
            print(f"4. different-table-label conflict edges: {len(cross_label_ids)}")

            if not apply:
                print("\nDry run. Re-run with --apply to delete the above.")
                return

            s.run("MATCH (a:Claim)-[k:CONFLICTS_WITH]->(b:Claim) "
                  "WHERE a.table_id IS NOT NULL AND a.table_id = b.table_id DELETE k")
            s.run("MATCH (a:Claim)-[k:CONFLICTS_WITH]->(b:Claim) "
                  "WHERE toLower(replace(coalesce(a.predicate_raw,''),' ','')) <> "
                  "      toLower(replace(coalesce(b.predicate_raw,''),' ','')) DELETE k")
            for e in junk:
                s.run("MATCH (e:Entity {uid:$uid}) OPTIONAL MATCH (e)-[:HAS_CLAIM]->(c:Claim) "
                      "DETACH DELETE c, e", uid=e["uid"])
            for i in range(0, len(cross_label_ids), 500):
                s.run("MATCH ()-[k:CONFLICTS_WITH]->() WHERE id(k) IN $ids DELETE k",
                      ids=cross_label_ids[i:i + 500])

            print("After:   claims=%d  entities=%d  CONFLICTS_WITH=%d" % (
                one("MATCH (c:Claim) RETURN count(c)"),
                one("MATCH (e:Entity) RETURN count(e)"),
                one("MATCH ()-[k:CONFLICTS_WITH]->() RETURN count(k)"),
            ))
    finally:
        memory.close()


if __name__ == "__main__":
    main()

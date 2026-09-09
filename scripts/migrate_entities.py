"""Migrate existing Entity nodes to global canonical-tag identity.

For every Entity whose name/canonical_name/aliases parse as an asset tag, the
new UID is md5(canonical_tag).  Entities that map to the same UID are merged:
aliases, document_ids and mention counts are unioned; HAS_CLAIM, MENTIONS,
HAS_ENTITY, HAS_TRAIN, SAME_AS and all predicate-typed relationships are
re-pointed to the surviving node; duplicates are deleted.

Usage:
    python scripts/migrate_entities.py [--dry-run] [--no-unit-scoping]

Prints before/after Entity counts and the merged pairs.
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config  # noqa: E402
from src.entity_identity import entity_uid, identify_tag  # noqa: E402
from src.memory import Neo4jMemory  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("migrate")


def main() -> int:
    dry = "--dry-run" in sys.argv
    unit_scoping = "--no-unit-scoping" not in sys.argv
    config = load_config()
    memory = Neo4jMemory(config)
    memory.connect()

    with memory.session() as s:
        before = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]
        docs = {r["id"]: (r["plant"], r["unit"]) for r in s.run(
            "MATCH (d:Document) RETURN d.document_id AS id, d.plant AS plant, d.unit AS unit")}
        rows = s.run("""
            MATCH (e:Entity)
            RETURN e.uid AS uid, e.name AS name, e.canonical_name AS canonical_name,
                   coalesce(e.aliases, []) AS aliases, e.canonical_tag AS tag,
                   coalesce(e.document_id, e.first_seen_document) AS doc,
                   coalesce(e.mention_count, 1) AS mentions, e.plant AS plant, e.unit AS unit
        """).data()
    log.info(f"{before} Entity nodes before migration; {len(docs)} documents")

    # 1) compute target uid per entity
    plan: dict[str, list[dict]] = defaultdict(list)     # new_uid -> entities
    retag: list[tuple[str, str, str]] = []              # (old_uid, new_uid, tag)
    for r in rows:
        plant, unit = docs.get(r["doc"], (r["plant"], r["unit"]))
        ident = None
        for cand in [r["tag"], r["name"], r["canonical_name"], *r["aliases"]]:
            ident = identify_tag(cand or "", plant=plant, unit=unit or plant, unit_scoping=unit_scoping,
                                 patterns=config.identity.tag_patterns) if cand else None
            if ident:
                break
        if ident:
            new_uid = entity_uid(ident.canonical)
            plan[new_uid].append({**r, "new_tag": ident.canonical, "new_plant": ident.plant or plant, "new_unit": unit})
            if new_uid != r["uid"]:
                retag.append((r["uid"], new_uid, ident.canonical))
        else:
            plan[r["uid"]].append({**r, "new_tag": r["tag"], "new_plant": plant, "new_unit": unit})

    merges = {uid: ents for uid, ents in plan.items() if len(ents) > 1}
    log.info(f"{len(retag)} entities get a canonical-tag UID; {len(merges)} merge groups")
    for uid, ents in merges.items():
        log.info(f"  merge -> {uid} ({ents[0]['new_tag']}): " + ", ".join(f"{e['name']!r}[{e['doc']}]" for e in ents))
    if dry:
        log.info("--dry-run: no changes written")
        memory.close()
        return 0

    merged_pairs = 0
    with memory.session() as s:
        for new_uid, ents in plan.items():
            survivor = max(ents, key=lambda e: e["mentions"])
            tag = ents[0]["new_tag"]
            # a) re-key the survivor
            s.run("""
                MATCH (e:Entity {uid: $old})
                SET e.uid = $new, e.canonical_tag = coalesce($tag, e.canonical_tag),
                    e.plant = coalesce(e.plant, $plant), e.unit = coalesce(e.unit, $unit),
                    e.document_ids = CASE WHEN e.document_ids IS NULL THEN [coalesce(e.document_id, '')] ELSE e.document_ids END,
                    e.first_seen_document = coalesce(e.first_seen_document, e.document_id),
                    e.aliases = coalesce(e.aliases, [])
            """, {"old": survivor["uid"], "new": new_uid, "tag": tag,
                  "plant": survivor["new_plant"], "unit": survivor["new_unit"]})
            # b) merge the others into it
            for dup in ents:
                if dup["uid"] == survivor["uid"]:
                    continue
                s.run("""
                    MATCH (keep:Entity {uid: $keep}), (dup:Entity {uid: $dup})
                    SET keep.aliases = [a IN keep.aliases + coalesce(dup.aliases, []) + [dup.name, dup.canonical_name]
                                        WHERE a IS NOT NULL AND a <> '' AND a <> keep.name],
                        keep.document_ids = keep.document_ids + [d IN coalesce(dup.document_ids, [coalesce(dup.document_id, '')])
                                                                WHERE NOT d IN keep.document_ids AND d <> ''],
                        keep.mention_count = coalesce(keep.mention_count, 1) + coalesce(dup.mention_count, 1),
                        keep.is_stub = coalesce(keep.is_stub, false) AND coalesce(dup.is_stub, false),
                        keep.entity_type = CASE WHEN keep.entity_type IN ['unknown', 'other', 'generic'] THEN dup.entity_type ELSE keep.entity_type END,
                        keep.domain = CASE WHEN keep.domain = 'unknown' THEN dup.domain ELSE keep.domain END
                """, {"keep": new_uid, "dup": dup["uid"]})
                # Re-point relationships without APOC: one statement per relationship type.
                rel_types = [r["t"] for r in s.run(
                    "MATCH (dup:Entity {uid: $dup})-[r]-() RETURN DISTINCT type(r) AS t", {"dup": dup["uid"]})]
                for t in rel_types:
                    safe = "".join(ch for ch in t if ch.isalnum() or ch == "_")
                    s.run(f"""
                        MATCH (keep:Entity {{uid: $keep}}), (dup:Entity {{uid: $dup}})
                        OPTIONAL MATCH (dup)-[r:{safe}]->(o) WHERE o <> keep
                        WITH keep, dup, collect({{o: o, p: properties(r)}}) AS outs
                        FOREACH (x IN outs | MERGE (keep)-[nr:{safe}]->(x.o) SET nr += x.p)
                        WITH keep, dup
                        OPTIONAL MATCH (i)-[r:{safe}]->(dup) WHERE i <> keep
                        WITH keep, dup, collect({{i: i, p: properties(r)}}) AS ins
                        FOREACH (x IN ins | MERGE (x.i)-[nr:{safe}]->(keep) SET nr += x.p)
                    """, {"keep": new_uid, "dup": dup["uid"]})
                s.run("MATCH (dup:Entity {uid: $dup}) DETACH DELETE dup", {"dup": dup["uid"]})
                merged_pairs += 1
                log.info(f"  merged {dup['name']!r}[{dup['doc']}] into {new_uid} ({tag})")
            # c) dedupe aliases / document_ids
            s.run("""
                MATCH (e:Entity {uid: $uid})
                WITH e, e.aliases AS al, e.document_ids AS di
                UNWIND (CASE WHEN size(al) = 0 THEN [null] ELSE al END) AS a
                WITH e, di, collect(DISTINCT a) AS al
                UNWIND (CASE WHEN size(di) = 0 THEN [null] ELSE di END) AS d
                WITH e, al, collect(DISTINCT d) AS di
                SET e.aliases = [x IN al WHERE x IS NOT NULL], e.document_ids = [x IN di WHERE x IS NOT NULL]
            """, {"uid": new_uid})
            # d) claims keep subject_uid in sync
            s.run("MATCH (e:Entity {uid: $uid})-[:HAS_CLAIM]->(c:Claim) SET c.subject_uid = $uid", {"uid": new_uid})

        after = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]
        dup_tags = s.run("MATCH (e:Entity) WHERE e.canonical_tag IS NOT NULL WITH e.canonical_tag AS t, count(*) AS n "
                         "WHERE n > 1 RETURN count(*) AS dups").single()["dups"]
    log.info(f"Entity nodes: {before} before -> {after} after; merged pairs: {merged_pairs}; "
             f"duplicate canonical_tag groups remaining: {dup_tags}")
    memory.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

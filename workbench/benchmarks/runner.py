"""Benchmark runner: runs prompts.yaml through the orchestrator and scores classification, entity
resolution, plan composition and output blocks. Writes data/workbench/reports/benchmark-<ts>.md."""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from workbench.config import load_config
from workbench.core.request import UserRequest
from workbench.orchestration.orchestrator import Orchestrator

HERE = Path(__file__).parent


def load_prompts(categories: list[str] | None = None) -> list[dict]:
    data = yaml.safe_load((HERE / "prompts.yaml").read_text(encoding="utf-8"))
    out = []
    for cat in data:
        if categories and cat["category"] not in categories:
            continue
        for p in cat["prompts"]:
            out.append({"category": cat["category"], **p})
    return out


def score(item: dict, resp) -> dict:
    task_ok = resp.task_type.value == item.get("expected_task") or (item.get("allow_ambiguous") and resp.task_type.value in ("ambiguous", item.get("expected_task")))
    agents_run = {s.agent for s in (resp.plan.steps if resp.plan else []) if s.status.value == "done"}
    agents_ok = all(a in agents_run for a in item.get("expected_agents", [])) if resp.status not in ("clarification",) else bool(item.get("allow_ambiguous") or item.get("expected_task") == "ambiguous")
    ents = " | ".join(resp.entities).lower()
    ent_ok = any(e.lower() in ents for e in item.get("expected_entities", [])) if item.get("expected_entities") else True
    btypes = {b.type for b in resp.blocks}
    blocks_ok = all(b in btypes for b in item.get("min_blocks", []))
    safety_ok = True
    if item.get("expected_safety") == "restricted":
        safety_ok = resp.status in ("restricted", "clarification") and (resp.requires_human_review or resp.status == "clarification")
    return {"task_ok": bool(task_ok), "agents_ok": bool(agents_ok), "entities_ok": bool(ent_ok), "blocks_ok": bool(blocks_ok), "safety_ok": bool(safety_ok),
            "answered": resp.status in ("answered", "restricted", "clarification", "needs_review"), "confidence": resp.confidence.score, "ms": resp.timing_ms, "llm_calls": resp.llm_calls}


def run_benchmark(categories: list[str] | None = None, limit: int | None = None, verbose: bool = False, write_report: bool = True) -> dict:
    items = load_prompts(categories)
    if limit:
        items = items[:limit]
    # The benchmark measures routing and retrieval, not the door. It runs with access control
    # off rather than holding a standing admin account open for 62 prompts; a real session
    # still signs in. Nothing is written to the credential store by this path.
    cfg = load_config()
    cfg.security.enabled = False
    orch = Orchestrator(cfg)
    rows = []
    t_all = time.time()
    for i, it in enumerate(items):
        resp = orch.ask(UserRequest(text=it["text"], session_id=f"bench-{i:03d}"))   # fresh session: no pronoun carry-over
        sc = score(it, resp)
        rows.append({**it, **sc, "task": resp.task_type.value, "status": resp.status, "entities": resp.entities[:3], "blocks": sorted({b.type for b in resp.blocks})})
        if verbose:
            print(f"[{it['category']:>14}] {'OK ' if all(sc[k] for k in ('task_ok', 'agents_ok', 'entities_ok', 'blocks_ok', 'safety_ok')) else 'MISS'} {resp.task_type.value:15} conf={resp.confidence.score:.2f} {resp.timing_ms:6}ms  {it['text'][:70]}")
    orch.shutdown()
    n = len(rows)
    summary = {
        "prompts": n, "seconds": round(time.time() - t_all, 1),
        "task_accuracy": round(sum(r["task_ok"] for r in rows) / n, 3) if n else 0,
        "agents_ok": round(sum(r["agents_ok"] for r in rows) / n, 3) if n else 0,
        "entities_ok": round(sum(r["entities_ok"] for r in rows) / n, 3) if n else 0,
        "blocks_ok": round(sum(r["blocks_ok"] for r in rows) / n, 3) if n else 0,
        "safety_ok": round(sum(r["safety_ok"] for r in rows) / n, 3) if n else 0,
        "answered": round(sum(r["answered"] for r in rows) / n, 3) if n else 0,
        "mean_confidence": round(sum(r["confidence"] for r in rows) / n, 3) if n else 0,
        "mean_ms": int(sum(r["ms"] for r in rows) / n) if n else 0,
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "misses": [{"text": r["text"], "task": r["task"], "expected": r.get("expected_task"), "entities": r["entities"], "fail": [k for k in ("task_ok", "agents_ok", "entities_ok", "blocks_ok", "safety_ok") if not r[k]]}
                   for r in rows if not all(r[k] for k in ("task_ok", "agents_ok", "entities_ok", "blocks_ok", "safety_ok"))],
    }
    if write_report:
        out = orch.cfg.paths.reports_dir / f"benchmark-{time.strftime('%Y%m%d-%H%M%S')}.md"
        lines = [f"# Benchmark {time.strftime('%Y-%m-%d %H:%M')}", "", f"backend {orch.backend_name}, llm {getattr(orch.llm, 'model', orch.llm.name)}, profile {orch.cfg.profile.name}", "",
                 "| metric | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in summary.items() if k != "misses"] + ["", "| category | prompt | task | status | conf | ms | ok |", "|---|---|---|---|---|---|---|"]
        for r in rows:
            ok = "✓" if all(r[k] for k in ("task_ok", "agents_ok", "entities_ok", "blocks_ok", "safety_ok")) else "✗ " + ",".join(k for k in ("task_ok", "agents_ok", "entities_ok", "blocks_ok", "safety_ok") if not r[k])
            lines.append(f"| {r['category']} | {r['text'][:60]} | {r['task']} | {r['status']} | {r['confidence']:.2f} | {r['ms']} | {ok} |")
        out.write_text("\n".join(lines), encoding="utf-8")
        summary["report"] = str(out)
    return summary

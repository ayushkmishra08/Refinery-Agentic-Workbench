"""Command line: ask / repl (with "btw" side channel) / serve / bench / schema / agents / status.

  python -m workbench ask "What is the normal flow rate of the crude charge pump?"
  python -m workbench repl                # interactive; while a request runs, type "btw <question>"
  python -m workbench serve --port 8000   # FastAPI + SSE for the web front end
  python -m workbench bench               # run the benchmark prompt set (no LLM unless --llm)
  python -m workbench schema              # export JSON schemas for the frontend (docs/schema/)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.ERROR)


def _print_events(ev) -> None:
    if ev.event in ("phase_started",):
        print(f"  ▸ {ev.phase}")
    elif ev.event == "agent_started":
        print(f"    · {ev.agent}: {ev.message}")
    elif ev.event == "agent_finished":
        print(f"      ✓ {ev.message} ({ev.data.get('duration_ms', 0)} ms)")
    elif ev.event in ("replan", "warning", "error"):
        print(f"    ! {ev.event}: {ev.message}")


def cmd_ask(args) -> None:
    from workbench.agents.base import blocks_to_markdown
    from workbench.core.request import UserRequest
    from workbench.orchestration.orchestrator import Orchestrator

    orch = Orchestrator()
    resp = orch.ask(UserRequest(text=args.text, session_id=args.session), on_event=_print_events if args.verbose else None)
    if args.json:
        print(resp.model_dump_json(indent=2))
    else:
        print("\n" + blocks_to_markdown(resp.blocks))
        print(f"\n[{resp.status}] confidence {resp.confidence.score} ({resp.confidence.level}); review={resp.requires_human_review}; {resp.timing_ms} ms; LLM calls {resp.llm_calls}; audit {resp.audit_trail_id}")
    orch.shutdown()


def cmd_repl(args) -> None:
    from workbench.agents.base import blocks_to_markdown
    from workbench.core.request import UserRequest
    from workbench.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(warm_start=True)
    print(f"Refinery workbench REPL — backend {orch.backend_name}, llm {getattr(orch.llm, 'model', orch.llm.name)}, profile {orch.cfg.profile.name}.")
    print("Type a question. While a run is in progress type 'btw <question>' to ask the status agent. 'quit' to exit.\n")
    current = {"run": None}

    def worker(text: str) -> None:
        rs = orch.start(UserRequest(text=text, session_id=args.session), on_event=_print_events if args.verbose else None)
        current["run"] = rs
        while not rs.finished:
            time.sleep(0.2)
        resp = rs.final
        print("\n" + blocks_to_markdown(resp.blocks) if resp else f"\n[run failed] {rs.error}")
        if resp:
            print(f"\n[{resp.status}] confidence {resp.confidence.score}; review={resp.requires_human_review}; {resp.timing_ms} ms; LLM calls {resp.llm_calls}\n")

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.lower() in ("quit", "exit"):
            break
        if line.lower().startswith("btw"):
            q = line[3:].strip(" ,:")
            print(blocks_to_markdown(orch.btw(q, session_id=args.session)) + "\n")
            continue
        rs = current["run"]
        if rs is not None and not rs.finished:
            print("(a request is still running — use 'btw ...' to ask about it, or wait)")
            continue
        threading.Thread(target=worker, args=(line,), daemon=True).start()
        time.sleep(0.3)
    orch.shutdown()


def cmd_serve(args) -> None:
    import uvicorn

    os.environ.setdefault("RWB_WARM_START", "1")
    uvicorn.run("workbench.app.api:app", host=args.host, port=args.port, reload=False, log_level="info")


def cmd_bench(args) -> None:
    from workbench.benchmarks.runner import run_benchmark

    if not args.llm:
        os.environ["RWB_LLM"] = "off"
    summary = run_benchmark(categories=args.category, limit=args.limit, verbose=args.verbose, write_report=True)
    print(json.dumps(summary, indent=2))


def cmd_schema(args) -> None:
    from workbench.core.blocks import Block
    from workbench.core.events import ProgressEvent
    from workbench.core.request import UserRequest
    from workbench.core.result import FinalResponse
    from pydantic import TypeAdapter

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "final_response.schema.json").write_text(json.dumps(FinalResponse.model_json_schema(), indent=2), encoding="utf-8")
    (out / "block.schema.json").write_text(json.dumps(TypeAdapter(Block).json_schema(), indent=2), encoding="utf-8")
    (out / "user_request.schema.json").write_text(json.dumps(UserRequest.model_json_schema(), indent=2), encoding="utf-8")
    (out / "progress_event.schema.json").write_text(json.dumps(ProgressEvent.model_json_schema(), indent=2), encoding="utf-8")
    print(f"schemas written to {out}")


def cmd_agents(args) -> None:
    from workbench.agents.registry import describe_agents

    for a in describe_agents():
        print(f"{a['key']:18} {a['phase']:42} {a['description']}")


def cmd_status(args) -> None:
    from workbench.config import detect_vram_mb, load_config
    from workbench.llm.client import build_llm

    cfg = load_config()
    llm = build_llm(cfg)
    print(json.dumps({"vram_mb": detect_vram_mb(), "profile": cfg.profile.name, "llm_model": cfg.llm.model, "vision_model": cfg.llm.vision_model, "llm_available": llm.available(),
                      "backend": cfg.resolve_backend(), "documents": cfg.discovered_documents(), "reranker": cfg.retrieval.reranker_model if cfg.retrieval.use_reranker else None}, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(prog="workbench", description="Refinery Engineering AI Workbench")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask"); a.add_argument("text"); a.add_argument("--session", default="cli"); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_ask)
    r = sub.add_parser("repl"); r.add_argument("--session", default="repl"); r.set_defaults(fn=cmd_repl)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000); s.set_defaults(fn=cmd_serve)
    b = sub.add_parser("bench"); b.add_argument("--category", nargs="*"); b.add_argument("--limit", type=int); b.add_argument("--llm", action="store_true"); b.set_defaults(fn=cmd_bench)
    sc = sub.add_parser("schema"); sc.add_argument("--out", default="docs/schema"); sc.set_defaults(fn=cmd_schema)
    sub.add_parser("agents").set_defaults(fn=cmd_agents)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = p.parse_args()
    _setup_logging(args.verbose)
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args.fn(args)

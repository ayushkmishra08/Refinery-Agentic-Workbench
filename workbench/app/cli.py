"""Command line: login / ask / repl (with "btw" side channel) / serve / bench / schema / agents / status.

  python -m workbench login               # sign in; the CDU manual is classified and needs a lead engineer
  python -m workbench ask "What is the normal flow rate of the crude charge pump?"
  python -m workbench ask "..." --detail  # every render block instead of the composed answer
  python -m workbench repl                # interactive; follow-ups are read in context of the turns before them
  python -m workbench serve --port 8000   # FastAPI + SSE for the web front end
  python -m workbench bench               # run the benchmark prompt set (no LLM unless --llm)
  python -m workbench schema              # export JSON schemas for the frontend (docs/schema/)
  python -m workbench whoami              # who is signed in and which documents they may read

Every command that reads a document asks for a sign-in first when the stored token does not
open it; ``RWB_AUTH=off`` removes the gate for the benchmark and the test suite.
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


def _quiet_model_loaders() -> None:
    """Keep the model libraries' progress bars and hub warnings out of the thinking display."""
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    for name in ("huggingface_hub", "transformers", "sentence_transformers", "model2vec", "torch"):
        logging.getLogger(name).setLevel(logging.ERROR)


def _print_events(ev) -> None:
    if ev.event in ("phase_started",):
        print(f"  \u25b8 {ev.phase}")
    elif ev.event == "agent_started":
        print(f"    \u00b7 {ev.agent}: {ev.message}")
    elif ev.event == "agent_finished":
        print(f"      \u2713 {ev.message} ({ev.data.get('duration_ms', 0)} ms)")
    elif ev.event in ("replan", "warning", "error", "access_denied"):
        print(f"    ! {ev.event}: {ev.message}")


def _make_display(orch, text: str):
    """A ThinkingDisplay bound to this orchestrator's runtime (model, backend, profile)."""
    from workbench.app.thinking_display import ThinkingDisplay

    return ThinkingDisplay(query=text, llm_model=getattr(orch.llm, "model", None) or orch.llm.name,
                           backend=orch.backend_name, profile=orch.cfg.profile.name, effort=orch.cfg.effort.name)


def _released_markdown(resp, detail: bool) -> str:
    """What the terminal prints: the composed answer, or the full block rendering with --detail."""
    from workbench.agents.base import REASONING_BLOCKS, blocks_to_markdown

    if detail or not resp.answer_markdown.strip():
        return blocks_to_markdown(resp.blocks, skip=REASONING_BLOCKS)
    return resp.answer_markdown


def _finish(orch, display, resp, detail: bool = False) -> None:
    """Print the answer below the thinking and save the trace as JSON."""
    from workbench.services.thinking_store import ThinkingStore

    display.print_answer(resp, _released_markdown(resp, detail))
    path = ThinkingStore(orch.cfg.paths.thinking_dir).save(display.collector.record_response(resp))
    display.print_trace_saved(path)


def cmd_ask(args) -> None:
    from workbench.app.credentials import ensure_access
    from workbench.config import load_config
    from workbench.core.request import UserRequest
    from workbench.orchestration.orchestrator import Orchestrator

    show_thinking = not (args.no_thinking or args.json)
    orch = Orchestrator(load_config(args.effort))
    token = ensure_access(orch, interactive=sys.stdin.isatty() and not args.json)
    display = _make_display(orch, args.text) if show_thinking else None
    on_event = display.on_event if display else (_print_events if args.verbose else None)

    resp = orch.ask(UserRequest(text=args.text, session_id=args.session, auth_token=token), on_event=on_event)

    if args.json:
        print(resp.model_dump_json(indent=2))
    elif display:
        _finish(orch, display, resp, detail=args.detail)
    else:
        print("\n" + _released_markdown(resp, args.detail))
        print(f"\n[{resp.status}] confidence {resp.confidence.score} ({resp.confidence.level}); review={resp.requires_human_review}; {resp.timing_ms} ms; LLM calls {resp.llm_calls}; audit {resp.audit_trail_id}")
    orch.shutdown()


def cmd_repl(args) -> None:
    from workbench.agents.base import blocks_to_markdown
    from workbench.app.credentials import ensure_access, prompt_login
    from workbench.config import load_config
    from workbench.core.request import UserRequest
    from workbench.orchestration.orchestrator import Orchestrator

    show_thinking = not args.no_thinking
    orch = Orchestrator(load_config(args.effort), warm_start=True)
    auth = {"token": ensure_access(orch, interactive=sys.stdin.isatty())}
    who = orch.principal(auth["token"]).describe()
    print(f"Refinery workbench REPL \u2014 effort {orch.cfg.effort.name}, backend {orch.backend_name}, llm {getattr(orch.llm, 'model', orch.llm.name)}, profile {orch.cfg.profile.name}, signed in as {who}.")
    print("Type a question; follow-ups are read in the context of the ones before them. 'btw <question>' asks about a run in progress, 'login' signs in, 'quit' exits.\n")
    current = {"run": None}

    def worker(text: str) -> None:
        display = _make_display(orch, text) if show_thinking else None
        on_ev = display.on_event if display else (_print_events if args.verbose else None)
        rs = orch.start(UserRequest(text=text, session_id=args.session, auth_token=auth["token"]), on_event=on_ev)
        current["run"] = rs
        while not rs.finished:
            time.sleep(0.2)
        resp = rs.final
        if resp is None:
            print(f"\n[run failed] {rs.error}")
        elif display:
            _finish(orch, display, resp, detail=args.detail)
        else:
            print("\n" + _released_markdown(resp, args.detail))
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
        if line.lower() in ("login", "signin", "sign in"):
            auth["token"] = prompt_login(orch) or auth["token"]
            continue
        if line.lower() in ("logout", "signout", "sign out"):
            from workbench.app.credentials import clear_token

            orch.logout(auth["token"])
            clear_token(orch.cfg)
            auth["token"] = None
            print("  signed out.\n")
            continue
        if line.lower().startswith("btw"):
            q = line[3:].strip(" ,:")
            print(blocks_to_markdown(orch.btw(q, session_id=args.session)) + "\n")
            continue
        rs = current["run"]
        if rs is not None and not rs.finished:
            print("(a request is still running \u2014 use 'btw ...' to ask about it, or wait)")
            continue
        threading.Thread(target=worker, args=(line,), daemon=True).start()
        time.sleep(0.3)
    orch.shutdown()


def cmd_login(args) -> None:
    from workbench.app.credentials import prompt_login
    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(load_config(), warm_start=False)
    if not orch.cfg.security.enabled:
        print("Access control is switched off (RWB_AUTH=off); no sign-in is needed.")
        return
    for d in orch.restricted_documents():
        print(f"  {d['title']} — classified {d['clearance']} ({d['reason']})")
    token = prompt_login(orch, username=args.user)
    if token:
        _, decision = orch.access_for(token)
        print(f"  readable documents: {', '.join(decision.allowed) or 'none'}")
        if decision.denied:
            print(f"  still withheld: {', '.join(decision.denied)}")
    orch.shutdown()


def cmd_logout(args) -> None:
    from workbench.app.credentials import clear_token, load_token
    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(load_config(), warm_start=False)
    revoked = orch.logout(load_token(orch.cfg))
    clear_token(orch.cfg)
    print("Signed out." if revoked else "No active sign-in on this machine.")
    orch.shutdown()


def cmd_whoami(args) -> None:
    from workbench.app.credentials import load_token
    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(load_config(), warm_start=False)
    principal, decision = orch.access_for(load_token(orch.cfg))
    print(json.dumps({"principal": principal.username, "role": principal.role.value, "authenticated": principal.authenticated,
                      "clearance": principal.clearance.value, "access_control": orch.cfg.security.enabled,
                      "readable": decision.allowed, "withheld": decision.denied,
                      "documents": orch.restricted_documents()}, indent=2))
    orch.shutdown()


def cmd_passwd(args) -> None:
    import getpass

    from workbench.app.credentials import clear_token
    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator
    from workbench.security.auth import AuthError

    orch = Orchestrator(load_config(), warm_start=False)
    user = args.user or input("username [lead]: ").strip() or "lead"
    try:
        orch.login(user, getpass.getpass("current password: "))
    except AuthError as exc:
        print(f"  {exc}")
        return
    new = getpass.getpass("new password: ")
    if len(new) < 4:
        print("  the new password must be at least 4 characters.")
        return
    if new != getpass.getpass("repeat new password: "):
        print("  the two entries do not match.")
        return
    orch.auth.set_password(user, new)
    orch.auth.revoke_all(user)
    clear_token(orch.cfg)
    print(f"  password changed for {user}; every existing session for that account was signed out.")
    orch.shutdown()


def cmd_users(args) -> None:
    import getpass

    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(load_config(), warm_start=False)
    if args.add:
        password = getpass.getpass(f"password for {args.add}: ")
        cred = orch.auth.add_user(args.add, password, args.role, display_name=args.name or args.add)
        print(f"  added {cred.username} as {cred.role.value}")
    print(json.dumps(orch.auth.users(), indent=2))
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
    print(json.dumps({"vram_mb": detect_vram_mb(), "profile": cfg.profile.name, "effort": cfg.effort.model_dump(), "llm_model": cfg.llm.model, "vision_model": cfg.llm.vision_model, "llm_available": llm.available(),
                      "backend": cfg.resolve_backend(), "documents": cfg.discovered_documents(), "reranker": cfg.retrieval.reranker_model if cfg.retrieval.use_reranker else None}, indent=2))


def cmd_trace(args) -> None:
    from workbench.config import load_config
    from workbench.services.thinking_store import ThinkingStore

    store = ThinkingStore(load_config().paths.thinking_dir)
    saved = store.latest(args.limit)
    if not saved:
        print("No thinking traces yet. Run 'python -m workbench ask \"...\"' first.")
        return
    if not args.show:
        for path in saved:
            trace = json.loads(path.read_text(encoding="utf-8"))
            print(f"{path.name}  {trace.get('task_type', ''):15} {trace.get('status', ''):13} {trace.get('total_duration_ms', 0):>7} ms  {trace.get('query', '')[:60]}")
        return
    trace = json.loads(saved[0].read_text(encoding="utf-8"))
    print(f"# {trace['query']}\n")
    print(f"{trace['task_type']} -> {trace['status']} in {trace['total_duration_ms']} ms ({saved[0].name})\n")
    for ph in trace["phases"]:
        print(f"--- {ph['name']} ({ph['duration_ms']} ms) ---")
        for ag in ph["agents"]:
            model = f" [{ag['model_used']}]" if ag.get("model_used") else ""
            print(f"  {ag['name']}{model}")
            for line in (ag.get("thinking") or "").split("\n"):
                if line.strip():
                    print(f"    | {line}")
            print(f"    -> {ag.get('decision', '')}\n")


def main() -> None:
    from workbench.config import EFFORT_LEVELS

    p = argparse.ArgumentParser(prog="workbench", description="Refinery Engineering AI Workbench")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    effort_help = ("how much work one request may do: low = index only, no model; medium = default; "
                   "high = wider retrieval + reranker + model narrative; ultra = everything on (minutes on a 4 GB card)")
    detail_help = "print every render block (values, topology, evidence, confidence) instead of the composed answer"
    a = sub.add_parser("ask"); a.add_argument("text"); a.add_argument("--session", default="cli"); a.add_argument("--json", action="store_true"); a.add_argument("--effort", choices=list(EFFORT_LEVELS), default=None, help=effort_help); a.add_argument("--no-thinking", action="store_true", help="hide the phase-by-phase thinking and print only the answer"); a.add_argument("--detail", action="store_true", help=detail_help); a.set_defaults(fn=cmd_ask)
    r = sub.add_parser("repl"); r.add_argument("--session", default="repl"); r.add_argument("--effort", choices=list(EFFORT_LEVELS), default=None, help=effort_help); r.add_argument("--no-thinking", action="store_true", help="hide the phase-by-phase thinking and print only the answer"); r.add_argument("--detail", action="store_true", help=detail_help); r.set_defaults(fn=cmd_repl)
    lg = sub.add_parser("login", help="sign in so the classified documents can be read"); lg.add_argument("--user", default=None); lg.set_defaults(fn=cmd_login)
    sub.add_parser("logout", help="revoke this machine's session token").set_defaults(fn=cmd_logout)
    sub.add_parser("whoami", help="who is signed in and what they may read").set_defaults(fn=cmd_whoami)
    pw = sub.add_parser("passwd", help="change an account's password"); pw.add_argument("--user", default=None); pw.set_defaults(fn=cmd_passwd)
    us = sub.add_parser("users", help="list accounts, or add one with --add"); us.add_argument("--add", default=None); us.add_argument("--role", default="engineer", choices=["operator", "engineer", "lead_engineer", "admin"]); us.add_argument("--name", default=None); us.set_defaults(fn=cmd_users)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000); s.set_defaults(fn=cmd_serve)
    b = sub.add_parser("bench"); b.add_argument("--category", nargs="*"); b.add_argument("--limit", type=int); b.add_argument("--llm", action="store_true"); b.set_defaults(fn=cmd_bench)
    sc = sub.add_parser("schema"); sc.add_argument("--out", default="docs/schema"); sc.set_defaults(fn=cmd_schema)
    tr = sub.add_parser("trace", help="list or replay saved thinking traces"); tr.add_argument("--limit", type=int, default=10); tr.add_argument("--show", action="store_true", help="print the newest trace in full"); tr.set_defaults(fn=cmd_trace)
    sub.add_parser("agents").set_defaults(fn=cmd_agents)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = p.parse_args()
    _setup_logging(args.verbose)
    _quiet_model_loaders()
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    args.fn(args)

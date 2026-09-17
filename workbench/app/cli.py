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
    from workbench.app.credentials import require_sign_in
    from workbench.config import load_config
    from workbench.core.request import UserRequest
    from workbench.orchestration.orchestrator import Orchestrator

    show_thinking = not (args.no_thinking or args.json)
    orch = Orchestrator(load_config(args.effort))
    # identity first, question second
    token = require_sign_in(orch, interactive=sys.stdin.isatty() and not args.json)
    display = _make_display(orch, args.text) if show_thinking else None
    on_event = display.on_event if display else (_print_events if args.verbose else None)

    resp = orch.ask(UserRequest(text=args.text, session_id=args.session, auth_token=token,
                                access_key=args.key), on_event=on_event)

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
    from workbench.app.credentials import prompt_login, require_sign_in
    from workbench.config import load_config
    from workbench.core.request import UserRequest
    from workbench.orchestration.orchestrator import Orchestrator

    show_thinking = not args.no_thinking
    orch = Orchestrator(load_config(args.effort), warm_start=True)
    auth = {"token": require_sign_in(orch, interactive=sys.stdin.isatty()), "key": None}
    principal = orch.principal(auth["token"])
    if orch.cfg.security.enabled and orch.cfg.security.require_sign_in_before_prompt and not principal.authenticated:
        print("\nNo one signed in. The workbench does not take questions from an unidentified caller.")
        print("Run 'python -m workbench login' and start again.\n")
        orch.shutdown()
        return
    readable = orch.access_for(auth["token"])[1]
    print(f"Refinery workbench REPL \u2014 effort {orch.cfg.effort.name}, backend {orch.backend_name}, llm {getattr(orch.llm, 'model', orch.llm.name)}, profile {orch.cfg.profile.name}.")
    print(f"Signed in as {principal.describe()} \u2014 may read: {', '.join(readable.allowed) or 'nothing'}"
          + (f"; withheld: {', '.join(readable.denied)}" if readable.denied else "") + ".")
    print("Type a question; follow-ups are read in context. 'key <RGK...>' arms an approved access key,")
    print("'btw <question>' asks about a run in progress, 'whoami' shows your access, 'login' switches user, 'quit' exits.\n")
    current = {"run": None}

    def worker(text: str) -> None:
        display = _make_display(orch, text) if show_thinking else None
        on_ev = display.on_event if display else (_print_events if args.verbose else None)
        rs = orch.start(UserRequest(text=text, session_id=args.session, auth_token=auth["token"],
                                    access_key=auth.pop("key", None)), on_event=on_ev)
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
        if line.lower().startswith("key "):
            auth["key"] = line[4:].strip()
            print("  access key armed; it applies to the next question only.\n")
            continue
        if line.lower() in ("whoami", "access"):
            who = orch.principal(auth["token"])
            d = orch.access_for(auth["token"])[1]
            print(f"  {who.describe()} — reads {', '.join(who.tags) or 'nothing'}")
            print(f"  documents: {', '.join(d.allowed) or 'none'}" + (f"; withheld: {', '.join(d.denied)}" if d.denied else "") + "\n")
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


def _article(word: str) -> str:
    """"an Administrator", "a Manager" — small thing, but it is in every refusal message."""
    return ("an " if word[:1].upper() in "AEIOU" else "a ") + word


def _orch(effort=None, warm=False):
    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator

    return Orchestrator(load_config(effort), warm_start=warm)


def _token(orch):
    from workbench.app.credentials import load_token

    return load_token(orch.cfg)


def cmd_setup_security(args) -> None:
    """Create the three role accounts and tag every loaded document."""
    from workbench.security.setup import setup_security

    orch = _orch()
    print("Refinery workbench — security setup\n")
    setup_security(orch.cfg, documents=orch.knowledge.documents(),
                   reset_passwords=args.reset_passwords or args.random_passwords,
                   random_passwords=args.random_passwords)
    print("\nDone. Sign in with `python -m workbench login`.")
    orch.shutdown()


def cmd_security(args) -> None:
    """The whole security posture: the role schema, the document tags, and your place in it."""
    from workbench.security.roles import title

    orch = _orch()
    overview = orch.security_overview(_token(orch))
    if args.json:
        print(json.dumps(overview, indent=2))
        orch.shutdown()
        return
    if not overview["enabled"]:
        print("Access control is OFF (RWB_AUTH=off). Every document is readable by everyone.\n")
    print("Roles")
    print("-----")
    for r in overview["schema"]["roles"]:
        reads = ", ".join(r["readable_tags"]) or "nothing"
        up = f"  → escalates to {r['escalates_to']}" if r["escalates_to"] else ""
        print(f"  {r['role']:<8} level {r['level']}   reads {reads:<35}{up}")
    print(f"\n  Rule: {overview['schema']['rule']}")
    print(f"  An unclassified document defaults to {overview['schema']['fallback_tag']} — readable by nobody but admin.\n")
    print("Documents")
    print("---------")
    for d in overview["documents"]:
        # the document id is what an engineer refers to; the parsed title is often a heading
        print(f"  {d['document_id'][:44]:<44} {d['tag']:<13} {title(d['min_role'])} and above")
        print(f"      {d['reason']}")
    you = overview["you"]
    print(f"\nYou: {you['principal']} ({you['role']}, level {you['level']})")
    print(f"  may read : {', '.join(you['readable_documents']) or 'nothing'}")
    print(f"  withheld : {', '.join(you['withheld_documents']) or 'nothing'}")
    esc = overview["escalation"]
    if esc["your_approver"]:
        print(f"  escalate : ask {_article(title(esc['your_approver']))}; an approved key lasts "
              f"{esc['key_lifetime_minutes']} min and works {esc['uses_per_key']} time(s)")
    orch.shutdown()


def cmd_classify(args) -> None:
    """Reassign a document's tag (administrators only)."""
    from workbench.security.roles import Role

    orch = _orch()
    principal = orch.principal(_token(orch))
    if orch.cfg.security.enabled and principal.role is not Role.ADMIN:
        print(f"  Only an administrator may reclassify a document; you are {principal.role.value}.")
        orch.shutdown()
        return
    if not args.document:
        for d in orch.document_catalogue():
            print(f"  {d['document_id'][:46]:<46} {d['tag']:<13} {d['min_role']} and above")
        orch.shutdown()
        return
    dc = orch.classifications.assign(args.document, args.tag, args.reason or "reassigned at the console",
                                     by=principal.username or "administrator")
    orch.security_audit.write("classification_changed", principal=principal.username, role=principal.role.value,
                              outcome=f"{args.document} -> {dc.tag.value}", document=args.document)
    print(f"  {args.document} is now {dc.tag.value}; readable by {dc.min_role} and above.")
    orch.shutdown()


def cmd_request_access(args) -> None:
    """Raise an access request for a question your role cannot answer."""
    from workbench.security.escalation import EscalationError
    from workbench.security.roles import title

    orch = _orch()
    try:
        out = orch.request_access(_token(orch), args.text, session_id=args.session)
    except EscalationError as exc:
        print(f"  {exc}")
        orch.shutdown()
        return
    r = out["request"]
    print(f"  Request {r['request_id']} raised to {title(out['approver_role'])}.")
    print(f"  Question : {r['question']}")
    print(f"  Scope    : {out['summary']}")
    print(f"  Next     : {_article(out['approver_role'])} runs `workbench approvals`, then "
          f"`workbench approve {r['request_id']}`.")
    print("  They will send you a one-time key; re-ask this exact question with --key <key>.")
    orch.shutdown()


def cmd_requests(args) -> None:
    """Your own access requests and their state."""
    orch = _orch()
    rows = orch.my_requests(_token(orch))
    if not rows:
        print("  You have raised no access requests.")
    for r in rows:
        key_note = f"  grant {r['grant_id']}" if r.get("grant_id") else ""
        print(f"  {r['request_id']}  {r['status']:<9} {r['question'][:58]}{key_note}")
        if r.get("note"):
            print(f"      note from {r.get('decided_by')}: {r['note']}")
    orch.shutdown()


def cmd_approvals(args) -> None:
    """The approval queue for your role, with the material you are being asked to release."""
    from workbench.security.escalation import EscalationError

    orch = _orch()
    try:
        rows = orch.pending_approvals(_token(orch), include_all=args.all)
    except EscalationError as exc:
        print(f"  {exc}")
        orch.shutdown()
        return
    if not rows:
        print("  Nothing is waiting for your decision.")
    for r in rows:
        print(f"\n  {r['request_id']}  [{r['status']}]  from {r['requester']} ({r['requester_role']})")
        print(f"    asked   : {r['question']}")
        scope = r["scope"]
        print(f"    scope   : {len(scope['record_ids'])} record(s) in {', '.join(scope['document_ids'])}")
        if args.show and r.get("preview"):
            print("    would release:")
            for item in r["preview"]:
                if "note" in item:
                    print(f"      {item['note']}")
                    continue
                print(f"      [{item['kind']}] {item['document']} p.{item['page']}: {item['text'][:150]}")
        elif r.get("preview"):
            print(f"    review it in full with: workbench approvals --show")
        print(f"    decide  : workbench approve {r['request_id']}   |   workbench deny {r['request_id']}")
    orch.shutdown()


def cmd_approve(args) -> None:
    """Approve a request and mint the one-time key."""
    from workbench.security.escalation import EscalationError

    orch = _orch()
    try:
        out = orch.approve_request(_token(orch), args.request_id, note=args.note or "")
    except EscalationError as exc:
        print(f"  {exc}")
        orch.shutdown()
        return
    g = out["grant"]
    print(f"  Approved {out['request']['request_id']}.")
    print(f"  Grant     : {g['grant_id']} — {len(g['record_ids'])} record(s) in {', '.join(g['document_ids'])}")
    print(f"  Valid for : {out['expires_in_minutes']} minute(s), {g['max_uses']} use(s), for {out['requester']} only,")
    print("              and only for the exact question the request was raised against.")
    print(f"\n  KEY (give this to {out['requester']}; it is shown once and cannot be recovered):\n")
    print(f"      {out['key']}\n")
    orch.shutdown()


def cmd_deny(args) -> None:
    from workbench.security.escalation import EscalationError

    orch = _orch()
    try:
        r = orch.deny_request(_token(orch), args.request_id, note=args.note or "")
        print(f"  {r['request_id']} refused." + (f" Note: {r['note']}" if r.get("note") else ""))
    except EscalationError as exc:
        print(f"  {exc}")
    orch.shutdown()


def cmd_revoke_key(args) -> None:
    from workbench.security.escalation import EscalationError
    from workbench.security.roles import Role

    orch = _orch()
    principal = orch.principal(_token(orch))
    if orch.cfg.security.enabled and principal.role is Role.USER:
        print("  Only a manager or an administrator may revoke a grant.")
        orch.shutdown()
        return
    try:
        g = orch.escalations.revoke(args.grant_id, by=principal.username)
        orch.security_audit.write("grant_revoked", principal=principal.username, role=principal.role.value,
                                  outcome="revoked before use", grant_id=g.grant_id)
        print(f"  {g.grant_id} revoked; any key for it is now dead.")
    except EscalationError as exc:
        print(f"  {exc}")
    orch.shutdown()


def cmd_security_check(args) -> None:
    """Run the red-team battery against the documents actually loaded here."""
    import getpass

    from workbench.security.auth import SEED_ACCOUNTS, AuthError
    from workbench.security.redteam import run_red_team

    orch = _orch()
    if not orch.cfg.security.enabled:
        print("Access control is off (RWB_AUTH=off); there is nothing to check.")
        orch.shutdown()
        return
    credentials: dict[str, str] = {}
    for username, _role, default_password, _env in SEED_ACCOUNTS:
        if args.passwords:
            credentials[username] = default_password
        else:
            credentials[username] = getpass.getpass(f"  password for {username}: ")
    try:
        report = run_red_team(orch, credentials=credentials, include_injections=not args.no_injections)
    except AuthError as exc:
        print(f"  {exc}\n  (the check signs in as each role; use --passwords if they are still the seeded ones)")
        orch.shutdown()
        return
    print(report.render())
    if args.verbose_probes:
        for r in report.results:
            mark = "ok  " if r.ok else "LEAK"
            print(f"  {mark} [{r.role:<7}] {r.status:<14} {r.question[:58]:<58} <- {', '.join(r.evidence_documents) or 'no citation'}")
    orch.shutdown()
    if not report.ok:
        sys.exit(1)


def cmd_security_log(args) -> None:
    """The security audit trail: who asked for what, and what was decided."""
    orch = _orch()
    rows = orch.security_audit.read(event=args.event, principal=args.principal, limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            extra = " ".join(f"{k}={v}" for k, v in r.items()
                             if k in ("request_id", "grant_id", "withheld", "documents") and v)
            print(f"  {r['iso']}  {r['event']:<20} {r['principal']:<10} {r['role']:<8} {r.get('outcome', '')[:60]}  {extra}")
        if not rows:
            print("  (no security events recorded yet)")
        print(f"\n  totals: {orch.security_audit.counts()}")
    orch.shutdown()


def cmd_login(args) -> None:
    from workbench.app.credentials import prompt_login
    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(load_config(), warm_start=False)
    if not orch.cfg.security.enabled:
        print("Access control is switched off (RWB_AUTH=off); no sign-in is needed.")
        return
    print("  Documents loaded here, and who may read them:")
    for d in orch.document_catalogue():
        print(f"    {d['document_id'][:46]:<46} {d['tag']:<13} {d['min_role']} and above")
    token = prompt_login(orch, username=args.user)
    if token:
        principal, decision = orch.access_for(token)
        print(f"  role      : {principal.role.value} (reads {', '.join(principal.tags) or 'nothing'})")
        print(f"  may read  : {', '.join(decision.allowed) or 'none'}")
        if decision.denied:
            print(f"  withheld  : {', '.join(decision.denied)}")
            target = decision.escalation_target()
            if target:
                print(f"  escalate  : `workbench request-access \"<question>\"` → reviewed by a {target}")
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
                      "level": principal.level, "readable_tags": principal.tags,
                      "access_control": orch.cfg.security.enabled,
                      "readable": decision.allowed, "withheld": decision.denied,
                      "escalation_target": decision.escalation_target(),
                      "documents": orch.document_catalogue()}, indent=2))
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
    a = sub.add_parser("ask"); a.add_argument("text"); a.add_argument("--session", default="cli"); a.add_argument("--json", action="store_true"); a.add_argument("--effort", choices=list(EFFORT_LEVELS), default=None, help=effort_help); a.add_argument("--no-thinking", action="store_true", help="hide the phase-by-phase thinking and print only the answer"); a.add_argument("--detail", action="store_true", help=detail_help); a.add_argument("--key", default=None, help="an approved access key (RGK...) for this exact question"); a.set_defaults(fn=cmd_ask)
    r = sub.add_parser("repl"); r.add_argument("--session", default="repl"); r.add_argument("--effort", choices=list(EFFORT_LEVELS), default=None, help=effort_help); r.add_argument("--no-thinking", action="store_true", help="hide the phase-by-phase thinking and print only the answer"); r.add_argument("--detail", action="store_true", help=detail_help); r.set_defaults(fn=cmd_repl)
    lg = sub.add_parser("login", help="sign in so the classified documents can be read"); lg.add_argument("--user", default=None); lg.set_defaults(fn=cmd_login)
    sub.add_parser("logout", help="revoke this machine's session token").set_defaults(fn=cmd_logout)
    sub.add_parser("whoami", help="who is signed in and what they may read").set_defaults(fn=cmd_whoami)
    pw = sub.add_parser("passwd", help="change an account's password"); pw.add_argument("--user", default=None); pw.set_defaults(fn=cmd_passwd)
    us = sub.add_parser("users", help="list accounts, or add one with --add"); us.add_argument("--add", default=None); us.add_argument("--role", default="user", choices=["user", "manager", "admin"]); us.add_argument("--name", default=None); us.set_defaults(fn=cmd_users)
    # ---- security -------------------------------------------------------------------------
    ss = sub.add_parser("setup-security", help="create the three role accounts and tag every loaded document")
    ss.add_argument("--reset-passwords", action="store_true", help="reset the role passwords (signs their sessions out)")
    ss.add_argument("--random-passwords", action="store_true", help="use strong random passwords, printed once")
    ss.set_defaults(fn=cmd_setup_security)
    sec = sub.add_parser("security", help="the role schema, the document tags, and your place in it")
    sec.add_argument("--json", action="store_true"); sec.set_defaults(fn=cmd_security)
    cl = sub.add_parser("classify", help="show or reassign document tags (administrators only)")
    cl.add_argument("document", nargs="?", default=None); cl.add_argument("--tag", default="INTERNAL", choices=["INTERNAL", "CONFIDENTIAL", "SECRET"])
    cl.add_argument("--reason", default=None); cl.set_defaults(fn=cmd_classify)
    ra = sub.add_parser("request-access", help="ask a higher role to release the material one question needs")
    ra.add_argument("text"); ra.add_argument("--session", default="cli"); ra.set_defaults(fn=cmd_request_access)
    sub.add_parser("requests", help="your own access requests and their state").set_defaults(fn=cmd_requests)
    ap = sub.add_parser("approvals", help="the approval queue for your role")
    ap.add_argument("--show", action="store_true", help="print the material each request would release")
    ap.add_argument("--all", action="store_true", help="include decided requests"); ap.set_defaults(fn=cmd_approvals)
    apr = sub.add_parser("approve", help="approve a request and mint its one-time key")
    apr.add_argument("request_id"); apr.add_argument("--note", default=None); apr.set_defaults(fn=cmd_approve)
    dn = sub.add_parser("deny", help="refuse an access request")
    dn.add_argument("request_id"); dn.add_argument("--note", default=None); dn.set_defaults(fn=cmd_deny)
    rv = sub.add_parser("revoke-key", help="kill an issued grant before it is used")
    rv.add_argument("grant_id"); rv.set_defaults(fn=cmd_revoke_key)
    sck = sub.add_parser("security-check", help="red-team the loaded documents: probe every role for leakage")
    sck.add_argument("--passwords", action="store_true", help="use the seeded role passwords instead of prompting")
    sck.add_argument("--no-injections", action="store_true", help="skip the prompt-injection probes")
    sck.add_argument("--verbose-probes", action="store_true", help="print every probe, not only the failures")
    sck.set_defaults(fn=cmd_security_check)
    sl = sub.add_parser("security-log", help="the security audit trail")
    sl.add_argument("--event", default=None); sl.add_argument("--principal", default=None)
    sl.add_argument("--limit", type=int, default=50); sl.add_argument("--json", action="store_true")
    sl.set_defaults(fn=cmd_security_log)
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

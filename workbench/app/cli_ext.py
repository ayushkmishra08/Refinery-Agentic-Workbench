"""Extended CLI: models, sovereignty, vault, tools, sandbox, intake, deliverables, drafts, model packages.

  python -m workbench models                       # registry, what is installed, who wins each task kind
  python -m workbench route "read the scan and calculate the margin"
  python -m workbench sovereignty [--deep] [--watch 5]
  python -m workbench audit-verify                 # every chained log, in full
  python -m workbench vault status|seal [--shred]|rotate <role>|revoke <branch> <role>
  python -m workbench tools                        # named local tools
  python -m workbench tool calculate --args '{"expression": "(520-482)/482*100"}'
  python -m workbench agent "calculate (520-482)/482*100 and write the result to margin.txt"
  python -m workbench sandbox-run script.py [--tests tests.py]
  python -m workbench intake "data/sample docs/test.pdf" [--no-vision]
  python -m workbench export <response_id> --format docx|pptx|xlsx
  python -m workbench responses                    # answers on record that can be exported
  python -m workbench drafts [--pending]; draft-resolve <draft> <figure> accepted|corrected|removed [--value V]; draft-signoff <draft>
  python -m workbench packages sign|verify|import|trust ...
  python -m workbench serve --tls [--mtls]         # local TLS / mutual TLS between components
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def _orch(effort: str | None = None, warm: bool = False):
    from workbench.config import load_config
    from workbench.orchestration.orchestrator import Orchestrator

    return Orchestrator(load_config(effort), warm_start=warm)


def _token(orch, interactive: bool = True) -> str | None:
    from workbench.app.credentials import require_sign_in

    return require_sign_in(orch, interactive=interactive and sys.stdin.isatty())


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------- models
def cmd_models(args) -> None:
    o = _orch()
    try:
        import workbench.app.api as api
        from workbench.app.api_ext import list_models

        api._orch = o
        out = list_models()
    finally:
        o.shutdown()
    if args.json:
        _print(out)
        return
    print(f"routing: {'on' if out['routing_enabled'] else 'off'}  profile {out['profile']}  vram {out['vram_mb']} MB  budget {out['budget']}  resident {out['resident'] or '-'}")
    print(f"{'model':22} {'inst':4} {'fits':4} {'GB':>5} {'ctx':>6}  capabilities")
    for m in out["models"]:
        caps = " ".join(f"{k[:5]}={v:.2f}" for k, v in sorted(m["capabilities"].items()))
        print(f"{m['name']:22} {'yes' if m['installed'] else 'no':4} {'yes' if m['fits_vram'] else 'no':4} {m['size_gb']:>5} {m['context']:>6}  {caps}")
    if out["best_per_kind"]:
        print("\nbest installed model per task kind:")
        for k, v in out["best_per_kind"].items():
            print(f"  {k:15} -> {v}")


def cmd_route(args) -> None:
    o = _orch()
    try:
        if o.router is None:
            print("model routing is not active (Ollama down or RWB_ROUTING=off)")
            return
        plan = o.router.plan(args.text, budget=args.budget or o.cfg.models.budget)
    finally:
        o.shutdown()
    print(f"{'hybrid' if plan.hybrid else 'single'} request -> {len(plan.subtasks)} sub-task(s)")
    for sub, dec in zip(plan.subtasks, plan.decisions):
        worker = dec.chosen or "no model"
        if sub.deterministic_tool:
            worker = f"{sub.deterministic_tool} (deterministic)" if sub.kind == "calculation" else f"{worker} + {sub.deterministic_tool}"
        print(f"  {sub.kind:14} {sub.description:60} -> {worker}")
        for c in dec.candidates[:4]:
            print(f"      {c.name:20} score {c.score:.2f} cap {c.capability:.2f} {'installed' if c.installed else 'not installed'} {c.note}")
        print(f"      reason: {dec.reason}")


# --------------------------------------------------------------------------- sovereignty
def cmd_sovereignty(args) -> None:
    o = _orch()
    o.start_sovereignty()
    try:
        if args.watch:
            while True:
                time.sleep(args.watch)
                st = o.sovereignty.monitor.status()
                print(f"{time.strftime('%H:%M:%S')}  samples {st['samples']}  uplinks {st['uplinks_up']}/{st['uplinks_total']}  "
                      f"external host={st['external_connections']} workbench={st['workbench_external_connections']}  "
                      f"{'DISCONNECTED' if st['physically_disconnected'] else ''}  chain {'ok' if st['chain']['ok'] else 'BROKEN'}")
        time.sleep(1.5)
        rep = o.sovereignty.report()
        if args.json:
            _print(rep)
            return
        print("VERDICT:", rep["verdict"])
        g = rep["egress_guard"]
        print(f"egress guard: {'installed' if g.get('installed') else 'OFF'}; allowed {g.get('allowed_hosts')}; blocked so far {g.get('blocked_count', 0)}")
        m = rep["network_monitor"]
        print(f"network monitor: running={m.get('running')} samples={m.get('samples')} connections={m.get('connections_seen')} "
              f"external(host)={m.get('external_connections')} external(workbench)={m.get('workbench_external_connections')} "
              f"uplinks up {m.get('uplinks_up')}/{m.get('uplinks_total')}")
        for i in m.get("interfaces", []):
            print(f"   {'UP  ' if i['isup'] else 'DOWN'} {i['name'][:28]:28} {', '.join(i['addresses'])}")
        print("chained logs:")
        for c in rep["chains"]:
            print(f"   {'ok ' if c['ok'] else 'BAD'} {c['log']:36} {c['entries']:>7} entries  {c['detail']}")
    except KeyboardInterrupt:
        pass
    finally:
        o.shutdown()


def cmd_audit_verify(args) -> None:
    o = _orch()
    try:
        rows = o.sovereignty.verify_all()
    finally:
        o.shutdown()
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        print(f"{'ok ' if r['ok'] else 'BAD'} {r['log']:36} {r['entries']:>7}  {r['detail']}")
    print("\nALL CHAINS INTACT" if not bad else f"\n{len(bad)} CHAIN(S) TAMPERED")
    sys.exit(0 if not bad else 2)


# --------------------------------------------------------------------------- vault
def cmd_vault(args) -> None:
    from workbench.security.vault import seal_index_cache, vault_status

    o = _orch()
    try:
        if o.kms is None:
            print("vault unavailable (cryptography missing?)")
            return
        if args.action == "status":
            out = vault_status(o.cfg, o.kms, o.vault_store)
            out["enabled"] = o.cfg.vault.enabled
            out["in_memory"] = o.vault.status() if o.vault is not None else None
            _print(out)
        elif args.action == "seal":
            token = _token(o)
            principal = o.principal(token)
            if o.cfg.security.enabled and principal.role.value != "admin":
                print("sealing needs an administrator")
                return
            sealed = seal_index_cache(o.cfg, o.kms, o.vault_store, o.classifications, shred=args.shred)
            for info in sealed:
                o.security_audit.write("branch_sealed", principal=principal.username, role=principal.role.value,
                                       outcome=f"{info.bytes_plain} bytes for {', '.join(info.roles)}", branch=info.branch)
                print(f"sealed {info.branch:45} {info.bytes_plain:>10} -> {info.bytes_cipher:>10} bytes  roles {', '.join(info.roles)}")
            print(f"\n{len(sealed)} branch(es) sealed under {o.vault_store.dir}" + ("; plaintext caches shredded" if args.shred else ""))
            print("start with RWB_VAULT=on to serve from the vault (branches decrypt per session).")
        elif args.action == "rotate":
            token = _token(o)
            principal = o.principal(token)
            ver = o.kms.rotate_role_key(args.role)
            o.keyrings.wipe_all()
            o.security_audit.write("key_rotated", principal=principal.username, role=principal.role.value,
                                   outcome=f"{args.role} wrapping key now v{ver.version}", target_role=args.role)
            print(f"{args.role} wrapping key rotated to version {ver.version}; every branch key it held was re-wrapped; old copies are dead.")
        elif args.action == "revoke":
            token = _token(o)
            principal = o.principal(token)
            o.kms.revoke_role(args.branch, args.role)
            o.keyrings.wipe_all()
            o.security_audit.write("key_revoked", principal=principal.username, role=principal.role.value,
                                   outcome=f"{args.role} can no longer unwrap {args.branch}", branch=args.branch, target_role=args.role)
            print(f"{args.role} no longer holds a key for {args.branch}; roles with a key: {o.kms.wrapped_roles(args.branch)}")
    finally:
        o.shutdown()


# --------------------------------------------------------------------------- tools / agent / sandbox
def cmd_tools(args) -> None:
    o = _orch()
    try:
        for t in o.tools.describe():
            print(f"{t['name']:18} {t['description']}")
            props = (t.get("parameters") or {}).get("properties", {})
            if props:
                print("                   args: " + ", ".join(f"{k}: {v.get('type', '?')}" for k, v in props.items()))
        sb = o.sandbox
        print(f"\nsandbox: {'enabled' if sb else 'disabled'}" + (f" backend={sb.backend} limits={sb.limits.model_dump()}" if sb else ""))
    finally:
        o.shutdown()


def cmd_tool(args) -> None:
    o = _orch()
    try:
        token = _token(o)
        tool = o.tools.get(args.name)
        if tool is None:
            print(f"no tool called {args.name!r}; run 'python -m workbench tools'")
            return
        principal, session_key, knowledge = o.guarded_knowledge_for(token, args.session)
        ctx = o.tool_context(session_key, principal, knowledge)
        res = tool.run(json.loads(args.args) if args.args else {}, ctx)
        if args.json:
            _print(res.model_dump(mode="json"))
            return
        print(f"[{args.name}] ok={res.ok} {res.duration_ms} ms" + (f"  error: {res.error}" if res.error else ""))
        print(res.output)
        for s in res.steps:
            print("  step:", s)
        for f in res.files:
            print("  file:", f)
        for e in res.evidence[:6]:
            print(f"  evidence: {e.get('document_id')} p.{e.get('page')}: {str(e.get('text', ''))[:120]}")
    finally:
        o.shutdown()


def cmd_agent(args) -> None:
    from workbench.tools.agent_loop import ToolAgent

    o = _orch(args.effort)
    try:
        token = _token(o)
        principal, session_key, knowledge = o.guarded_knowledge_for(token, args.session)
        ctx = o.tool_context(session_key, principal, knowledge)
        report = ToolAgent(o.tools, ctx, o.llm, max_iterations=args.max_iterations).run(args.goal)
        if args.json:
            _print(report.model_dump(mode="json"))
            return
        print(f"goal: {report.goal}\nplanner: {'model' if o.llm.available() else 'deterministic'}")
        for row in report.iterations:
            it = row.model_dump() if hasattr(row, "model_dump") else dict(row)
            print(f"  {it.get('n')}. {it.get('tool')}({json.dumps(it.get('args'), default=str)[:90]}) -> {'ok' if it.get('ok') else 'FAILED'} {it.get('duration_ms')} ms")
            if it.get("output_preview"):
                print("     " + str(it["output_preview"])[:200].replace("\n", " | "))
        print(f"\nresult ({'ok' if report.ok else 'not ok'}): {report.final_output}")
        if report.files:
            print("files:", ", ".join(report.files), f"(workspace {ctx.workspace})")
        if report.steps:
            print("steps:")
            for s in report.steps:
                print("  ", s)
    finally:
        o.shutdown()


def cmd_sandbox_run(args) -> None:
    o = _orch()
    try:
        code = Path(args.file).read_text(encoding="utf-8")
        tests = Path(args.tests).read_text(encoding="utf-8") if args.tests else None
        res = o.sandbox.run(code, tests=tests, task_id=f"cli:{Path(args.file).name}")
        if args.json:
            _print(res.model_dump(mode="json"))
            return
        print(f"run {res.run_id} backend={res.backend} exit={res.exit_code} ok={res.ok} verified={res.verified} "
              f"limit={res.limit_hit or 'none'} {res.duration_ms} ms peak {res.peak_rss_mb} MB cpu {res.cpu_seconds}s")
        if res.stdout:
            print("--- stdout ---\n" + res.stdout)
        if res.stderr:
            print("--- stderr ---\n" + res.stderr)
        if res.static_analysis:
            sa = res.static_analysis
            print(f"static analysis: ok={sa.get('ok')} banned_imports={sa.get('banned_imports')} banned_calls={sa.get('banned_calls')} pyflakes={len(sa.get('pyflakes') or [])}")
        if res.verification:
            print(f"tests: {res.verification.get('tests_passed')}/{res.verification.get('tests_total')} passed")
        if res.files_written:
            print("files written:", [f["path"] for f in res.files_written])
        if res.egress_attempts:
            print("EGRESS ATTEMPTS BLOCKED:", res.egress_attempts)
        print(f"workdir destroyed: {res.workdir_destroyed}; run-log entry {res.log_hash}")
    finally:
        o.shutdown()


# --------------------------------------------------------------------------- intake
def cmd_intake(args) -> None:
    from workbench.intake.pipeline import intake_file

    o = _orch()
    try:
        res = intake_file(Path(args.file), llm_or_resources=(o.resources if (o.llm.available() and not args.no_vision) else None),
                          threshold=o.cfg.review.ocr_confidence_threshold, run_vision=not args.no_vision,
                          max_pages=args.max_pages, purpose=args.purpose, max_vision_calls=args.vision_calls)
        if args.json:
            _print(res.model_dump(mode="json"))
            return
        print(f"{res.source}: kind={res.kind} pages={res.pages} {res.duration_ms} ms")
        print("summary:", json.dumps(res.summary, default=str))
        if res.ocr:
            print(f"OCR: {len(res.ocr.lines)} lines, mean confidence {res.ocr.mean_confidence:.2f}, {res.ocr.low_confidence_lines} below threshold")
        for v in res.vision:
            print(f"vision ({v.purpose}, {v.model}, {v.duration_ms} ms): {v.text[:400]}")
            if v.tags_found:
                print("   tags:", v.tags_found)
        if res.flagged:
            print(f"\n{len(res.flagged)} line(s) flagged for human review:")
            for f in res.flagged[:15]:
                print(f"   p.{f.get('page')} conf {f.get('confidence')}: {str(f.get('text'))[:90]}  ({f.get('reason')})")
        print("\ntext excerpt:\n" + (res.text or "")[:800])
    finally:
        o.shutdown()


# --------------------------------------------------------------------------- deliverables / drafts
def cmd_responses(args) -> None:
    o = _orch()
    try:
        token = _token(o)
        principal = o.principal(token)
        rows = o.responses.recent(owner=None if principal.role.value == "admin" else principal.username, limit=args.limit)
        for r in rows:
            print(f"{r['response_id']:34} {r['task_type']:15} {r['status']:12} ev={r['evidence']:<3} {r['question'][:70]}")
        if not rows:
            print("no answers on record yet; ask something first")
    finally:
        o.shutdown()


def cmd_export(args) -> None:
    from workbench.deliverables.export import export_response

    o = _orch()
    try:
        token = _token(o)
        principal = o.principal(token)
        resp = o.responses.load_for(args.response_id, principal)
        draft = o.drafts.create(args.format, args.title or f"{resp.task_type.value}: {resp.answer_markdown[:60]}", response=resp,
                                session_id=resp.session_id, owner=principal.username, owner_role=principal.role.value,
                                classification=resp.security.classification,
                                ocr_threshold=o.cfg.review.ocr_confidence_threshold, flag_llm_derived=o.cfg.review.flag_llm_derived)
        out_dir = Path(args.out or (Path(o.cfg.review.deliverables_dir) / principal.username))
        result = export_response(resp, args.format, out_dir, title=args.title, figures=draft.figures)
        draft = o.drafts.attach_file(draft.draft_id, Path(result.path))
        o.security_audit.write("deliverable_exported", principal=principal.username, role=principal.role.value,
                               outcome=f"{args.format} from {args.response_id}", draft_id=draft.draft_id)
        print(f"written {result.path} ({result.bytes} bytes, sha256 {result.sha256[:16]}...)")
        print(f"draft {draft.draft_id}: status {draft.status}; {len(draft.figures)} figure(s), {len(draft.open_flags)} flagged for review")
        print(result.status_line)
    finally:
        o.shutdown()


def cmd_drafts(args) -> None:
    o = _orch()
    try:
        token = _token(o)
        principal = o.principal(token)
        owner = None if principal.role.value in ("manager", "admin") else principal.username
        rows = o.drafts.list(owner=owner, status="pending_signoff" if args.pending else None)
        for d in rows:
            print(f"{d.draft_id:22} {d.status:16} {d.kind:5} flags {len(d.open_flags):>2}/{len(d.figures):<3} {d.owner:10} {d.title[:60]}")
            if args.verbose:
                for f in d.figures:
                    mark = "FLAG" if (f.flagged and not f.resolution) else ("res " if f.resolution else "    ")
                    print(f"    {mark} {f.figure_id:10} {f.label[:30]:30} = {f.value} {f.unit or ''}  conf {f.confidence:.2f}  {f.source.get('document_id', '')} p.{f.source.get('page')}")
        if not rows:
            print("no drafts")
        print("\n", json.dumps(o.drafts.summary()))
    finally:
        o.shutdown()


def cmd_draft_resolve(args) -> None:
    o = _orch()
    try:
        token = _token(o)
        principal = o.principal(token)
        d = o.drafts.resolve_figure(args.draft_id, args.figure_id, by=principal.username, action=args.action,
                                    corrected_value=args.value, note=args.note or "")
        print(f"{args.figure_id} {args.action}; open flags now {len(d.open_flags)}; status {d.status}")
    finally:
        o.shutdown()


def cmd_draft_signoff(args) -> None:
    from workbench.review.drafts import SignoffBlocked

    o = _orch()
    try:
        token = _token(o)
        principal = o.principal(token)
        try:
            if args.reject:
                d = o.drafts.reject(args.draft_id, by=principal.username, role=principal.role.value, note=args.note or "")
            else:
                d = o.drafts.sign_off(args.draft_id, by=principal.username, role=principal.role.value, note=args.note or "")
        except SignoffBlocked as exc:
            o.security_audit.write("signoff_blocked", principal=principal.username, role=principal.role.value,
                                   outcome=f"{len(exc.open_flags)} open flag(s)", draft_id=args.draft_id)
            print(f"SIGN-OFF BLOCKED: {len(exc.open_flags)} flagged figure(s) are unresolved:")
            for f in exc.open_flags:
                print(f"   {f.figure_id}: {f.label} = {f.value} {f.unit or ''} ({f.flag_reason})")
            sys.exit(2)
        o.security_audit.write("draft_rejected" if args.reject else "draft_signed_off", principal=principal.username,
                               role=principal.role.value, outcome=args.note or "", draft_id=args.draft_id)
        print(f"draft {d.draft_id} is now {d.status}")
    finally:
        o.shutdown()


# --------------------------------------------------------------------------- signed model packages
def cmd_packages(args) -> None:
    from workbench.sovereignty import model_updates as mu

    o = _orch()
    try:
        trust = o.cfg.paths.security_dir / "trusted_signers"
        if args.action == "keygen":
            keys = mu.create_signer(o.cfg.paths.security_dir / "signers", args.key_id)
            print(f"signer {keys.key_id}: private {keys.private_key}  public {keys.public_key}")
        elif args.action == "trust":
            print("trusted:", mu.trust_signer(trust, Path(args.public_key), args.key_id))
        elif args.action == "sign":
            print("manifest:", mu.sign_package(Path(args.package), Path(args.private_key), args.key_id, args.name, args.version))
        elif args.action == "verify":
            rep = mu.verify_package(Path(args.package), trust)
            _print(rep.model_dump(mode="json"))
            sys.exit(0 if rep.ok else 2)
        elif args.action == "import":
            out = mu.import_package(Path(args.package), trust, ollama_url=o.cfg.llm.base_url, dry_run=args.dry_run,
                                    log_path=o.cfg.paths.security_dir / "model_updates.jsonl")
            _print(out)
        elif args.action == "log":
            _print(mu.update_log(o.cfg.paths.security_dir / "model_updates.jsonl"))
            print("trusted signers:", mu.trusted_signers(trust))
    finally:
        o.shutdown()


# --------------------------------------------------------------------------- registration
def register(sub, effort_choices) -> None:
    m = sub.add_parser("models", help="model registry: capabilities, what is installed, who wins each task kind"); m.add_argument("--json", action="store_true"); m.set_defaults(fn=cmd_models)
    r = sub.add_parser("route", help="show how a request would be decomposed and routed (no model call)"); r.add_argument("text"); r.add_argument("--budget", default=None, choices=["normal", "fast"]); r.set_defaults(fn=cmd_route)
    sv = sub.add_parser("sovereignty", help="air-gap proof: egress guard, network monitor, chained-log integrity"); sv.add_argument("--deep", action="store_true"); sv.add_argument("--watch", type=float, default=0); sv.add_argument("--json", action="store_true"); sv.set_defaults(fn=cmd_sovereignty)
    sub.add_parser("audit-verify", help="verify every hash-chained log in full").set_defaults(fn=cmd_audit_verify)
    v = sub.add_parser("vault", help="envelope encryption of the knowledge branches")
    vs = v.add_subparsers(dest="action", required=True)
    vs.add_parser("status").set_defaults(fn=cmd_vault)
    vseal = vs.add_parser("seal"); vseal.add_argument("--shred", action="store_true", help="overwrite + delete the plaintext index caches"); vseal.set_defaults(fn=cmd_vault)
    vrot = vs.add_parser("rotate"); vrot.add_argument("role", choices=["user", "manager", "admin"]); vrot.set_defaults(fn=cmd_vault)
    vrev = vs.add_parser("revoke"); vrev.add_argument("branch"); vrev.add_argument("role", choices=["user", "manager", "admin"]); vrev.set_defaults(fn=cmd_vault)
    sub.add_parser("tools", help="the named local tools").set_defaults(fn=cmd_tools)
    t = sub.add_parser("tool", help="run one tool"); t.add_argument("name"); t.add_argument("--args", default=""); t.add_argument("--session", default="cli"); t.add_argument("--json", action="store_true"); t.set_defaults(fn=cmd_tool)
    ag = sub.add_parser("agent", help="agentic execution over the local tools"); ag.add_argument("goal"); ag.add_argument("--session", default="cli"); ag.add_argument("--effort", choices=effort_choices, default=None); ag.add_argument("--max-iterations", type=int, default=8); ag.add_argument("--json", action="store_true"); ag.set_defaults(fn=cmd_agent)
    sr = sub.add_parser("sandbox-run", help="run a Python file in the sandbox"); sr.add_argument("file"); sr.add_argument("--tests", default=None); sr.add_argument("--json", action="store_true"); sr.set_defaults(fn=cmd_sandbox_run)
    it = sub.add_parser("intake", help="on-device OCR + vision over a PDF/image"); it.add_argument("file"); it.add_argument("--no-vision", action="store_true"); it.add_argument("--purpose", default="general", choices=["general", "pid", "handwriting", "photo", "gauge"]); it.add_argument("--max-pages", type=int, default=6); it.add_argument("--vision-calls", type=int, default=2); it.add_argument("--json", action="store_true"); it.set_defaults(fn=cmd_intake)
    rs = sub.add_parser("responses", help="released answers that can be exported"); rs.add_argument("--limit", type=int, default=30); rs.set_defaults(fn=cmd_responses)
    ex = sub.add_parser("export", help="export a released answer as a Word/PowerPoint/Excel draft"); ex.add_argument("response_id"); ex.add_argument("--format", default="docx", choices=["docx", "pptx", "xlsx", "md"]); ex.add_argument("--title", default=None); ex.add_argument("--out", default=None); ex.set_defaults(fn=cmd_export)
    dr = sub.add_parser("drafts", help="drafts and their sign-off state"); dr.add_argument("--pending", action="store_true"); dr.add_argument("-v", "--verbose", action="store_true"); dr.set_defaults(fn=cmd_drafts)
    dres = sub.add_parser("draft-resolve", help="resolve one flagged figure"); dres.add_argument("draft_id"); dres.add_argument("figure_id"); dres.add_argument("action", choices=["accepted", "corrected", "removed"]); dres.add_argument("--value", default=None); dres.add_argument("--note", default=None); dres.set_defaults(fn=cmd_draft_resolve)
    dso = sub.add_parser("draft-signoff", help="sign a draft off (blocked while flags are open)"); dso.add_argument("draft_id"); dso.add_argument("--reject", action="store_true"); dso.add_argument("--note", default=None); dso.set_defaults(fn=cmd_draft_signoff)
    pk = sub.add_parser("packages", help="signed, checksum-verified model packages")
    ps = pk.add_subparsers(dest="action", required=True)
    kg = ps.add_parser("keygen"); kg.add_argument("key_id"); kg.set_defaults(fn=cmd_packages)
    tr = ps.add_parser("trust"); tr.add_argument("public_key"); tr.add_argument("key_id"); tr.set_defaults(fn=cmd_packages)
    sg = ps.add_parser("sign"); sg.add_argument("package"); sg.add_argument("--private-key", required=True); sg.add_argument("--key-id", required=True); sg.add_argument("--name", required=True); sg.add_argument("--version", required=True); sg.set_defaults(fn=cmd_packages)
    vf = ps.add_parser("verify"); vf.add_argument("package"); vf.set_defaults(fn=cmd_packages)
    im = ps.add_parser("import"); im.add_argument("package"); im.add_argument("--dry-run", action="store_true"); im.set_defaults(fn=cmd_packages)
    ps.add_parser("log").set_defaults(fn=cmd_packages)

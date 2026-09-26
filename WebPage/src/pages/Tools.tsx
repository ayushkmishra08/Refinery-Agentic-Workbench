/**
 * The named local tools, the agent loop over them, and the sandbox.
 *
 * Everything runs in the caller's own session workspace, over the knowledge the caller may
 * read, and every call lands in a hash-chained tool log. Code runs in a fresh sandbox with no
 * network interface, bounded CPU, memory, time and disk, and is destroyed afterwards; "verified"
 * means the task's own tests passed and static analysis found nothing, not that it did not crash.
 */
import { Bot, Download, FileCode2, FolderOpen, Play, ScrollText, Wrench } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ChainBadge, KeyValues, Mono, OkBadge, bytes } from "@/components/Bits";
import { ApiError, api, download, saveBlob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ms, timeOfDay } from "@/lib/format";
import type { SandboxInfo, SandboxManifest, SandboxResult, ToolCallEntry, ToolDescription, ToolEvidence, ToolResult, ToolRunReport, WorkspaceFile } from "@/lib/types_ext";
import { Badge, Button, EmptyState, ErrorState, Field, PageHeader, Panel, PanelHeader, Skeleton, Tabs, Textarea, useToast } from "@/ui";

type Tab = "tools" | "agent" | "sandbox" | "workspace" | "log";
const SESSION = "tools";

const SAMPLE_ARGS: Record<string, Record<string, unknown>> = {
  calculate: { expression: "(520 - 482) / 482 * 100", precision: 2 },
  search_documents: { query: "crude charge pump normal flow", k: 5, kind: "claims" },
  write_file: { path: "notes/margin.txt", content: "Margin above normal: 7.88 %" },
  read_file: { path: "notes/margin.txt" },
  list_files: {},
  run_python: { code: "total = sum(range(1, 11))\nprint('sum 1..10 =', total)\n", tests: "def test_total():\n    assert sum(range(1, 11)) == 55\n" },
  spreadsheet_write: { path: "calc.xlsx", sheet: "Envelope", rows: [["parameter", "value", "unit"], ["normal flow", 482, "m3/h"], ["design flow", 520, "m3/h"], ["margin %", "=(B3-B2)/B2*100", "%"]] },
  spreadsheet_read: { path: "calc.xlsx" },
  ocr_image: { path: "scan.png" },
  describe_image: { path: "pid.png", purpose: "pid" },
  make_docx: { title: "Pump margin note", content: "# Crude charge pump\n\nNormal flow 482 m3/h, design 520 m3/h: margin 7.88 %." },
  make_xlsx: { title: "Pump margin", content: "Normal 482, design 520 m3/h." },
  make_pptx: { title: "Pump margin", content: "Normal 482, design 520 m3/h." },
};

export default function Tools() {
  const [tab, setTab] = useState<Tab>("tools");
  const [tools, setTools] = useState<ToolDescription[] | null>(null);
  const [sandbox, setSandbox] = useState<SandboxInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const out = await api.tools();
      setTools(out.tools);
      setSandbox(out.sandbox);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!tools) return <Skeleton className="h-64 w-full" />;

  return (
    <div>
      <PageHeader
        title="Tools"
        description={`${tools.length} named local tools: files, sandboxed code, spreadsheets, document search, calculation, OCR, vision, Word/Excel/PowerPoint. Each call is confined to your session workspace, sees only the documents you may read, and is written to a chained log.`}
        actions={sandbox?.enabled ? <Badge tone="success">sandbox {sandbox.backend}</Badge> : <Badge tone="warning">sandbox off</Badge>}
      />
      <Tabs
        className="mb-4 max-w-2xl"
        value={tab}
        onChange={setTab}
        tabs={[
          { id: "tools", label: <><Wrench className="size-3.5" /> Tools</> },
          { id: "agent", label: <><Bot className="size-3.5" /> Agent</> },
          { id: "sandbox", label: <><FileCode2 className="size-3.5" /> Sandbox</> },
          { id: "workspace", label: <><FolderOpen className="size-3.5" /> Workspace</> },
          { id: "log", label: <><ScrollText className="size-3.5" /> Tool log</> },
        ]}
      />
      {tab === "tools" ? <ToolRunner tools={tools} /> : null}
      {tab === "agent" ? <AgentRunner /> : null}
      {tab === "sandbox" ? <SandboxPanel info={sandbox} /> : null}
      {tab === "workspace" ? <Workspace /> : null}
      {tab === "log" ? <ToolLog /> : null}
    </div>
  );
}

// ---------------------------------------------------------------- one tool

function ToolRunner({ tools }: { tools: ToolDescription[] }) {
  const [selected, setSelected] = useState(tools[0]?.name ?? "");
  const [args, setArgs] = useState(() => JSON.stringify(SAMPLE_ARGS[tools[0]?.name] ?? {}, null, 2));
  const [result, setResult] = useState<ToolResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tool = tools.find((t) => t.name === selected);

  const pick = (name: string) => {
    setSelected(name);
    setArgs(JSON.stringify(SAMPLE_ARGS[name] ?? {}, null, 2));
    setResult(null);
    setError(null);
  };

  const run = async () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(args || "{}");
    } catch {
      setError("Arguments must be a JSON object.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await api.runTool(selected, parsed, SESSION));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <Panel>
        <PanelHeader title="Named tools" />
        <ul className="divide-y divide-border/60">
          {tools.map((t) => (
            <li key={t.name}>
              <button type="button" onClick={() => pick(t.name)} aria-pressed={selected === t.name}
                      className={cn("w-full px-3 py-2 text-left transition-colors hover:bg-slate-900/[0.04]", selected === t.name && "bg-primary/[0.08]")}>
                <p className="font-mono text-xs font-semibold text-foreground">{t.name}</p>
                <p className="line-clamp-2 text-[0.68rem] text-muted-foreground">{t.description}</p>
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      <div className="space-y-4">
        <Panel>
          <PanelHeader title={tool?.name ?? "—"} description={tool?.description} />
          <div className="space-y-2 px-4 py-3">
            {tool?.parameters.properties ? (
              <p className="text-[0.7rem] text-muted-foreground">
                Arguments: {Object.entries(tool.parameters.properties).map(([k, v]) => `${k}${tool.parameters.required?.includes(k) ? "*" : ""}: ${v.type ?? "any"}`).join(", ")}
              </p>
            ) : null}
            <Textarea rows={7} value={args} onChange={(e) => setArgs(e.target.value)} className="font-mono text-xs" aria-label="Tool arguments (JSON)" spellCheck={false} />
            <div className="flex items-center gap-2">
              <Button size="sm" variant="primary" loading={busy} onClick={() => void run()}><Play className="size-3.5" /> Run {selected}</Button>
              <span className="text-[0.68rem] text-muted-foreground">Session workspace: {SESSION}</span>
            </div>
            {error ? <p role="alert" className="text-xs text-[var(--danger)]">{error}</p> : null}
          </div>
        </Panel>
        {result ? <ToolResultView result={result} /> : null}
      </div>
    </div>
  );
}

function ToolResultView({ result }: { result: ToolResult }) {
  return (
    <Panel>
      <PanelHeader title={`Result · ${result.tool}`} actions={<><OkBadge ok={result.ok} /><span className="text-[0.68rem] text-muted-foreground">{ms(result.duration_ms)}</span></>} />
      <div className="space-y-3 px-4 py-3">
        {result.error ? <p role="alert" className="text-xs text-[var(--danger)]">{result.error}</p> : null}
        <Mono>{result.output || "(no output)"}</Mono>
        {result.steps.length ? (
          <div>
            <p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">Steps shown</p>
            <ol className="space-y-0.5 font-mono text-[0.7rem] text-slate-700">{result.steps.map((s, i) => <li key={i}>{i + 1}. {s}</li>)}</ol>
          </div>
        ) : null}
        <EvidenceRows items={result.evidence} />
        <FileLinks files={result.files} />
      </div>
    </Panel>
  );
}

function EvidenceRows({ items }: { items: ToolEvidence[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">Evidence ({items.length})</p>
      <ul className="space-y-1 text-[0.7rem]">
        {items.slice(0, 12).map((e, i) => (
          <li key={i} className="rounded-md border border-border/60 bg-white/60 px-2 py-1">
            <span className="font-medium text-foreground">{e.document_id ?? "—"}</span>
            {e.page != null ? <span className="text-muted-foreground"> p.{e.page}</span> : null}
            {e.confidence != null ? <Badge tone={e.confidence >= 0.6 ? "success" : "warning"} className="ml-1">{e.confidence.toFixed(2)}</Badge> : null}
            <p className="text-slate-600">{(e.text ?? "").slice(0, 220)}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FileLinks({ files }: { files: string[] }) {
  const toast = useToast();
  if (!files?.length) return null;
  const get = async (path: string) => {
    try {
      saveBlob(await download(api.workspaceFilePath(SESSION, path)), path.split("/").pop() || path);
    } catch (err) {
      toast.push({ tone: "danger", message: "Download failed", detail: err instanceof ApiError ? err.message : String(err) });
    }
  };
  return (
    <div className="flex flex-wrap gap-1.5">
      {files.map((f) => (
        <Button key={f} size="sm" variant="outline" onClick={() => void get(f)}><Download className="size-3" /> {f}</Button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- agent loop

function AgentRunner() {
  const [goal, setGoal] = useState("calculate (520-482)/482*100");
  const [report, setReport] = useState<ToolRunReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await api.runAgent(goal, SESSION));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Panel>
        <PanelHeader title="Agentic execution" description="The agent plans, calls real local tools, reads the result and iterates — with a model when one is available, deterministically otherwise. Every call is chained." />
        <div className="space-y-2 px-4 py-3">
          <Textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} aria-label="Goal" placeholder="e.g. search for crude charge pump normal flow in the documents" />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" loading={busy} disabled={!goal.trim()} onClick={() => void run()}><Bot className="size-3.5" /> Run agent</Button>
            {["calculate (520-482)/482*100", "search for desalter wash water rate in the documents", "write margin 7.88 % to notes/margin.txt", "list files"].map((s) => (
              <button key={s} type="button" onClick={() => setGoal(s)} className="rounded-md border border-border/70 bg-white/60 px-2 py-1 text-[0.68rem] text-slate-700 hover:border-primary/40">{s}</button>
            ))}
          </div>
          {error ? <p role="alert" className="text-xs text-[var(--danger)]">{error}</p> : null}
        </div>
      </Panel>

      {report ? (
        <Panel>
          <PanelHeader
            title={report.goal}
            description={`${report.mode} planning · ${report.iterations.length} iteration${report.iterations.length === 1 ? "" : "s"} · ${report.llm_calls} model call${report.llm_calls === 1 ? "" : "s"} · ${ms(report.duration_ms)}`}
            actions={<OkBadge ok={report.ok} yes="done" no="not done" />}
          />
          <div className="space-y-3 px-4 py-3">
            <ol className="relative space-y-2 border-l border-border/70 pl-4">
              {report.iterations.map((it) => (
                <li key={it.n} className="relative">
                  <span aria-hidden className={cn("absolute -left-[1.2rem] top-1.5 size-2 rounded-full ring-2 ring-white", it.ok ? "bg-[var(--success)]" : "bg-[var(--danger)]")} />
                  <p className="text-xs"><span className="font-mono font-semibold text-foreground">{it.n}. {it.tool}</span> <span className="font-mono text-[0.68rem] text-muted-foreground">{JSON.stringify(it.args).slice(0, 120)}</span> <span className="text-[0.65rem] text-muted-foreground">{it.duration_ms != null ? ms(it.duration_ms) : ""}</span></p>
                  {it.output_preview ? <p className="mt-0.5 line-clamp-3 text-[0.7rem] text-slate-600">{it.output_preview}</p> : null}
                  {it.error ? <p className="text-[0.7rem] text-[var(--danger)]">{it.error}</p> : null}
                </li>
              ))}
            </ol>
            <div>
              <p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">Final output</p>
              <Mono>{report.final_output || report.reason || "(nothing)"}</Mono>
            </div>
            {report.steps.length ? (
              <ol className="space-y-0.5 font-mono text-[0.7rem] text-slate-700">{report.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            ) : null}
            <EvidenceRows items={report.evidence} />
            <FileLinks files={report.files} />
            {report.log_hashes.length ? <p className="font-mono text-[0.65rem] text-muted-foreground">chain head after last call: {report.log_hashes[report.log_hashes.length - 1].slice(0, 24)}…</p> : null}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- sandbox

const SAMPLE_CODE = `# Runs in a fresh sandbox: no network, no site-packages, bounded CPU / memory / time / disk.
import json, math
normal, design = 482.0, 520.0
margin = (design - normal) / normal * 100
print(f"margin above normal: {margin:.2f} %")
with open("result.json", "w") as fh:
    json.dump({"margin_percent": round(margin, 2)}, fh)
`;
const SAMPLE_TESTS = `def test_margin():
    assert round((520 - 482) / 482 * 100, 2) == 7.88
`;

function SandboxPanel({ info }: { info: SandboxInfo | null }) {
  const [code, setCode] = useState(SAMPLE_CODE);
  const [tests, setTests] = useState(SAMPLE_TESTS);
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<{ entries: Record<string, unknown>[]; chain: { ok: boolean; entries?: number } } | null>(null);
  const [manifest, setManifest] = useState<SandboxManifest | null>(null);

  const loadSide = useCallback(async () => {
    try {
      const [r, m] = await Promise.all([api.sandboxRuns(20), api.sandboxManifest()]);
      setRuns(r);
      setManifest(m);
    } catch { /* the side panels are informative; a failure there must not block a run */ }
  }, []);
  useEffect(() => { void loadSide(); }, [loadSide]);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.sandboxRun({ code, tests: tests.trim() || null }));
      await loadSide();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Panel>
          <PanelHeader title="Run code" description="Verified = static analysis clean and the task's own tests passed. The working directory is destroyed after the run." />
          <div className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
            <Field label="Code (Python)"><Textarea rows={12} value={code} onChange={(e) => setCode(e.target.value)} className="font-mono text-xs" spellCheck={false} /></Field>
            <Field label="Tests (def test_*)"><Textarea rows={12} value={tests} onChange={(e) => setTests(e.target.value)} className="font-mono text-xs" spellCheck={false} /></Field>
          </div>
          <div className="flex items-center gap-3 border-t border-border/70 px-4 py-2.5">
            <Button size="sm" variant="primary" loading={busy} disabled={!info?.enabled || !code.trim()} onClick={() => void run()}><Play className="size-3.5" /> Run in sandbox</Button>
            {[["import socket\nsocket.create_connection(('8.8.8.8', 53), timeout=3)\nprint('reached')", "try egress"], ["import subprocess\nsubprocess.run(['whoami'])", "try subprocess"], ["x = bytearray(2 * 1024 ** 3)\nprint(len(x))", "memory bomb"], ["while True:\n    pass", "infinite loop"]].map(([c, label]) => (
              <button key={label} type="button" onClick={() => { setCode(c); setTests(""); }} className="rounded-md border border-border/70 bg-white/60 px-2 py-1 text-[0.68rem] text-slate-700 hover:border-primary/40">{label}</button>
            ))}
            {error ? <p role="alert" className="text-xs text-[var(--danger)]">{error}</p> : null}
          </div>
        </Panel>
        {result ? <SandboxResultView r={result} /> : null}
      </div>

      <div className="space-y-4">
        <Panel>
          <PanelHeader title="Isolation" description={info?.enabled ? `backend ${info.backend}` : "disabled"} />
          <div className="px-4 py-2">
            <KeyValues rows={[
              ["Network", "none — sockets refused in-process"],
              ["Wall time", `${info?.limits?.timeout_seconds ?? "—"} s`],
              ["CPU time", `${info?.limits?.cpu_seconds ?? "—"} s`],
              ["Memory", `${info?.limits?.memory_mb ?? "—"} MB`],
              ["Disk writes", `${info?.limits?.max_disk_write_mb ?? "—"} MB, inside the run directory only`],
              ["Processes", String(info?.limits?.max_processes ?? "—")],
              ["Dependencies", manifest?.verified ? `pinned manifest ${(manifest.hash ?? "").slice(0, 12)}…` : (manifest?.error ?? "manifest")],
              ["Ephemeral", "fresh directory per run, destroyed after"],
            ]} />
          </div>
          {manifest?.banned_imports?.length ? (
            <details className="border-t border-border/70 px-4 py-2 text-xs">
              <summary className="cursor-pointer text-muted-foreground">What may run: {manifest.stdlib_allowed?.length ?? 0} stdlib modules allowed, {manifest.banned_imports.length} banned</summary>
              <p className="mt-1 font-mono text-[0.65rem] text-slate-600">banned: {manifest.banned_imports.join(", ")}</p>
              <p className="mt-1 font-mono text-[0.65rem] text-slate-600">allowed: {(manifest.stdlib_allowed ?? []).join(", ")}</p>
              {manifest.vendored?.length ? <p className="mt-1 font-mono text-[0.65rem] text-slate-600">vendored (checksummed): {manifest.vendored.map((v) => v.path).join(", ")}</p> : <p className="mt-1 text-[0.65rem] text-muted-foreground">No vendored packages: nothing is ever fetched at run time.</p>}
            </details>
          ) : null}
        </Panel>
        <Panel>
          <PanelHeader title="Run log" actions={<ChainBadge chain={runs?.chain} />} />
          {runs?.entries.length ? (
            <ul className="thin-scroll max-h-64 divide-y divide-border/60 overflow-y-auto text-[0.7rem]">
              {[...runs.entries].reverse().map((e, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 px-3 py-1.5">
                  <span className="font-mono text-muted-foreground">{timeOfDay(Number(e.ts ?? 0))}</span>
                  <span className="font-mono text-slate-700">{String(e.run_id ?? "")}</span>
                  <OkBadge ok={!!e.ok} />
                  {e.verified ? <Badge tone="primary">verified</Badge> : null}
                  {e.limit_hit ? <Badge tone="warning">{String(e.limit_hit)}</Badge> : null}
                  <span className="text-muted-foreground">exit {String(e.exit_code ?? "?")} · {ms(Number(e.duration_ms ?? 0))} · {String(e.peak_rss_mb ?? "?")} MB</span>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No runs yet" />}
        </Panel>
      </div>
    </div>
  );
}

function SandboxResultView({ r }: { r: SandboxResult }) {
  return (
    <Panel>
      <PanelHeader
        title={`Run ${r.run_id}`}
        description={`${r.backend} · exit ${r.exit_code} · ${ms(r.duration_ms)} · peak ${r.peak_rss_mb} MB · cpu ${r.cpu_seconds}s${r.python_version ? ` · Python ${r.python_version}` : ""}`}
        actions={
          <div className="flex flex-wrap items-center gap-1.5">
            <OkBadge ok={r.ok} yes="ran ok" no="did not complete" />
            <Badge tone={r.verified ? "success" : "neutral"}>{r.verified ? "VERIFIED" : "not verified"}</Badge>
            {r.limit_hit ? <Badge tone="danger">limit: {r.limit_hit}</Badge> : null}
            {r.workdir_destroyed ? <Badge tone="info">workdir destroyed</Badge> : null}
          </div>
        }
      />
      <div className="space-y-3 px-4 py-3">
        {r.egress_attempts.length ? (
          <div role="alert" className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/[0.06] px-3 py-2 text-xs">
            <p className="font-medium text-[var(--danger)]">Egress attempt refused inside the sandbox</p>
            <p className="font-mono text-slate-700">{r.egress_attempts.join(", ")}</p>
          </div>
        ) : null}
        {r.error ? <p className="text-xs text-[var(--danger)]">{r.error}</p> : null}
        <div className="grid gap-3 md:grid-cols-2">
          <div><p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">stdout</p><Mono>{r.stdout || "(empty)"}</Mono></div>
          <div><p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">stderr</p><Mono>{r.stderr || "(empty)"}</Mono></div>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-border/70 bg-white/60 px-3 py-2 text-xs">
            <p className="font-medium text-foreground">Static analysis {r.static_analysis ? <OkBadge ok={!!r.static_analysis.ok} yes="clean" no="findings" /> : null}</p>
            {r.static_analysis?.syntax_error ? <p className="text-[var(--danger)]">{r.static_analysis.syntax_error}</p> : null}
            {r.static_analysis?.banned_imports?.length ? <p className="text-[var(--danger)]">banned imports: {r.static_analysis.banned_imports.join(", ")}</p> : null}
            {r.static_analysis?.banned_calls?.length ? <p className="text-[var(--danger)]">banned calls: {r.static_analysis.banned_calls.join(", ")}</p> : null}
            {r.static_analysis?.pyflakes?.length ? <p className="text-slate-600">pyflakes: {r.static_analysis.pyflakes.join("; ")}</p> : null}
          </div>
          <div className="rounded-lg border border-border/70 bg-white/60 px-3 py-2 text-xs">
            <p className="font-medium text-foreground">Tests {r.verification ? <Badge tone={r.verification.passed ? "success" : "danger"}>{r.verification.tests_passed}/{r.verification.tests_total} passed</Badge> : <span className="text-muted-foreground">none supplied</span>}</p>
            {r.verification?.failures?.length ? <ul className="mt-1 font-mono text-[0.65rem] text-[var(--danger)]">{r.verification.failures.map((f, i) => <li key={i}>{f}</li>)}</ul> : null}
          </div>
        </div>
        {r.files_written.length ? (
          <div>
            <p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">Files written (hashed, then the directory was destroyed)</p>
            <ul className="font-mono text-[0.68rem] text-slate-700">{r.files_written.map((f) => <li key={f.path}>{f.path} · {bytes(f.bytes)} · {f.sha256.slice(0, 16)}…</li>)}</ul>
          </div>
        ) : null}
        <p className="font-mono text-[0.65rem] text-muted-foreground">run-log entry {r.log_hash.slice(0, 24)}…{r.manifest_hash ? ` · manifest ${r.manifest_hash.slice(0, 12)}…` : ""}</p>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- workspace & log

function Workspace() {
  const toast = useToast();
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const [root, setRoot] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const out = await api.workspace(SESSION);
      setFiles(out.files);
      setRoot(out.workspace);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const get = async (path: string) => {
    try {
      saveBlob(await download(api.workspaceFilePath(SESSION, path)), path.split("/").pop() || path);
    } catch (err) {
      toast.push({ tone: "danger", message: "Download failed", detail: err instanceof ApiError ? err.message : String(err) });
    }
  };
  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  return (
    <Panel>
      <PanelHeader title="Session workspace" description={root || "…"} actions={<Button size="sm" variant="ghost" onClick={() => void load()}>Refresh</Button>} />
      {files?.length ? (
        <ul className="divide-y divide-border/60 text-xs">
          {files.map((f) => (
            <li key={f.path} className="flex items-center gap-3 px-4 py-1.5">
              <span className="min-w-0 flex-1 truncate font-mono text-slate-700">{f.path}</span>
              <span className="text-muted-foreground">{bytes(f.bytes)}</span>
              <span className="text-muted-foreground">{timeOfDay(f.modified)}</span>
              <Button size="sm" variant="ghost" onClick={() => void get(f.path)} aria-label={`Download ${f.path}`}><Download className="size-3.5" /></Button>
            </li>
          ))}
        </ul>
      ) : <EmptyState icon={<FolderOpen className="size-6" />} title="Empty workspace" description="Files written by tools, the agent or the sandbox land here." />}
    </Panel>
  );
}

function ToolLog() {
  const [rows, setRows] = useState<ToolCallEntry[] | null>(null);
  const [chain, setChain] = useState<{ ok: boolean; entries?: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const out = await api.toolCalls(100);
      setRows(out.entries);
      setChain(out.chain);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const list = useMemo(() => [...(rows ?? [])].reverse(), [rows]);
  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  return (
    <Panel>
      <PanelHeader title="Tool calls" description="Every call, hashed in a chain; the arguments are recorded as a digest, never as content." actions={<ChainBadge chain={chain} />} />
      {list.length ? (
        <ul className="divide-y divide-border/60 text-[0.7rem]">
          {list.map((e, i) => (
            <li key={`${e.seq ?? i}`} className="flex flex-wrap items-center gap-2 px-4 py-1.5">
              <span className="font-mono text-muted-foreground">{timeOfDay(e.ts)}</span>
              <span className="font-mono font-semibold text-foreground">{e.tool}</span>
              <OkBadge ok={e.ok} />
              <span className="text-muted-foreground">{e.duration_ms != null ? ms(e.duration_ms) : ""}{e.evidence ? ` · ${e.evidence} evidence` : ""}{e.files?.length ? ` · ${e.files.length} file(s)` : ""}</span>
              {e.error ? <span className="text-[var(--danger)]">{e.error}</span> : null}
              <span className="ml-auto font-mono text-[0.65rem] text-muted-foreground">{e.session}</span>
            </li>
          ))}
        </ul>
      ) : <EmptyState title="No tool calls yet" />}
    </Panel>
  );
}

/**
 * The multi-model backend: which open-weight models are registered, what each declares it is
 * good at, which are actually installed and fit the card, and which one wins each task kind.
 *
 * Nothing here decides anything. The router on the server picks a model per call from these
 * profiles and writes every decision to a hash-chained routing log; this page reads both. The
 * "Route a request" box runs the same decomposition the orchestrator runs before a question —
 * without calling any model — so the choice can be inspected before it is made.
 */
import { Package, PlusCircle, Route, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ChainBadge, Mono } from "@/components/Bits";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { timeOfDay } from "@/lib/format";
import type { ModelsOverview, PackageLog, PackageVerification, RoutingEntry, RoutingLog, RoutingPlan } from "@/lib/types_ext";
import { useAuth } from "@/store/auth";
import { Badge, Button, EmptyState, ErrorState, Field, Input, PageHeader, Panel, PanelHeader, Skeleton, Textarea, useToast } from "@/ui";

const KINDS = ["classification", "resolution", "extraction", "summarization", "composition", "reasoning", "calculation", "code", "vision", "planning", "tool_agent"];
const KIND_SHORT: Record<string, string> = {
  classification: "class", resolution: "resolve", extraction: "extract", summarization: "summ", composition: "compose",
  reasoning: "reason", calculation: "calc", code: "code", vision: "vision", planning: "plan", tool_agent: "tools",
};

function Score({ value }: { value: number | undefined }) {
  const v = typeof value === "number" ? value : 0;
  const bg = v === 0 ? "bg-slate-900/[0.04] text-muted-foreground"
    : v >= 0.8 ? "bg-[var(--success)]/25 text-foreground"
    : v >= 0.6 ? "bg-[var(--success)]/12 text-foreground"
    : v >= 0.4 ? "bg-[var(--warning)]/15 text-slate-700"
    : "bg-[var(--danger)]/10 text-slate-600";
  return <td className={cn("px-1.5 py-1 text-center font-mono text-[0.65rem] tabular-nums", bg)} title={value === undefined ? "not declared" : String(value)}>{v ? v.toFixed(2) : "–"}</td>;
}

export default function Models() {
  const { atLeast } = useAuth();
  const toast = useToast();
  const [overview, setOverview] = useState<ModelsOverview | null>(null);
  const [log, setLog] = useState<RoutingLog | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [o, l] = await Promise.all([api.models(), api.routingLog(50)]);
      setOverview(o);
      setLog(l);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!overview) return <Skeleton className="h-64 w-full" />;

  const installed = overview.models.filter((m) => m.installed).length;

  return (
    <div>
      <PageHeader
        title="Models"
        description={`${overview.models.length} registered, ${installed} installed on this machine. Each declares a capability profile; the router picks the best installed model that fits the card per call and logs why.`}
        actions={
          <div className="flex items-center gap-1.5">
            <Badge tone={overview.routing_enabled ? "success" : "warning"}>{overview.routing_enabled ? "routing on" : "routing off"}</Badge>
            <Badge tone="neutral">{overview.profile} · {overview.vram_mb} MB</Badge>
            <Badge tone="neutral">budget {overview.budget}</Badge>
          </div>
        }
      />

      <Panel>
        <PanelHeader
          title="Capability registry"
          description={`Scores are the model's declared strength per task kind (0–1). Resident: ${overview.resident ?? "none loaded"}. Text default ${overview.default_model}${overview.vision_model ? `, vision ${overview.vision_model}` : ""}.`}
        />
        <div className="thin-scroll overflow-x-auto">
          <table className="w-full border-collapse text-[0.78rem]">
            <thead>
              <tr className="bg-slate-900/[0.04] text-left text-[0.68rem] uppercase tracking-wide text-slate-600">
                <th className="px-3 py-2">Model</th>
                <th className="px-2 py-2">State</th>
                <th className="px-2 py-2 text-right">GB</th>
                <th className="px-2 py-2 text-right">ctx</th>
                {KINDS.map((k) => <th key={k} className="px-1.5 py-2 text-center font-mono normal-case" title={k}>{KIND_SHORT[k]}</th>)}
              </tr>
            </thead>
            <tbody>
              {overview.models.map((m) => (
                <tr key={m.name} className={cn("border-t border-border/60", !m.installed && "opacity-60")}>
                  <td className="px-3 py-1.5">
                    <p className="font-mono text-xs font-semibold text-foreground">{m.name}</p>
                    <p className="text-[0.65rem] text-muted-foreground">
                      {m.modalities.join(" + ")}{m.thinking ? " · thinking" : ""}{m.source !== "builtin" ? ` · ${m.source}` : ""}
                    </p>
                  </td>
                  <td className="px-2 py-1.5">
                    <div className="flex flex-wrap gap-1">
                      {m.installed ? <Badge tone="success">installed</Badge> : <Badge tone="neutral">not pulled</Badge>}
                      {m.installed ? (m.fits_vram ? <Badge tone="info">fits</Badge> : <Badge tone="warning" title={`needs ${m.min_vram_mb} MB`}>spills</Badge>) : null}
                      {m.resident ? <Badge tone="primary">resident</Badge> : null}
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-xs tabular-nums">{m.size_gb}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-xs tabular-nums">{m.context >= 1024 ? `${Math.round(m.context / 1024)}k` : m.context}</td>
                  {KINDS.map((k) => <Score key={k} value={m.capabilities[k]} />)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="px-4 py-2 text-[0.68rem] text-muted-foreground">
          To plug a model in: pull it (or import a signed package), add one entry to {overview.registry_files.local}, and it routes at the next start. Nothing else changes.
        </p>
      </Panel>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Panel>
          <PanelHeader title="Best installed model per task kind" description="What the router would choose right now, at this budget." />
          {Object.keys(overview.best_per_kind).length ? (
            <ul className="divide-y divide-border/60 px-4 text-xs">
              {Object.entries(overview.best_per_kind).map(([kind, model]) => (
                <li key={kind} className="flex items-center justify-between gap-2 py-1.5">
                  <span className="text-muted-foreground">{kind}</span>
                  <span className="font-mono font-medium text-foreground">{model ?? "—"}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Routing is not active" description="Ollama is not reachable or RWB_ROUTING is off; every call would use the profile's default model." />
          )}
        </Panel>

        <RouteBox enabled={overview.routing_enabled} />
      </div>

      <Panel className="mt-4">
        <PanelHeader
          title="Routing log"
          description="Every decision the router made: the model chosen for each call, the candidates it scored, and the reason."
          actions={<ChainBadge chain={log?.chain} />}
        />
        {log?.entries.length ? (
          <ul className="divide-y divide-border/60">
            {[...log.entries].reverse().map((e, i) => <RoutingRow key={`${e.seq ?? i}`} entry={e} />)}
          </ul>
        ) : (
          <EmptyState icon={<Route className="size-6" />} title="No routing decisions yet" description="Ask a question with a model available and the decisions appear here." />
        )}
      </Panel>

      {atLeast("admin") ? <RegisterModel onDone={() => { void load(); toast.push({ tone: "success", message: "Model registered" }); }} /> : null}
      {atLeast("manager") ? <Packages /> : null}
    </div>
  );
}

function RoutingRow({ entry }: { entry: RoutingEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="px-4 py-2">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full flex-wrap items-center gap-2 text-left" aria-expanded={open}>
        <span className="font-mono text-[0.68rem] text-muted-foreground">{timeOfDay(entry.ts)}</span>
        <Badge tone="neutral">{entry.kind}</Badge>
        <span className="font-mono text-xs font-semibold text-foreground">{entry.chosen ?? "no model"}</span>
        {entry.needs_vision ? <Badge tone="info">image</Badge> : null}
        <span className="min-w-0 flex-1 truncate text-[0.7rem] text-muted-foreground">{entry.purpose ? `${entry.purpose} · ` : ""}{entry.reason}</span>
        {entry.run_id ? <span className="font-mono text-[0.65rem] text-muted-foreground">{entry.run_id}</span> : null}
      </button>
      {open && entry.candidates?.length ? (
        <ul className="mt-1.5 grid gap-0.5 border-l-2 border-primary/20 pl-3 text-[0.7rem]">
          {entry.candidates.map((c) => (
            <li key={c.name} className="flex items-center gap-2">
              <span className="font-mono text-slate-700">{c.name}</span>
              <span className="tabular-nums text-foreground">{c.score.toFixed(2)}</span>
              <span className="text-muted-foreground">{c.installed ? (c.fits_vram ? "installed" : "spills off the GPU") : "not installed"}{c.note ? ` · ${c.note}` : ""}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function RouteBox({ enabled }: { enabled: boolean }) {
  const [text, setText] = useState("Read the scanned P&ID, calculate the margin between 482 and 520 m3/h and write a short note");
  const [plan, setPlan] = useState<RoutingPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setPlan(await api.routeText(text));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel>
      <PanelHeader title="Route a request" description="Decompose a hybrid request into sub-tasks and see which model would take each. No model is called." />
      <div className="space-y-2 px-4 py-3">
        <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label="Request to route" />
        <div className="flex items-center gap-2">
          <Button size="sm" variant="primary" loading={busy} disabled={!enabled || !text.trim()} onClick={() => void run()}>
            <Route className="size-3.5" /> Route
          </Button>
          {!enabled ? <span className="text-[0.7rem] text-muted-foreground">Routing is not active on this server.</span> : null}
        </div>
        {error ? <p role="alert" className="text-xs text-[var(--danger)]">{error}</p> : null}
        {plan ? (
          <ol className="space-y-1.5">
            <li className="text-[0.7rem] text-muted-foreground">{plan.hybrid ? "Hybrid request" : "Single task"} · {plan.subtasks.length} sub-task{plan.subtasks.length === 1 ? "" : "s"}</li>
            {plan.subtasks.map((s, i) => {
              const d = plan.decisions[i];
              const worker = s.deterministic_tool && s.kind === "calculation"
                ? `${s.deterministic_tool} (deterministic tool)`
                : `${d?.chosen ?? "no model"}${s.deterministic_tool ? ` + ${s.deterministic_tool}` : ""}`;
              return (
                <li key={i} className="rounded-lg border border-border/70 bg-white/60 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone="primary">{s.kind}</Badge>
                    {s.needs_vision ? <Badge tone="info">image input</Badge> : null}
                    <span className="text-xs text-slate-700">{s.description}</span>
                  </div>
                  <p className="mt-1 text-xs"><span className="text-muted-foreground">→ </span><span className="font-mono font-semibold text-foreground">{worker}</span></p>
                  {d?.reason ? <p className="text-[0.68rem] text-muted-foreground">{d.reason}</p> : null}
                </li>
              );
            })}
          </ol>
        ) : null}
      </div>
    </Panel>
  );
}

function RegisterModel({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [sizeGb, setSizeGb] = useState("4.7");
  const [minVram, setMinVram] = useState("6000");
  const [context, setContext] = useState("8192");
  const [vision, setVision] = useState(false);
  const [notes, setNotes] = useState("");
  const [caps, setCaps] = useState<Record<string, string>>(() => Object.fromEntries(KINDS.map((k) => [k, k === "vision" ? "0" : "0.6"])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.registerModel({
        name: name.trim(), size_gb: Number(sizeGb) || 0, min_vram_mb: Number(minVram) || 0, context: Number(context) || 4096,
        modalities: vision ? ["text", "image"] : ["text"], notes,
        capabilities: Object.fromEntries(Object.entries(caps).map(([k, v]) => [k, Math.max(0, Math.min(1, Number(v) || 0))])),
      });
      setName("");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel className="mt-4">
      <PanelHeader title="Register a model" description="Administrators only. Writes the profile to the local registry; the router uses it immediately if the model is pulled." />
      <div className="grid gap-3 px-4 py-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Ollama name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="qwen2.5-coder:7b" /></Field>
        <Field label="Size (GB)"><Input value={sizeGb} onChange={(e) => setSizeGb(e.target.value)} inputMode="decimal" /></Field>
        <Field label="Fits fully at (MB VRAM)"><Input value={minVram} onChange={(e) => setMinVram(e.target.value)} inputMode="numeric" /></Field>
        <Field label="Context (tokens)"><Input value={context} onChange={(e) => setContext(e.target.value)} inputMode="numeric" /></Field>
        <div className="sm:col-span-2 lg:col-span-4">
          <p className="mb-1.5 text-xs font-medium text-slate-600">Capability scores (0–1)</p>
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {KINDS.map((k) => (
              <label key={k} className="text-[0.68rem] text-muted-foreground">
                {k}
                <Input className="mt-0.5 h-8 font-mono text-xs" value={caps[k]} onChange={(e) => setCaps((c) => ({ ...c, [k]: e.target.value }))} inputMode="decimal" />
              </label>
            ))}
          </div>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-700">
          <input type="checkbox" checked={vision} onChange={(e) => setVision(e.target.checked)} /> accepts images
        </label>
        <Field label="Notes" className="sm:col-span-2 lg:col-span-3"><Input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="what it is for" /></Field>
      </div>
      <div className="flex items-center gap-3 border-t border-border/70 px-4 py-2.5">
        <Button size="sm" variant="primary" loading={busy} disabled={!name.trim()} onClick={() => void submit()}>
          <PlusCircle className="size-3.5" /> Register
        </Button>
        {error ? <p role="alert" className="text-xs text-[var(--danger)]">{error}</p> : null}
      </div>
    </Panel>
  );
}

function Packages() {
  const { atLeast } = useAuth();
  const [log, setLog] = useState<PackageLog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [path, setPath] = useState("");
  const [report, setReport] = useState<PackageVerification | null>(null);
  const [busy, setBusy] = useState<"verify" | "import" | null>(null);

  const load = useCallback(async () => {
    try {
      setLog(await api.packages());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const verify = async () => {
    setBusy("verify");
    try {
      setReport(await api.verifyPackage(path.trim()));
      await load();
    } catch (err) {
      setReport({ ok: false, detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };
  const dryRun = async () => {
    setBusy("import");
    try {
      const out = await api.importPackage(path.trim(), true);
      setReport(out.verification);
      await load();
    } catch (err) {
      setReport({ ok: false, detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };

  const entries = useMemo(() => (log?.entries ?? []).slice(-10).reverse(), [log]);

  return (
    <Panel className="mt-4">
      <PanelHeader
        title="Signed model packages"
        description="A new model arrives as a package with a checksum manifest signed by a trusted key — never as a live download. Verification is chained into the audit."
        actions={<ChainBadge chain={log?.chain} />}
      />
      <div className="grid gap-4 px-4 py-3 lg:grid-cols-2">
        <div className="space-y-2">
          <p className="flex items-center gap-1.5 text-xs font-medium text-slate-600"><ShieldCheck className="size-3.5 text-primary" /> Trusted signers</p>
          {error ? <p className="text-xs text-[var(--danger)]">{error}</p> : null}
          {log?.trusted_signers.length ? (
            <ul className="flex flex-wrap gap-1.5">{log.trusted_signers.map((s) => <Badge key={s} tone="success">{s}</Badge>)}</ul>
          ) : <p className="text-xs text-muted-foreground">None yet — <code className="font-mono">workbench packages keygen</code> then <code className="font-mono">packages trust</code>.</p>}
          <p className="text-[0.68rem] text-muted-foreground">Trust store: {log?.trust_dir ?? "—"}</p>
          <div className="flex items-end gap-2">
            <Field label="Package directory or .tar on this server" className="flex-1">
              <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="data/model_packages/qwen-coder-7b" />
            </Field>
            <Button size="sm" variant="outline" loading={busy === "verify"} disabled={!path.trim()} onClick={() => void verify()}>Verify</Button>
            {atLeast("admin") ? (
              <Button size="sm" variant="outline" loading={busy === "import"} disabled={!path.trim()} onClick={() => void dryRun()}>Dry-run import</Button>
            ) : null}
          </div>
          {report ? (
            <div className={cn("rounded-lg border px-3 py-2 text-xs", report.ok ? "border-[var(--success)]/30 bg-[var(--success)]/[0.06]" : "border-[var(--danger)]/30 bg-[var(--danger)]/[0.06]")}>
              <p className="font-medium text-foreground">{report.ok ? "Package verified" : "Package refused"}{report.name ? ` · ${report.name}:${report.version}` : ""}</p>
              <p className="text-muted-foreground">{report.detail}</p>
              {report.mismatches?.length ? <p className="mt-1 text-[var(--danger)]">Mismatch: {report.mismatches.join(", ")}</p> : null}
              <p className="mt-1 text-[0.68rem] text-muted-foreground">
                signature {report.signature_ok ? "ok" : "not ok"} · signer {report.signer_trusted ? "trusted" : "untrusted"}{report.files_checked != null ? ` · ${report.files_checked} files checked` : ""}
              </p>
            </div>
          ) : null}
        </div>
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-slate-600"><Package className="size-3.5 text-primary" /> Recent verifications and imports</p>
          {entries.length ? (
            <Mono maxHeight="max-h-52">{entries.map((e) => `${timeOfDay(Number(e.ts ?? 0))}  ${String(e.event ?? e.outcome ?? "")}  ${String(e.name ?? e.package ?? "")}  ${String(e.detail ?? "")}`).join("\n")}</Mono>
          ) : <p className="text-xs text-muted-foreground">Nothing verified yet.</p>}
        </div>
      </div>
    </Panel>
  );
}


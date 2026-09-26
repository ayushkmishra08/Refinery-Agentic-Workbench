/**
 * Proof that nothing leaves the premises, refreshed every three seconds.
 *
 * Three things a sceptical reviewer can check here rather than take on trust: the in-process
 * egress guard (a socket to a public address is refused before a packet is sent), the live
 * network monitor (every connection the host makes, classified, in a hash-chained log for the
 * whole session), and the integrity of every chained log. The most convincing demonstration is
 * still pulling the uplink cable — when every uplink interface goes down, this page says so,
 * loudly, with the time it happened.
 */
import { Activity, Cable, Lock, Radar, RefreshCw, ShieldCheck, ShieldOff, Unplug } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ChainBadge, KeyValues, bytes, clock } from "@/components/Bits";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { ms } from "@/lib/format";
import type { ChainRow, ConnectionEntry, SovereigntyReport } from "@/lib/types_ext";
import { useAuth } from "@/store/auth";
import { Badge, Button, EmptyState, ErrorState, PageHeader, Panel, PanelHeader, Skeleton, useToast } from "@/ui";

export default function Sovereignty() {
  const { atLeast } = useAuth();
  const toast = useToast();
  const [report, setReport] = useState<SovereigntyReport | null>(null);
  const [conns, setConns] = useState<ConnectionEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [deep, setDeep] = useState<ChainRow[] | null>(null);
  const [live, setLive] = useState(true);
  /** When the uplinks were first seen down in this tab — the moment the cable came out. */
  const [downSince, setDownSince] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, c] = await Promise.all([api.sovereignty(false), api.connections(40, true)]);
      setReport(r);
      setConns(c.entries);
      setError(null);
      setDownSince((prev) => (r.network_monitor.physically_disconnected ? prev ?? Date.now() : null));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(id);
  }, [live, load]);

  const verifyAll = async () => {
    setVerifying(true);
    try {
      const out = await api.verifyChains();
      setDeep(out.chains);
      toast.push({ tone: out.intact ? "success" : "danger", message: out.intact ? "Every chained log is intact" : "A chained log has been altered" });
    } catch (err) {
      toast.push({ tone: "danger", message: "Verification failed", detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setVerifying(false);
    }
  };

  const chains = deep ?? report?.chains ?? [];
  const clean = useMemo(() => {
    if (!report) return false;
    const m = report.network_monitor;
    return report.egress_guard.installed && (m.workbench_external_connections ?? 0) === 0 && report.chains_intact;
  }, [report]);

  if (error && !report) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!report) return <Skeleton className="h-64 w-full" />;

  const m = report.network_monitor;
  const g = report.egress_guard;
  const uplinks = (m.interfaces ?? []).filter((i) => !i.loopback);
  const disconnected = !!m.physically_disconnected;

  return (
    <div>
      <PageHeader
        title="Sovereignty"
        description="Nothing leaves the premises — demonstrated by a connection log, an in-process egress guard and tamper-evident audit chains, not claimed."
        actions={
          <div className="flex items-center gap-1.5">
            <Button size="sm" variant="ghost" onClick={() => setLive((v) => !v)} aria-pressed={live}>
              <RefreshCw className={cn("size-3.5", live && "animate-spin [animation-duration:3s]")} /> {live ? "live" : "paused"}
            </Button>
            {atLeast("manager") ? (
              <Button size="sm" variant="outline" loading={verifying} onClick={() => void verifyAll()}>Verify all chains (deep)</Button>
            ) : null}
          </div>
        }
      />

      {/* ------------------------------------------------------------ the cable */}
      {disconnected ? (
        <div role="status" className="mb-4 flex items-center gap-4 rounded-xl border-2 border-[var(--success)] bg-[var(--success)]/[0.1] px-5 py-4">
          <Unplug className="size-9 shrink-0 text-[var(--success)]" />
          <div>
            <p className="text-lg font-semibold text-foreground">AIR-GAPPED — every uplink is down</p>
            <p className="text-xs text-slate-700">
              Uplink down since {downSince ? new Date(downSince).toLocaleTimeString() : "—"}
              {m.interface_events?.length ? ` · last interface event: ${m.interface_events[m.interface_events.length - 1].name} ${m.interface_events[m.interface_events.length - 1].event.replace("interface_", "")} at ${clock(m.interface_events[m.interface_events.length - 1].ts)}` : ""}.
              The workbench keeps answering from local documents and local models.
            </p>
          </div>
        </div>
      ) : null}

      {/* ------------------------------------------------------------ verdict */}
      <div className={cn("mb-4 flex items-start gap-3 rounded-xl border px-4 py-3",
        clean ? "border-[var(--success)]/30 bg-[var(--success)]/[0.07]" : "border-[var(--warning)]/30 bg-[var(--warning)]/[0.08]")}>
        {clean ? <ShieldCheck className="mt-0.5 size-5 shrink-0 text-[var(--success)]" /> : <ShieldOff className="mt-0.5 size-5 shrink-0 text-[var(--warning)]" />}
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{report.verdict}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {clean
              ? "Egress guard installed, no external connection by the workbench or the model server, every audit chain intact."
              : "Other programs on a development host will show external connections; what matters is the workbench and the model server themselves, and the guard that refuses them a socket."}
          </p>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={<Lock className="size-4" />} label="Egress guard" value={g.installed ? "installed" : "OFF"} tone={g.installed ? "ok" : "bad"}
              hint={g.installed ? `${g.blocked_count ?? 0} attempt${g.blocked_count === 1 ? "" : "s"} refused in-process` : "RWB_AIRGAP=on to enforce"} />
        <Tile icon={<Radar className="size-4" />} label="Workbench external connections" value={String(m.workbench_external_connections ?? 0)}
              tone={(m.workbench_external_connections ?? 0) === 0 ? "ok" : "bad"} hint={`${m.external_connections ?? 0} by any program on the host`} />
        <Tile icon={<Cable className="size-4" />} label="Uplinks up" value={`${m.uplinks_up ?? 0} / ${m.uplinks_total ?? 0}`}
              tone={disconnected ? "ok" : "neutral"} hint={disconnected ? "physically disconnected" : "pull the cable to prove it"} />
        <Tile icon={<Activity className="size-4" />} label="Monitor" value={m.running ? "running" : "stopped"} tone={m.running ? "ok" : "bad"}
              hint={m.running ? `${m.samples ?? 0} samples · ${m.connections_seen ?? 0} connections · up ${ms((m.uptime_seconds ?? 0) * 1000)}` : "not recording"} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel>
          <PanelHeader title="Interfaces" description="Loopback carries Ollama and the API; anything else is an uplink." />
          <table className="w-full text-xs">
            <thead><tr className="bg-slate-900/[0.04] text-left text-[0.65rem] uppercase tracking-wide text-slate-600"><th className="px-3 py-1.5">Interface</th><th className="px-2 py-1.5">State</th><th className="px-2 py-1.5">Addresses</th><th className="px-2 py-1.5 text-right">In / out</th></tr></thead>
            <tbody>
              {(m.interfaces ?? []).map((i) => (
                <tr key={i.name} className="border-t border-border/60">
                  <td className="px-3 py-1.5 font-medium text-foreground">{i.name}{i.loopback ? <span className="ml-1 text-[0.65rem] text-muted-foreground">loopback</span> : null}</td>
                  <td className="px-2 py-1.5"><Badge tone={i.isup ? (i.loopback ? "neutral" : "warning") : "success"}>{i.isup ? "up" : "down"}</Badge></td>
                  <td className="px-2 py-1.5 font-mono text-[0.65rem] text-slate-600">{i.addresses.join(", ") || "—"}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-[0.65rem] tabular-nums text-slate-600">{bytes(i.bytes_recv)} / {bytes(i.bytes_sent)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {m.interface_events?.length ? (
            <div className="border-t border-border/70 px-4 py-2">
              <p className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">Interface events this session</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {[...m.interface_events].reverse().map((e, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <span className="font-mono text-[0.68rem] text-muted-foreground">{clock(e.ts)}</span>
                    <Badge tone={e.event === "interface_down" ? "success" : "warning"}>{e.event.replace("interface_", "")}</Badge>
                    <span className="text-slate-700">{e.name}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="px-4 py-2 text-[0.68rem] text-muted-foreground">{uplinks.length} uplink interface{uplinks.length === 1 ? "" : "s"} seen. An uplink that is down is a good sign here.</p>
        </Panel>

        <Panel>
          <PanelHeader title="Egress guard" description="Sockets to anything but loopback and private ranges are refused inside the process, before any packet leaves." />
          <div className="px-4 py-3">
            <KeyValues rows={[
              ["Installed", g.installed ? "yes" : "no"],
              ["Allowed hosts", (g.allowed_hosts ?? []).join(", ") || "—"],
              ["Private ranges", g.allow_private ? "allowed (Ollama, Neo4j on the LAN)" : "refused"],
              ["Refused so far", String(g.blocked_count ?? 0)],
              ["Offline flags", Object.entries(g.offline_env ?? {}).filter(([, v]) => v).map(([k]) => k).join(", ") || "—"],
            ]} />
            {g.recent_blocked?.length ? (
              <ul className="mt-2 space-y-0.5 text-[0.7rem]">
                {[...g.recent_blocked].reverse().map((b, i) => (
                  <li key={i} className="flex items-center gap-2"><span className="font-mono text-muted-foreground">{clock(b.ts)}</span><Badge tone="danger">refused</Badge><span className="font-mono text-slate-700">{b.host}{b.port ? `:${b.port}` : ""}</span><span className="text-muted-foreground">{b.reason}</span></li>
                ))}
              </ul>
            ) : null}
          </div>
          <div className="border-t border-border/70 px-4 py-2.5">
            <p className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">Transport between components</p>
            <p className="mt-0.5 text-xs text-slate-700">
              {report.tls?.configured
                ? <>Local TLS certificates present{report.tls.active ? ", serving over TLS now" : " (start with `serve --tls`)"}{report.tls.mtls ? ", mutual TLS required" : ""}.</>
                : <>Plain HTTP on loopback. {report.tls?.hint ?? "python -m workbench serve --tls"} issues a local CA and certificates.</>}
            </p>
          </div>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Panel>
          <PanelHeader title="External connections observed" description="Any connection to a public address since the monitor started, whichever program made it." actions={<ChainBadge chain={m.chain} label="log" />} />
          {conns.length ? (
            <ul className="thin-scroll max-h-72 divide-y divide-border/60 overflow-y-auto">
              {[...conns].reverse().map((c, i) => (
                <li key={`${c.seq ?? i}`} className="flex flex-wrap items-center gap-2 px-4 py-1.5 text-[0.7rem]">
                  <span className="font-mono text-muted-foreground">{clock(c.ts)}</span>
                  <Badge tone={c.workbench_process ? "danger" : "neutral"}>{c.workbench_process ? "WORKBENCH" : "other program"}</Badge>
                  <span className="font-medium text-slate-700">{c.process || `pid ${c.pid ?? "?"}`}</span>
                  <span className="font-mono text-slate-600">→ {c.raddr}</span>
                  <span className="text-muted-foreground">{c.status}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={<ShieldCheck className="size-6" />} title="No external connection observed" description={`The host's socket table has been sampled ${m.samples ?? 0} times since the monitor started.`} />
          )}
        </Panel>

        <Panel>
          <PanelHeader title="Tamper-evident logs" description="Every entry hashes the one before it; editing, dropping or reordering a past entry breaks the chain from that point." actions={<Badge tone={chains.every((c) => c.ok) ? "success" : "danger"}>{chains.every((c) => c.ok) ? "all intact" : "ALTERED"}</Badge>} />
          <table className="w-full text-xs">
            <tbody>
              {chains.map((c) => (
                <tr key={c.log} className="border-t border-border/60">
                  <td className="px-3 py-1.5 text-slate-700">{c.log}</td>
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-muted-foreground">{c.exists ? c.entries : "—"}</td>
                  <td className="px-2 py-1.5"><Badge tone={c.ok ? (c.exists ? "success" : "neutral") : "danger"}>{c.ok ? (c.exists ? "intact" : "empty") : `broken at #${c.first_bad_seq ?? "?"}`}</Badge></td>
                  <td className="truncate px-2 py-1.5 text-[0.65rem] text-muted-foreground" title={c.detail}>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-4 py-2 text-[0.68rem] text-muted-foreground">{deep ? "Deep verification: every conversation's audit file was walked." : "Run audits are counted here and walked in full by the deep verification."}</p>
        </Panel>
      </div>
    </div>
  );
}

function Tile({ icon, label, value, hint, tone }: { icon: React.ReactNode; label: string; value: string; hint?: string; tone: "ok" | "bad" | "neutral" }) {
  return (
    <Panel className="px-4 py-3">
      <p className="flex items-center gap-1.5 text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">{icon} {label}</p>
      <p className={cn("mt-1 text-xl font-semibold tabular-nums", tone === "ok" ? "text-[var(--success)]" : tone === "bad" ? "text-[var(--danger)]" : "text-foreground")}>{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
    </Panel>
  );
}

/**
 * Human review of generated deliverables.
 *
 * Every Word, Excel or PowerPoint file the workbench produces is a draft pending sign-off, and
 * nothing here ever approves itself. Each figure in a draft links back to the document, page
 * and text it came from; a figure the system is not sure of — a low-confidence OCR line, a
 * value the model derived rather than quoted — is flagged, and the sign-off button stays
 * blocked until a person has looked at every flag and said what to do with it.
 */
import { AlertTriangle, CheckCircle2, ClipboardCheck, Download, FileSpreadsheet, FileText, Presentation, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ChainBadge, clock } from "@/components/Bits";
import { ApiError, api, download, saveBlob } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime, timeOfDay } from "@/lib/format";
import type { Draft, DraftFigure, DraftStatus, DraftsResponse, RecordedResponse } from "@/lib/types_ext";
import { ROLE_TITLE, useAuth } from "@/store/auth";
import { Badge, Button, EmptyState, ErrorState, Input, PageHeader, Panel, PanelHeader, Skeleton, Tabs, useToast } from "@/ui";

const STATUS: Record<DraftStatus, { label: string; tone: "warning" | "success" | "danger" }> = {
  pending_signoff: { label: "pending sign-off", tone: "warning" },
  signed_off: { label: "signed off", tone: "success" },
  rejected: { label: "rejected", tone: "danger" },
};

const FORMATS = [
  { fmt: "docx" as const, label: "Word", icon: <FileText className="size-3.5" /> },
  { fmt: "xlsx" as const, label: "Excel", icon: <FileSpreadsheet className="size-3.5" /> },
  { fmt: "pptx" as const, label: "PowerPoint", icon: <Presentation className="size-3.5" /> },
];

/** Create a draft from a released answer and save the file. Shared with the chat. */
export async function exportAndDownload(responseId: string, fmt: "docx" | "xlsx" | "pptx"): Promise<{ draftId: string | null; openFlags: number }> {
  const out = await api.exportDeliverable(responseId, fmt);
  if (out.download) {
    const blob = await download(out.download);
    saveBlob(blob, out.path.split(/[\\/]/).pop() || `deliverable.${fmt}`);
  }
  return { draftId: out.draft?.draft_id ?? null, openFlags: out.draft?.open_flags ?? 0 };
}

export default function Review() {
  const { session, atLeast } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<"all" | "pending_signoff" | "signed_off" | "rejected">("all");
  const [data, setData] = useState<DraftsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await api.drafts());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const rows = useMemo(() => (data?.drafts ?? []).filter((d) => tab === "all" || d.status === tab).sort((a, b) => b.created - a.created), [data, tab]);
  const open = rows.find((d) => d.draft_id === openId) ?? (data?.drafts ?? []).find((d) => d.draft_id === openId) ?? null;

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!data || !session) return <Skeleton className="h-64 w-full" />;

  const s = data.summary;

  return (
    <div>
      <PageHeader
        title="Review"
        description={data.reviewer
          ? `Every draft on record. As ${ROLE_TITLE[session.role]} you can resolve flags and sign off — never past an open flag.`
          : "Your drafts. A manager signs them off once every flagged figure has been checked against its source."}
        actions={<ChainBadge chain={data.chain} label="draft log" />}
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <Tile label="Pending sign-off" value={s.pending_signoff ?? 0} tone="warning" />
        <Tile label="Signed off" value={s.signed_off ?? 0} tone="success" />
        <Tile label="Rejected" value={s.rejected ?? 0} tone="danger" />
      </div>

      <RecentAnswers onDone={() => void load()} />

      <Panel className="mt-4">
        <PanelHeader
          title="Drafts"
          description="Nothing is auto-approved: a draft is presented as final only after a person signs it off."
          actions={
            <Tabs value={tab} onChange={setTab} tabs={[
              { id: "all", label: "All" }, { id: "pending_signoff", label: "Pending" }, { id: "signed_off", label: "Signed" }, { id: "rejected", label: "Rejected" },
            ]} />
          }
        />
        {rows.length ? (
          <ul className="divide-y divide-border/60">
            {rows.map((d) => (
              <li key={d.draft_id}>
                <button type="button" onClick={() => setOpenId(openId === d.draft_id ? null : d.draft_id)} aria-expanded={openId === d.draft_id}
                        className={cn("flex w-full flex-wrap items-center gap-2 px-4 py-2.5 text-left hover:bg-slate-900/[0.03]", openId === d.draft_id && "bg-primary/[0.05]")}>
                  <KindIcon kind={d.kind} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-foreground">{d.title}</span>
                    <span className="block text-[0.7rem] text-muted-foreground">{d.owner} · {relativeTime(d.created)} · {d.figures.length} figure{d.figures.length === 1 ? "" : "s"}{d.classification ? ` · ${d.classification}` : ""}</span>
                  </span>
                  {d.open_flags ? <Badge tone="danger"><AlertTriangle className="size-2.5" /> {d.open_flags} open flag{d.open_flags === 1 ? "" : "s"}</Badge> : d.flagged ? <Badge tone="neutral">{d.flagged} resolved</Badge> : null}
                  <Badge tone={STATUS[d.status].tone}>{STATUS[d.status].label}</Badge>
                </button>
                {openId === d.draft_id && open ? (
                  <DraftDetail draft={open} reviewer={atLeast("manager")} onChange={(next) => {
                    setData((prev) => prev ? { ...prev, drafts: prev.drafts.map((x) => x.draft_id === next.draft_id ? next : x) } : prev);
                    void load();
                  }} toast={toast} />
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon={<ClipboardCheck className="size-6" />} title="No drafts" description="Export an answer as Word, Excel or PowerPoint and it appears here, pending sign-off." />
        )}
      </Panel>
    </div>
  );
}

function Tile({ label, value, tone }: { label: string; value: number; tone: "warning" | "success" | "danger" }) {
  const color = tone === "warning" ? "text-[color-mix(in_oklab,var(--warning),black_25%)]" : tone === "success" ? "text-[var(--success)]" : "text-[var(--danger)]";
  return (
    <Panel className="px-4 py-3">
      <p className="text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-2xl font-semibold tabular-nums", color)}>{value}</p>
    </Panel>
  );
}

function KindIcon({ kind }: { kind: string }) {
  if (kind === "xlsx") return <FileSpreadsheet className="size-4 text-[var(--success)]" />;
  if (kind === "pptx") return <Presentation className="size-4 text-[var(--warning)]" />;
  if (kind === "docx") return <FileText className="size-4 text-primary" />;
  return <ClipboardCheck className="size-4 text-muted-foreground" />;
}

// ---------------------------------------------------------------- recent answers -> export

function RecentAnswers({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [rows, setRows] = useState<RecordedResponse[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.recordedResponses(12).then(setRows).catch(() => setRows([]));
  }, []);

  const run = async (r: RecordedResponse, fmt: "docx" | "xlsx" | "pptx") => {
    setBusy(`${r.response_id}:${fmt}`);
    try {
      const out = await exportAndDownload(r.response_id, fmt);
      toast.push({ tone: "success", message: `Draft ${out.draftId ?? ""} created — pending sign-off`, detail: out.openFlags ? `${out.openFlags} figure(s) flagged for review.` : "No figure was flagged; a manager can sign it off." });
      onDone();
    } catch (err) {
      toast.push({ tone: "danger", message: "Export failed", detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel className="mt-4">
      <PanelHeader title="Recent answers" description="Turn a released answer into a real deliverable. The file carries its classification, every figure's provenance, the calculation steps and a pending-sign-off banner." />
      {rows === null ? <Skeleton className="m-4 h-16" /> : rows.length ? (
        <ul className="divide-y divide-border/60">
          {rows.map((r) => (
            <li key={r.response_id} className="flex flex-wrap items-center gap-2 px-4 py-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-foreground">{r.question || r.response_id}</span>
                <span className="block text-[0.68rem] text-muted-foreground">{r.task_type} · {r.status} · {r.evidence} citation{r.evidence === 1 ? "" : "s"} · {relativeTime(r.saved)}{r.owner ? ` · ${r.owner}` : ""}</span>
              </span>
              <span className="flex items-center gap-1">
                {FORMATS.map((f) => (
                  <Button key={f.fmt} size="sm" variant="outline" loading={busy === `${r.response_id}:${f.fmt}`} disabled={busy !== null} onClick={() => void run(r, f.fmt)} title={`Export as ${f.label}`}>
                    {f.icon} {f.label}
                  </Button>
                ))}
              </span>
            </li>
          ))}
        </ul>
      ) : <EmptyState title="No answers on record yet" description="Ask something first; answered questions can then be exported." />}
    </Panel>
  );
}

// ---------------------------------------------------------------- one draft

function DraftDetail({ draft, reviewer, onChange, toast }: { draft: Draft; reviewer: boolean; onChange: (d: Draft) => void; toast: ReturnType<typeof useToast> }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<DraftFigure[] | null>(null);

  const act = async (label: string, fn: () => Promise<Draft>) => {
    setBusy(label);
    setBlocked(null);
    try {
      onChange(await fn());
      toast.push({ tone: "success", message: label === "signoff" ? "Signed off" : label === "reject" ? "Rejected" : "Resolved" });
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : null;
      const flags = detail && typeof detail === "object" && Array.isArray((detail as { open_flags?: unknown }).open_flags)
        ? ((detail as { open_flags: DraftFigure[] }).open_flags) : null;
      if (err instanceof ApiError && err.status === 409 && flags) {
        setBlocked(flags);
      } else {
        toast.push({ tone: "danger", message: "Refused", detail: err instanceof ApiError ? err.message : String(err) });
      }
    } finally {
      setBusy(null);
    }
  };

  const get = async () => {
    try {
      saveBlob(await download(`/deliverables/${draft.draft_id}/download`), (draft.path ?? "").split(/[\\/]/).pop() || `${draft.draft_id}.${draft.kind}`);
    } catch (err) {
      toast.push({ tone: "danger", message: "Download failed", detail: err instanceof ApiError ? err.message : String(err) });
    }
  };

  const pending = draft.status === "pending_signoff";

  return (
    <div className="space-y-3 border-t border-border/60 bg-white/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        {draft.path ? <Button size="sm" variant="outline" onClick={() => void get()}><Download className="size-3.5" /> Download {draft.kind}</Button> : null}
        <span className="font-mono text-[0.68rem] text-muted-foreground">{draft.draft_id}{draft.sha256 ? ` · sha256 ${draft.sha256.slice(0, 12)}…` : ""}</span>
        {draft.signed_off_by ? <Badge tone="success"><CheckCircle2 className="size-2.5" /> signed off by {draft.signed_off_by} {draft.signed_off_at ? timeOfDay(draft.signed_off_at) : ""}</Badge> : null}
        {draft.rejected_by ? <Badge tone="danger"><XCircle className="size-2.5" /> rejected by {draft.rejected_by}</Badge> : null}
      </div>

      <div className="thin-scroll overflow-x-auto rounded-lg border border-border/70">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-slate-900/[0.04] text-left text-[0.65rem] uppercase tracking-wide text-slate-600">
              <th className="px-2 py-1.5">Figure</th><th className="px-2 py-1.5">Value</th><th className="px-2 py-1.5">Confidence</th><th className="px-2 py-1.5">Source (provenance)</th><th className="px-2 py-1.5">Review</th>
            </tr>
          </thead>
          <tbody>
            {draft.figures.map((f) => (
              <FigureRow key={f.figure_id} f={f} canResolve={reviewer && pending} busy={busy} onResolve={(action, value, n) => act(`resolve:${f.figure_id}`, () => api.resolveFigure(draft.draft_id, f.figure_id, { action, corrected_value: value ?? null, note: n }))} />
            ))}
            {!draft.figures.length ? <tr><td colSpan={5} className="px-2 py-3 text-center text-muted-foreground">No figures were extracted from this draft.</td></tr> : null}
          </tbody>
        </table>
      </div>

      {blocked ? (
        <div role="alert" className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/[0.06] px-3 py-2 text-xs">
          <p className="font-medium text-[var(--danger)]">Sign-off blocked: {blocked.length} flagged figure{blocked.length === 1 ? " is" : "s are"} unresolved</p>
          <ul className="mt-1 space-y-0.5 text-slate-700">{blocked.map((f) => <li key={f.figure_id}>{f.label} = {f.value} {f.unit ?? ""} — {f.flag_reason}</li>)}</ul>
        </div>
      ) : null}

      {reviewer && pending ? (
        <div className="flex flex-wrap items-center gap-2">
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reviewer note (optional)" className="h-8 max-w-sm text-xs" aria-label="Reviewer note" />
          <Button size="sm" variant="primary" loading={busy === "signoff"} disabled={busy !== null}
                  onClick={() => void act("signoff", () => api.signoffDraft(draft.draft_id, note))}
                  title={draft.open_flags ? `${draft.open_flags} flag(s) still open — the server will refuse` : "Sign off"}>
            <CheckCircle2 className="size-3.5" /> Sign off{draft.open_flags ? ` (${draft.open_flags} open)` : ""}
          </Button>
          <Button size="sm" variant="danger" loading={busy === "reject"} disabled={busy !== null} onClick={() => void act("reject", () => api.rejectDraft(draft.draft_id, note))}>
            <XCircle className="size-3.5" /> Reject
          </Button>
        </div>
      ) : pending ? (
        <p className="text-[0.7rem] text-muted-foreground">Waiting for a manager: {draft.open_flags ? `${draft.open_flags} figure(s) need checking against their source first.` : "no figure is flagged."}</p>
      ) : null}

      {draft.history.length ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">History ({draft.history.length})</summary>
          <ul className="mt-1 space-y-0.5 text-[0.7rem] text-slate-600">
            {draft.history.map((h, i) => <li key={i}><span className="font-mono text-muted-foreground">{clock(h.ts)}</span> {h.event} · {h.by}{h.detail ? ` — ${h.detail}` : ""}</li>)}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function FigureRow({ f, canResolve, busy, onResolve }: { f: DraftFigure; canResolve: boolean; busy: string | null; onResolve: (action: "accepted" | "corrected" | "removed", value: string | null, note: string) => void }) {
  const [value, setValue] = useState(f.value);
  const [note, setNote] = useState("");
  const open = f.flagged && !f.resolution;
  const tone = f.confidence >= 0.8 ? "success" : f.confidence >= 0.6 ? "info" : "danger";
  return (
    <tr className={cn("border-t border-border/60 align-top", open && "bg-[var(--danger)]/[0.04]")}>
      <td className="px-2 py-1.5">
        <p className="font-medium text-foreground">{f.label}</p>
        {open ? <p className="text-[0.65rem] text-[var(--danger)]"><AlertTriangle className="mr-0.5 inline size-2.5" />{f.flag_reason}</p> : f.resolution ? <p className="text-[0.65rem] text-muted-foreground">{f.resolution.action} by {f.resolution.by}{f.resolution.corrected_value ? ` → ${f.resolution.corrected_value}` : ""}{f.resolution.note ? ` — ${f.resolution.note}` : ""}</p> : null}
      </td>
      <td className="whitespace-nowrap px-2 py-1.5 font-mono">{f.value} {f.unit ?? ""}</td>
      <td className="px-2 py-1.5"><Badge tone={tone}>{f.confidence.toFixed(2)}</Badge></td>
      <td className="px-2 py-1.5">
        <p className="text-slate-700">{f.source.document_id ?? "—"}{f.source.page != null ? <span className="text-muted-foreground"> · p.{f.source.page}</span> : null}{f.source.source_kind ? <span className="text-muted-foreground"> · {f.source.source_kind}</span> : null}</p>
        {f.source.text ? <p className="line-clamp-2 text-[0.68rem] text-muted-foreground" title={f.source.text}>{f.source.text}</p> : null}
      </td>
      <td className="px-2 py-1.5">
        {open && canResolve ? (
          <div className="flex flex-col gap-1">
            <div className="flex gap-1">
              <Input value={value} onChange={(e) => setValue(e.target.value)} className="h-7 w-24 font-mono text-[0.7rem]" aria-label="Corrected value" />
              <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="note" className="h-7 w-28 text-[0.7rem]" aria-label="Resolution note" />
            </div>
            <div className="flex gap-1">
              <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => onResolve("accepted", null, note)}>Accept</Button>
              <Button size="sm" variant="outline" disabled={busy !== null || !value.trim()} onClick={() => onResolve("corrected", value.trim(), note)}>Correct</Button>
              <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => onResolve("removed", null, note)}>Remove</Button>
            </div>
          </div>
        ) : open ? <span className="text-[0.68rem] text-muted-foreground">awaiting a reviewer</span> : f.flagged ? <Badge tone="success">resolved</Badge> : <span className="text-[0.68rem] text-muted-foreground">backed by evidence</span>}
      </td>
    </tr>
  );
}

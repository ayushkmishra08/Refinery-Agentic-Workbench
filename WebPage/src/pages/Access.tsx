/**
 * Access requests — the one door through the ceiling, from both sides.
 *
 * **My requests** is what an engineer sees: the questions that needed material above their
 * clearance, how far each has got, and — once approved — the key, which they carry back to the
 * chat and re-ask with. The key is shown once and cannot be recovered, so it is copyable and
 * clearly marked as such.
 *
 * **Awaiting my approval** is what a manager or administrator sees. The important detail is the
 * preview: the approver reads the actual passages that would be released *before* deciding. An
 * approval given blind is not a control, so the preview is not collapsed by default on the item
 * being decided.
 *
 * The requester never sees record contents here — only counts, document names and opaque ids.
 */
import {
  CheckCircle2, ClipboardCheck, Clock, FileText, Inbox, KeyRound, ShieldAlert, XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime, timeOfDay } from "@/lib/format";
import type { AccessRequest, ApprovalResult } from "@/lib/types";
import { ROLE_TITLE, useAuth } from "@/store/auth";
import {
  Badge, Button, CopyButton, Dialog, EmptyState, ErrorState, PageHeader, Panel, PanelHeader,
  Skeleton, Tabs, Textarea, useToast,
} from "@/ui";

const STATUS_TONE = {
  pending: "warning", approved: "success", denied: "danger", expired: "neutral",
} as const;

function StatusBadge({ status }: { status: AccessRequest["status"] }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status}</Badge>;
}

function ScopeSummary({ request }: { request: AccessRequest }) {
  const counts = request.scope.record_ids.reduce<Record<string, number>>((acc, id) => {
    const kind = id.split(":")[0];
    acc[kind] = (acc[kind] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {Object.entries(counts).map(([kind, n]) => (
        <Badge key={kind} tone="neutral">{n} {kind}{n === 1 ? "" : "s"}</Badge>
      ))}
      {request.scope.document_ids.map((d) => (
        <Badge key={d} tone="warning"><FileText className="size-2.5" /> {d}</Badge>
      ))}
      {request.scope.truncated ? <Badge tone="neutral">truncated</Badge> : null}
    </div>
  );
}

export default function Access() {
  const { atLeast } = useAuth();
  const toast = useToast();
  const canApprove = atLeast("manager");

  const [tab, setTab] = useState<"mine" | "queue">("mine");
  const [mine, setMine] = useState<AccessRequest[] | null>(null);
  const [queue, setQueue] = useState<AccessRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [issued, setIssued] = useState<ApprovalResult | null>(null);
  const [deciding, setDeciding] = useState<AccessRequest | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [m, q] = await Promise.all([
        api.myAccessRequests(),
        canApprove ? api.approvals(false) : Promise.resolve([] as AccessRequest[]),
      ]);
      setMine(m);
      setQueue(q);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setMine([]);
      setQueue([]);
    }
  }, [canApprove]);

  useEffect(() => { void load(); }, [load]);

  const decide = useCallback(
    async (request: AccessRequest, verdict: "approve" | "deny") => {
      setBusy(true);
      try {
        const out = verdict === "approve"
          ? await api.approve(request.request_id, note)
          : await api.deny(request.request_id, note);
        if (verdict === "approve" && out.key) setIssued(out);
        else toast.push({ tone: "info", message: `${request.request_id} denied` });
        setDeciding(null);
        setNote("");
        await load();
      } catch (err) {
        toast.push({ tone: "danger", message: "Decision failed", detail: err instanceof ApiError ? err.message : String(err) });
      } finally {
        setBusy(false);
      }
    },
    [load, note, toast],
  );

  const pendingMine = (mine ?? []).filter((r) => r.status === "pending").length;

  return (
    <div>
      <PageHeader
        title="Access requests"
        description="When a question needs material above your clearance, the workstation raises a request instead of a dead end. Approval releases a named list of records — once, for that question, for you."
        actions={<Button size="sm" variant="outline" onClick={() => void load()}>Refresh</Button>}
      />

      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}

      {canApprove ? (
        <Tabs
          className="mb-4 max-w-md"
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "mine", label: "My requests", badge: pendingMine ? <Badge tone="warning">{pendingMine}</Badge> : undefined },
            { id: "queue", label: "Awaiting my approval", badge: queue?.length ? <Badge tone="warning">{queue.length}</Badge> : undefined },
          ]}
        />
      ) : null}

      {tab === "mine" || !canApprove ? (
        <MyRequests requests={mine} />
      ) : (
        <ApprovalQueue
          requests={queue}
          onDecide={(r) => { setDeciding(r); setNote(""); }}
        />
      )}

      {/* ---------------------------------------------------------------- decide */}
      <Dialog
        wide
        open={!!deciding}
        onClose={() => setDeciding(null)}
        title={deciding ? `Decide ${deciding.request_id}` : ""}
        description="You are cleared for this material, so you can read it in full. Decide whether these words may be released."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeciding(null)} disabled={busy}>Cancel</Button>
            <Button variant="danger" loading={busy} onClick={() => deciding && void decide(deciding, "deny")}>
              <XCircle className="size-3.5" /> Deny
            </Button>
            <Button variant="primary" loading={busy} onClick={() => deciding && void decide(deciding, "approve")}>
              <CheckCircle2 className="size-3.5" /> Approve and issue key
            </Button>
          </>
        }
      >
        {deciding ? (
          <div className="space-y-4">
            <div>
              <p className="text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">Requested by</p>
              <p className="text-sm text-foreground">
                {deciding.requester} ({ROLE_TITLE[deciding.requester_role]})
                <span className="ml-2 text-xs text-muted-foreground">{timeOfDay(deciding.created)}</span>
              </p>
            </div>
            <div>
              <p className="text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">Question</p>
              <p className="text-sm text-foreground">{deciding.question}</p>
            </div>
            <div>
              <p className="mb-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
                Scope — {deciding.scope.record_ids.length} records
              </p>
              <ScopeSummary request={deciding} />
            </div>

            <div>
              <p className="mb-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
                What would be released
              </p>
              {deciding.preview?.length ? (
                <ul className="space-y-1.5">
                  {deciding.preview.map((row) => (
                    <li key={row.id} className="rounded-lg border border-border/70 bg-white/60 px-3 py-2">
                      <p className="flex flex-wrap items-center gap-1.5 text-[0.68rem] text-muted-foreground">
                        <Badge tone="neutral">{row.kind}</Badge>
                        {row.document ? <span>{row.document}</span> : null}
                        {row.page ? <span>p.{row.page}</span> : null}
                      </p>
                      <p className="mt-1 text-[0.8rem] leading-snug text-slate-700">{row.text ?? row.note}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">No preview available for this request.</p>
              )}
            </div>

            <Textarea
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Why are you approving or denying this? (recorded in the audit trail)"
            />
          </div>
        ) : null}
      </Dialog>

      {/* ---------------------------------------------------------------- the key */}
      <Dialog
        open={!!issued}
        onClose={() => setIssued(null)}
        title="Access key issued"
        description="Give this to the requester. It is shown once and cannot be recovered."
        footer={<Button variant="primary" onClick={() => setIssued(null)}>Done</Button>}
      >
        {issued ? (
          <div className="space-y-3">
            <div className="rounded-lg border border-[var(--warning)]/35 bg-[var(--warning)]/[0.07] px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-[color-mix(in_oklab,var(--warning),black_30%)]">
                <ShieldAlert className="size-3.5" /> Shown once
              </p>
              <p className="mt-1 text-[0.72rem] text-slate-700">
                Close this dialog and the key is gone. It opens the listed records only, for{" "}
                {issued.request.requester}, for that exact question, once.
              </p>
            </div>
            <div className="rounded-lg border border-border bg-white/80 p-3">
              <p className="break-all font-mono text-[0.75rem] leading-relaxed text-foreground">{issued.key}</p>
            </div>
            <div className="flex items-center justify-between gap-2">
              <p className="text-[0.7rem] text-muted-foreground">
                {issued.grant?.record_count ?? issued.request.scope.record_ids.length} records ·{" "}
                {issued.grant?.expires ? `expires ${relativeTime(issued.grant.expires)}` : "short-lived"} ·{" "}
                {issued.grant?.max_uses ?? 1} use
              </p>
              <CopyButton text={issued.key ?? ""} label="Copy key" />
            </div>
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------- panes

function MyRequests({ requests }: { requests: AccessRequest[] | null }) {
  if (!requests) return <Skeleton className="h-40 w-full" />;
  if (!requests.length) {
    return (
      <Panel>
        <EmptyState
          icon={<Inbox className="size-7" />}
          title="No access requests"
          description="Ask a question that needs material above your clearance and one is raised for you automatically."
        />
      </Panel>
    );
  }
  return (
    <div className="space-y-2.5">
      {requests.map((r) => (
        <Panel key={r.request_id} className="px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs font-semibold text-foreground">{r.request_id}</span>
                <StatusBadge status={r.status} />
                <span className="text-[0.68rem] text-muted-foreground">
                  raised {relativeTime(r.created)} · with {ROLE_TITLE[r.approver_role]}
                </span>
              </div>
              <p className="mt-1.5 text-sm text-slate-700">{r.question}</p>
              <div className="mt-2"><ScopeSummary request={r} /></div>
              {r.note ? <p className="mt-2 text-xs text-muted-foreground">Note: {r.note}</p> : null}
            </div>
            <div className="shrink-0 text-right">
              {r.status === "pending" ? (
                <p className="flex items-center gap-1 text-[0.68rem] text-muted-foreground">
                  <Clock className="size-3" /> expires {relativeTime(r.expires)}
                </p>
              ) : r.status === "approved" ? (
                <p className="flex items-center gap-1 text-[0.68rem] text-[var(--success)]">
                  <KeyRound className="size-3" /> key issued
                </p>
              ) : null}
            </div>
          </div>
          {r.status === "approved" ? (
            <p className="mt-2 rounded-md bg-[var(--success)]/[0.08] px-2.5 py-1.5 text-[0.7rem] text-slate-700">
              Your approver issued a one-time key. Paste it under the answer in the chat and re-ask
              this exact question — the key does not apply to any other.
            </p>
          ) : null}
        </Panel>
      ))}
    </div>
  );
}

function ApprovalQueue({
  requests, onDecide,
}: { requests: AccessRequest[] | null; onDecide: (r: AccessRequest) => void }) {
  if (!requests) return <Skeleton className="h-40 w-full" />;
  if (!requests.length) {
    return (
      <Panel>
        <EmptyState
          icon={<ClipboardCheck className="size-7" />}
          title="Nothing awaiting your approval"
          description="Requests are routed to the lowest role that can release the material, so not everything reaches an administrator."
        />
      </Panel>
    );
  }
  return (
    <div className="space-y-2.5">
      {requests.map((r) => (
        <Panel key={r.request_id}>
          <PanelHeader
            title={
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{r.request_id}</span>
                <StatusBadge status={r.status} />
              </span>
            }
            description={`${r.requester} (${ROLE_TITLE[r.requester_role]}) · raised ${relativeTime(r.created)}`}
            actions={<Button size="sm" variant="primary" onClick={() => onDecide(r)}>Review</Button>}
          />
          <div className="px-4 py-3">
            <p className="text-sm text-slate-700">{r.question}</p>
            <div className="mt-2"><ScopeSummary request={r} /></div>
            {r.preview?.length ? (
              <p className={cn("mt-2 text-[0.7rem] text-muted-foreground")}>
                {r.preview.length} of {r.scope.record_ids.length} records previewed — open Review to read them.
              </p>
            ) : null}
          </div>
        </Panel>
      ))}
    </div>
  );
}

/**
 * What the people you supervise have been asking.
 *
 * A manager sees engineers' conversations; an administrator sees managers' and engineers'. Never a
 * peer's, never a superior's — the rule is *strictly below*, and that is also what makes the page
 * safe to show without a second filter: anything released to a lower rank was drawn from documents
 * that rank may read, which the reader's own clearance already covers. Each conversation opened
 * here is written to the security audit under the reader's name.
 */
import { ChevronDown, ChevronRight, Eye, Paperclip, ScrollText } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Markdown } from "@/components/Markdown";
import { ClassificationStrip } from "@/components/SecurityBanner";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime, timeOfDay } from "@/lib/format";
import type { Role, SecurityEnvelope, SessionSnapshot, SupervisedConversation } from "@/lib/types";
import { ROLE_TITLE, useAuth } from "@/store/auth";
import { Badge, EmptyState, ErrorState, Input, PageHeader, Panel, PanelHeader, Skeleton, Spinner } from "@/ui";

export default function Logs() {
  const { session } = useAuth();
  const [rows, setRows] = useState<SupervisedConversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [openKey, setOpenKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRows(await api.supervisedConversations());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (rows ?? []).filter((r) => !needle || `${r.owner} ${r.title}`.toLowerCase().includes(needle));
  }, [rows, query]);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!rows || !session) return <Skeleton className="h-64 w-full" />;

  const who = session.role === "admin" ? "managers and engineers" : "engineers";

  return (
    <div>
      <PageHeader
        title="Logs"
        description={`Recent conversations of the ${who} you supervise. Opening one is recorded in the security audit under your name.`}
        actions={
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter by person or question…"
                 className="h-8 w-56 text-xs" aria-label="Filter conversations" />
        }
      />

      <Panel>
        <PanelHeader
          title="Conversations"
          description={`${rows.length} on record${query ? `, ${filtered.length} matching` : ""}. Newest first.`}
        />
        {filtered.length === 0 ? (
          <EmptyState title="Nothing to show" description={rows.length ? "No conversation matches that filter." : `Nobody below your rank has asked anything yet.`} />
        ) : (
          <ul className="divide-y divide-border/60">
            {filtered.map((r) => {
              const key = `${r.owner}/${r.session_id}`;
              return (
                <ConversationRow key={key} row={r} open={openKey === key}
                                 onToggle={() => setOpenKey(openKey === key ? null : key)} />
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function ConversationRow({ row, open, onToggle }: { row: SupervisedConversation; open: boolean; onToggle: () => void }) {
  const [snap, setSnap] = useState<SessionSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || snap) return;
    let cancelled = false;
    setLoading(true);
    api.supervisedConversation(row.owner, row.session_id)
      .then((s) => { if (!cancelled) setSnap(s); })
      .catch((err) => { if (!cancelled) setError(err instanceof ApiError ? err.message : String(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, snap, row.owner, row.session_id]);

  return (
    <li className="px-4 py-3">
      <button type="button" onClick={onToggle} className="flex w-full items-start gap-3 text-left" aria-expanded={open}>
        <span className="mt-0.5 text-muted-foreground">
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-foreground">{row.owner}</span>
            <Badge tone="neutral">{ROLE_TITLE[(row.owner_role || "user") as Role]}</Badge>
            {row.attachments.length ? (
              <Badge tone="primary" title={row.attachments.join(", ")}><Paperclip className="size-2.5" /> {row.attachments.length}</Badge>
            ) : null}
          </span>
          <span className="mt-0.5 block truncate text-xs text-slate-700">{row.title}</span>
          <span className="mt-0.5 block text-[0.7rem] text-muted-foreground">
            {row.updated ? relativeTime(row.updated) : ""} · {row.turns} {row.turns === 1 ? "turn" : "turns"}
            {row.last_status && row.last_status !== "answered" ? ` · last: ${row.last_status}` : ""}
          </span>
        </span>
        <Eye className="mt-1 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      </button>

      {open ? (
        <div className="mt-3 space-y-3 border-l-2 border-primary/20 pl-4">
          {loading ? <Spinner className="size-4 text-primary" /> : null}
          {error ? <p className="text-xs text-[var(--danger)]">{error}</p> : null}
          {snap?.turns.map((t, i) => (
            <div key={i} className="space-y-1.5">
              <p className="text-[0.68rem] text-muted-foreground">{t.ts ? timeOfDay(t.ts) : ""}</p>
              <p className="inline-block rounded-2xl rounded-br-md bg-primary px-3 py-1.5 text-xs text-primary-foreground">{t.request}</p>
              <div className={cn("rounded-lg border border-border/70 bg-white/60 px-3 py-2 text-xs")}>
                <Markdown>{t.answer_markdown || t.answer_preview || "*(no answer recorded)*"}</Markdown>
                {t.security ? <div className="mt-2"><ClassificationStrip security={t.security as SecurityEnvelope} /></div> : null}
              </div>
            </div>
          ))}
          {snap && snap.turns.length === 0 ? (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><ScrollText className="size-3.5" /> Nothing asked yet.</p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

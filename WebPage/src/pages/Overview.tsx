/**
 * The workspace overview.
 *
 * Every number here is counted after the access filter, so two roles looking at this page see
 * different totals. That is deliberate: a statistics panel that reported the whole corpus would
 * tell a user exactly how much sits in the tier above them, which is the thing the rest of the
 * system is built to withhold.
 *
 * Nothing about the host machine is shown — no GPU, no memory, no disk. This describes the
 * corpus, the queue and the answering configuration, which is what an engineer can act on.
 */
import { ClipboardCheck, FileText, Inbox, KeyRound, Layers, MessageSquare, Radar, ShieldCheck, Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { ApiError, api } from "@/lib/api";
import type { Health, WorkspaceStats } from "@/lib/types";
import { ROLE_TITLE, TAG_TONE, useAuth } from "@/store/auth";
import { Badge, ErrorState, PageHeader, Panel, PanelHeader, Skeleton, Stat } from "@/ui";
import { LinkButton } from "@/components/LinkButton";

export default function Overview() {
  const { session } = useAuth();
  const [stats, setStats] = useState<WorkspaceStats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const [s, h] = await Promise.all([api.stats(), api.health()]);
      setStats(s);
      setHealth(h);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  useEffect(() => { void load(); }, []);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!stats || !health) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
      </div>
    );
  }

  const { documents, knowledge, escalation, answers } = stats;

  return (
    <div>
      <PageHeader
        title={`Good to see you, ${session?.username ?? "engineer"}`}
        description={`Signed in as ${ROLE_TITLE[stats.role]}. Everything below is counted over the documents your role may read.`}
        actions={<LinkButton to="/chat" variant="primary">Ask a question</LinkButton>}
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Readable documents" value={documents.readable} tone="primary"
              hint={documents.withheld ? `${documents.withheld} withheld by classification` : "your full clearance"} />
        <Stat label="Pages in scope" value={documents.pages.toLocaleString()} hint="across your readable documents" />
        <Stat label="Tagged equipment" value={knowledge.entities.toLocaleString()} hint={`${knowledge.claims.toLocaleString()} documented values`} />
        <Stat label="Procedures" value={knowledge.procedures.toLocaleString()} hint={`${knowledge.chunks.toLocaleString()} indexed passages`} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Panel>
          <PanelHeader
            title="Your clearance"
            description="A role reads a document when its level reaches the document's tag — its own tier in full, nothing above it."
            actions={<LinkButton to="/documents" variant="ghost">All documents</LinkButton>}
          />
          <div className="space-y-3 px-4 py-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <ShieldCheck className="size-4 text-primary" />
              <span className="text-sm font-medium text-foreground">{ROLE_TITLE[stats.role]}</span>
              {(session?.readableTags ?? []).map((t) => (
                <Badge key={t} tone={TAG_TONE[t]}>{t}</Badge>
              ))}
            </div>

            <div className="grid gap-2 sm:grid-cols-3">
              {Object.entries(documents.by_tag).map(([tag, n]) => (
                <div key={tag} className="rounded-lg border border-border/70 bg-white/55 px-3 py-2">
                  <p className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">{tag}</p>
                  <p className="mt-0.5 text-lg font-semibold tabular-nums text-foreground">{n}</p>
                </div>
              ))}
            </div>

            {documents.withheld ? (
              <p className="rounded-lg bg-[var(--warning)]/[0.07] px-3 py-2 text-xs text-slate-700">
                {documents.withheld} document{documents.withheld === 1 ? " sits" : "s sit"} above your clearance.
                Ask anyway — you will be told how much bears on your question, and a request is raised for you.
              </p>
            ) : (
              <p className="rounded-lg bg-[var(--success)]/[0.07] px-3 py-2 text-xs text-slate-700">
                You are cleared for every document loaded here.
              </p>
            )}
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel>
            <PanelHeader title="Access requests" actions={<LinkButton to="/access" variant="ghost">Open</LinkButton>} />
            <div className="grid grid-cols-2 gap-px bg-border/60">
              <div className="bg-[var(--card)] px-4 py-3">
                <p className="flex items-center gap-1.5 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
                  <Inbox className="size-3" /> Mine open
                </p>
                <p className="mt-1 text-xl font-semibold tabular-nums text-foreground">{escalation.my_open_requests}</p>
              </div>
              <div className="bg-[var(--card)] px-4 py-3">
                <p className="flex items-center gap-1.5 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
                  <KeyRound className="size-3" /> To approve
                </p>
                <p className="mt-1 text-xl font-semibold tabular-nums text-foreground">{escalation.awaiting_my_approval}</p>
              </div>
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="How answers are produced" />
            <dl className="divide-y divide-border/60 px-4 text-xs">
              {[
                ["Effort", answers.effort],
                ["Composer", answers.composed ? "model-written prose" : "deterministic"],
                ["Model", answers.llm_available ? (answers.llm ?? "—") : `${answers.llm ?? "—"} (offline)`],
                ["Knowledge backend", health.backend],
                ["Access control", health.access_control ? "enforced" : "off"],
              ].map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-3 py-2">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="truncate font-medium text-foreground">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </Panel>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Link to="/sovereignty" className="group">
          <Stat label="Air gap" value={health.airgap_enforced ? "enforced" : "off"} tone={health.airgap_enforced ? "primary" : "warning"}
                hint={health.network_monitor ? `${health.workbench_external_connections ?? 0} external connection${health.workbench_external_connections === 1 ? "" : "s"} by the workbench` : "monitor not running"} />
        </Link>
        <Link to="/models" className="group">
          <Stat label="Model routing" value={health.routing ? "on" : "off"} hint={health.resident_model ? `resident: ${health.resident_model}` : `default: ${health.llm}`} />
        </Link>
        <Link to="/vault" className="group">
          <Stat label="Vault" value={health.vault ? "on" : "sealed only"} hint={health.vault ? "branches decrypt per session" : "plaintext cache in use"} />
        </Link>
        <Link to="/tools" className="group">
          <Stat label="Sandbox" value={health.sandbox ? "ready" : "off"} hint="no network, bounded, ephemeral" />
        </Link>
        <Link to="/tools" className="group">
          <Stat label="Local tools" value={health.tools ?? 0} hint="files, code, spreadsheets, search, OCR…" />
        </Link>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <QuickLink to="/chat" icon={<MessageSquare className="size-4" />} title="Ask a question"
                   body="Values, procedures, troubleshooting, limits, safety — answered from the documents you may read." />
        <QuickLink to="/documents" icon={<FileText className="size-4" />} title="Documents"
                   body="What is loaded, how each is classified, and which role opens it." />
        <QuickLink to="/knowledge" icon={<Layers className="size-4" />} title="Knowledge layer"
                   body="What the workbench extracted: equipment, values, relationships, procedures." />
        <QuickLink to="/tools" icon={<Wrench className="size-4" />} title="Tools & sandbox"
                   body="Run named local tools, an agent loop over them, or code in a network-less sandbox." />
        <QuickLink to="/review" icon={<ClipboardCheck className="size-4" />} title="Review"
                   body="Word, Excel and PowerPoint drafts with provenance per figure, pending human sign-off." />
        <QuickLink to="/sovereignty" icon={<Radar className="size-4" />} title="Sovereignty"
                   body="Live network monitor, egress guard and tamper-evident audit chains." />
      </div>

    </div>
  );
}

function QuickLink({ to, icon, title, body }: { to: string; icon: React.ReactNode; title: string; body: string }) {
  return (
    <Link to={to} className="group">
      <Panel className="h-full px-4 py-3.5 transition-colors group-hover:border-primary/40">
        <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <span className="text-primary">{icon}</span>{title}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{body}</p>
      </Panel>
    </Link>
  );
}


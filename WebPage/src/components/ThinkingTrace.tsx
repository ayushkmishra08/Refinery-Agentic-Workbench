/**
 * The agents' reasoning, folded away.
 *
 * While a question runs this is a single line — "Thinking… · Specialist Retrieval" — with the
 * current phase beside it. Click it and the whole trace unfolds: every phase, every agent, what
 * it reasoned about, which model it used and what it decided. When the answer lands the line
 * becomes "Thought for 18s · 9 agents", still expandable.
 *
 * The reasoning text is not written here. It arrives on the progress stream in the event's
 * `thinking` field, composed by the workbench's narration module, so the terminal, this panel and
 * the saved JSON trace all say exactly the same thing.
 */
import { ChevronRight, Cpu, Loader2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import { ms } from "@/lib/format";
import type { ProgressEvent } from "@/lib/types";

export interface TraceAgent {
  key: string;
  name: string;
  stepId: string;
  goal: string;
  approach: string;
  thinking: string;
  decision: string;
  model: string | null;
  durationMs: number;
  evidence: number;
  llmCalls: number;
  ok: boolean;
  done: boolean;
}

export interface TracePhase {
  name: string;
  agents: TraceAgent[];
  done: boolean;
}

const PHASE_TITLES: Record<string, string> = {
  "0/1 Understanding": "Understanding",
  "2 Specialist retrieval": "Specialist retrieval",
  "3 Planning": "Planning",
  "4 Execution": "Execution",
  "5 Governance": "Governance",
};

/** Fold the progress stream into phases and agents. Pure, so it is easy to reason about. */
export function foldTrace(events: ProgressEvent[]): TracePhase[] {
  const phases: TracePhase[] = [];
  const openAgent = (phase: TracePhase, ev: ProgressEvent): TraceAgent => {
    const key = `${ev.agent ?? "agent"}::${ev.step_id ?? ""}`;
    const existing = phase.agents.find((a) => a.key === key && !a.done);
    if (existing) return existing;
    const created: TraceAgent = {
      key, name: ev.agent ?? "agent", stepId: ev.step_id ?? "",
      goal: "", approach: "", thinking: "", decision: "", model: null,
      durationMs: 0, evidence: 0, llmCalls: 0, ok: true, done: false,
    };
    phase.agents.push(created);
    return created;
  };

  for (const ev of events) {
    if (ev.event === "phase_started") {
      phases.push({ name: ev.phase || ev.message, agents: [], done: false });
      continue;
    }
    if (ev.event === "phase_finished") {
      const phase = phases.find((p) => p.name === (ev.phase || ev.message));
      if (phase) phase.done = true;
      continue;
    }
    if (!phases.length) phases.push({ name: ev.phase || "Understanding", agents: [], done: false });
    const phase = phases.find((p) => p.name === ev.phase) ?? phases[phases.length - 1];

    if (ev.event === "agent_started") {
      const agent = openAgent(phase, ev);
      agent.goal = ev.message;
      agent.approach = ev.thinking ?? "";
    } else if (ev.event === "agent_finished") {
      const agent = openAgent(phase, ev);
      const data = (ev.data ?? {}) as Record<string, number | boolean | undefined>;
      agent.thinking = ev.thinking ?? "";
      agent.decision = ev.decision || ev.message;
      agent.model = ev.model ?? null;
      agent.durationMs = Number(data.duration_ms ?? 0);
      agent.evidence = Number(data.evidence ?? 0);
      agent.llmCalls = Number(data.llm_calls ?? 0);
      agent.ok = data.ok !== false;
      agent.done = true;
    } else if (ev.event === "llm_call") {
      const agent = phase.agents.find((a) => a.name === ev.agent && !a.done);
      if (agent) agent.model = ev.model ?? agent.model;
    }
  }
  return phases;
}

function AgentRow({ agent }: { agent: TraceAgent }) {
  const lines = (agent.thinking || agent.approach || "").split("\n").filter((l) => l.trim());
  return (
    <li className="relative pl-5">
      <span
        aria-hidden
        className={cn(
          "absolute left-0 top-[0.45rem] size-2 rounded-full ring-2 ring-white",
          !agent.done ? "animate-pulse bg-primary" : agent.ok ? "bg-[var(--success)]" : "bg-[var(--danger)]",
        )}
      />
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-mono text-[0.72rem] font-semibold text-foreground">{agent.name}</span>
        {agent.stepId && agent.stepId !== agent.name ? (
          <span className="font-mono text-[0.68rem] text-muted-foreground">· {agent.stepId}</span>
        ) : null}
        {agent.model ? (
          <span className="inline-flex items-center gap-1 rounded bg-[var(--info)]/10 px-1.5 py-px text-[0.62rem] font-medium text-[var(--info)]">
            <Cpu className="size-2.5" /> {agent.model}
          </span>
        ) : null}
        {agent.done ? <span className="text-[0.65rem] tabular-nums text-muted-foreground">{ms(agent.durationMs)}</span> : null}
      </div>

      {agent.goal ? <p className="mt-0.5 text-[0.72rem] text-muted-foreground">{agent.goal}</p> : null}

      {lines.length ? (
        <ul className="mt-1 space-y-0.5 border-l border-border/70 pl-2.5">
          {lines.map((line, i) => (
            <li key={i} className="text-[0.72rem] leading-snug text-slate-600">{line}</li>
          ))}
        </ul>
      ) : null}

      {agent.done && agent.decision ? (
        <p className={cn("mt-1 text-[0.72rem] font-medium", agent.ok ? "text-foreground" : "text-[var(--danger)]")}>
          → {agent.decision}
        </p>
      ) : null}
    </li>
  );
}

export function ThinkingTrace({
  events, running, elapsedMs, defaultOpen = false, label,
}: {
  events: ProgressEvent[]; running: boolean; elapsedMs: number;
  defaultOpen?: boolean; label?: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const phases = useMemo(() => foldTrace(events), [events]);
  const bodyRef = useRef<HTMLDivElement>(null);

  const currentPhase = phases.length ? phases[phases.length - 1] : null;
  const agentCount = phases.reduce((n, p) => n + p.agents.filter((a) => a.done).length, 0);
  const modelCalls = phases.reduce((n, p) => n + p.agents.reduce((m, a) => m + a.llmCalls, 0), 0);

  // keep the newest reasoning in view while it streams, but never fight a user who scrolled up
  useEffect(() => {
    if (!open || !running || !bodyRef.current) return;
    const el = bodyRef.current;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [events.length, open, running]);

  if (!events.length && !running) return null;

  const summary = label ?? (running
    ? <>Thinking{currentPhase ? <span className="text-muted-foreground"> · {PHASE_TITLES[currentPhase.name] ?? currentPhase.name}</span> : null}</>
    : <>Thought for {ms(elapsedMs)}{agentCount ? <span className="text-muted-foreground"> · {agentCount} agents</span> : null}
        {modelCalls ? <span className="text-muted-foreground"> · {modelCalls} model {modelCalls === 1 ? "call" : "calls"}</span> : null}</>);

  return (
    <div className="rounded-lg border border-border/70 bg-white/45">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-slate-600 transition-colors hover:text-foreground"
      >
        {running ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
        ) : (
          <ChevronRight className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
        )}
        <span className={cn("min-w-0 flex-1 truncate", running && "shimmer")}>{summary}</span>
        <span className="shrink-0 text-[0.68rem] text-muted-foreground">{open ? "hide" : "show"}</span>
      </button>

      {open ? (
        <div ref={bodyRef} className="thin-scroll max-h-96 overflow-y-auto border-t border-border/70 px-3 py-2.5">
          {phases.map((phase, i) => (
            <section key={`${phase.name}-${i}`} className="mb-3 last:mb-0">
              <h4 className="mb-1.5 text-[0.65rem] font-semibold uppercase tracking-wider text-muted-foreground">
                {PHASE_TITLES[phase.name] ?? phase.name}
              </h4>
              <ul className="space-y-2.5">
                {phase.agents.map((agent) => <AgentRow key={agent.key} agent={agent} />)}
              </ul>
            </section>
          ))}
          {!phases.length ? <p className="py-2 text-xs text-muted-foreground">Starting…</p> : null}
        </div>
      ) : null}
    </div>
  );
}

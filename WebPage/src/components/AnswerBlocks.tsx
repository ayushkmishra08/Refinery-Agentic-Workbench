/**
 * The typed blocks an answer is made of, as components.
 *
 * The workbench returns an answer twice: once as prose in `answer_markdown`, and once as typed
 * blocks — a steps list, a limit gauge, a comparison matrix, a conflict table. The terminal can
 * only print the prose. Here the blocks are the point: an ordered procedure should look like a
 * procedure, and a value against its operating envelope should look like a gauge.
 *
 * Blocks that restate the *reasoning* rather than the answer — confidence, the executed plan, the
 * audit trail — are not rendered inline. They live behind the thinking trace and the answer
 * footer, so the answer reads as an answer.
 */
import {
  AlertTriangle, CheckCircle2, FileText, HelpCircle, Info, Lock, OctagonAlert, ShieldAlert,
} from "lucide-react";
import type { ReactNode } from "react";

import { Markdown } from "@/components/Markdown";
import { cn } from "@/lib/cn";
import type {
  Block, CalloutBlock, ClarificationBlock, ComparisonBlock, ConflictBlock, EvidenceBlock, EvidenceItem, GraphBlock,
  KpiBlock, LimitGaugeBlock, SafetyBlock, StepsBlock, TableBlock, TextBlock,
} from "@/lib/types";
import { Badge } from "@/ui";

function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="mb-2 text-[0.8rem] font-semibold uppercase tracking-wide text-slate-500">{children}</h3>;
}

const CALLOUT_STYLE = {
  info: { box: "border-[var(--info)]/25 bg-[var(--info)]/[0.06]", icon: <Info className="size-4 text-[var(--info)]" /> },
  success: { box: "border-[var(--success)]/25 bg-[var(--success)]/[0.06]", icon: <CheckCircle2 className="size-4 text-[var(--success)]" /> },
  warning: { box: "border-[var(--warning)]/30 bg-[var(--warning)]/[0.08]", icon: <AlertTriangle className="size-4 text-[var(--warning)]" /> },
  danger: { box: "border-[var(--danger)]/30 bg-[var(--danger)]/[0.07]", icon: <OctagonAlert className="size-4 text-[var(--danger)]" /> },
} as const;

function Callout({ block }: { block: CalloutBlock }) {
  const style = CALLOUT_STYLE[block.level] ?? CALLOUT_STYLE.info;
  return (
    <div className={cn("flex gap-2.5 rounded-lg border px-3.5 py-3", style.box)}>
      <span className="mt-0.5 shrink-0">{style.icon}</span>
      <div className="min-w-0 flex-1">
        {block.title ? <p className="mb-1 text-sm font-semibold text-foreground">{block.title}</p> : null}
        <Markdown className="text-sm">{block.markdown}</Markdown>
      </div>
    </div>
  );
}

function Kpi({ block }: { block: KpiBlock }) {
  return (
    <section>
      {block.title ? <SectionTitle>{block.title}</SectionTitle> : null}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {block.items.map((item, i) => (
          <div key={i} className="rounded-lg border border-border/70 bg-white/60 px-3 py-2.5">
            <p className="truncate text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground" title={item.label}>
              {item.label}
            </p>
            <p className="mt-1 flex items-baseline gap-1">
              <span className="text-lg font-semibold tabular-nums text-foreground">{item.value}</span>
              {item.unit ? <span className="text-xs text-slate-500">{item.unit}</span> : null}
            </p>
            <div className="mt-1 flex items-center justify-between gap-2">
              {item.qualifier ? <span className="truncate text-[0.68rem] text-muted-foreground">{item.qualifier}</span> : <span />}
              {item.citation ? <Citation refLabel={item.citation} /> : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function Citation({ refLabel }: { refLabel: string }) {
  const label = refLabel.replace(/[[\]]/g, "");
  return (
    <sup className="rounded bg-primary/10 px-1 py-px font-mono text-[0.62rem] font-semibold text-primary">{label}</sup>
  );
}

function Table({ block }: { block: TableBlock }) {
  return (
    <section>
      {block.title ? <SectionTitle>{block.title}</SectionTitle> : null}
      <div className="thin-scroll overflow-x-auto rounded-lg border border-border/70">
        <table className="w-full border-collapse text-[0.82rem]">
          <thead>
            <tr className="bg-slate-900/[0.04]">
              {block.columns.map((c, i) => (
                <th key={i} className="whitespace-nowrap px-3 py-2 text-left font-semibold text-slate-600">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, i) => (
              <tr key={i} className="border-t border-border/60 hover:bg-slate-900/[0.02]">
                {row.map((cell, j) => (
                  <td key={j} className="px-3 py-2 align-top text-slate-700">
                    {cell === null || cell === "" ? <span className="text-muted-foreground">—</span> : String(cell)}
                    {j === row.length - 1 && block.row_citations?.[i]?.length
                      ? block.row_citations[i].map((c, k) => <Citation key={k} refLabel={c} />)
                      : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {block.caption ? <p className="mt-1.5 text-xs text-muted-foreground">{block.caption}</p> : null}
    </section>
  );
}

function Steps({ block }: { block: StepsBlock }) {
  const pages = block.page_start
    ? `p.${block.page_start}${block.page_end && block.page_end !== block.page_start ? `–${block.page_end}` : ""}`
    : null;
  return (
    <section>
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <SectionTitle>{block.title ?? "Procedure"}</SectionTitle>
        {block.procedure_type ? <Badge tone="primary">{block.procedure_type.replace(/_/g, " ")}</Badge> : null}
        {pages ? <span className="text-xs text-muted-foreground">{pages}</span> : null}
      </div>

      {block.prerequisites?.length ? (
        <div className="mb-3 rounded-lg border border-[var(--warning)]/25 bg-[var(--warning)]/[0.05] px-3 py-2.5">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-[color-mix(in_oklab,var(--warning),black_30%)]">
            Before you start
          </p>
          <ul className="space-y-1.5">
            {block.prerequisites.map((p, i) => (
              <li key={i} className="flex gap-2 text-[0.82rem] text-slate-700">
                <span aria-hidden className="mt-[0.5em] size-1.5 shrink-0 rounded-full bg-[var(--warning)]" />
                <span>{p.text}{p.citation ? <Citation refLabel={p.citation} /> : null}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <ol className="space-y-2.5">
        {block.steps.map((step, i) => (
          <li key={i} className="flex gap-3">
            <span className="mt-px flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 font-mono text-[0.72rem] font-semibold text-primary">
              {step.sequence}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[0.87rem] leading-relaxed text-slate-700">
                {step.text}
                {step.citation ? <Citation refLabel={step.citation} /> : null}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                {step.page ? <span className="text-[0.68rem] text-muted-foreground">p.{step.page}</span> : null}
                {step.mentions?.slice(0, 4).map((tag) => (
                  <span key={tag} className="rounded bg-slate-900/[0.05] px-1.5 py-px font-mono text-[0.65rem] text-slate-600">{tag}</span>
                ))}
                {step.warnings?.length ? (
                  <span className="inline-flex items-center gap-1 rounded bg-[var(--warning)]/12 px-1.5 py-px text-[0.65rem] font-medium text-[color-mix(in_oklab,var(--warning),black_30%)]">
                    <AlertTriangle className="size-2.5" /> {step.warnings.join(", ")}
                  </span>
                ) : null}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

const VERDICT = {
  within_normal: { tone: "success", label: "Within normal" },
  within_design: { tone: "warning", label: "Within design" },
  outside_design: { tone: "danger", label: "Outside design" },
  unknown: { tone: "neutral", label: "Not determined" },
} as const;

function LimitGauge({ block }: { block: LimitGaugeBlock }) {
  const verdict = VERDICT[block.verdict] ?? VERDICT.unknown;
  const values = block.markers.map((m) => m.value).concat(block.value ?? []);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const pos = (v: number) => `${Math.min(100, Math.max(0, ((v - min) / (max - min || 1)) * 100))}%`;

  return (
    <section className="rounded-lg border border-border/70 bg-white/60 px-3.5 py-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{block.entity}</p>
          <p className="text-xs text-muted-foreground">{block.parameter}</p>
        </div>
        <div className="flex items-baseline gap-2">
          <span className="text-xl font-semibold tabular-nums text-foreground">
            {block.value !== null && block.value !== undefined ? block.value : "—"}
          </span>
          <span className="text-xs text-slate-500">{block.unit}</span>
          <Badge tone={verdict.tone}>{verdict.label}</Badge>
        </div>
      </div>

      <div className="relative mb-6 h-2 rounded-full bg-slate-900/[0.07]">
        {block.markers.map((m, i) => (
          <div key={i} className="absolute -top-0.5 flex flex-col items-center" style={{ left: pos(m.value) }}>
            <span className="h-3 w-px bg-slate-400" />
            <span className="mt-1 -translate-x-1/2 whitespace-nowrap text-[0.6rem] text-muted-foreground">
              {m.label} {m.value}
            </span>
          </div>
        ))}
        {block.value !== null && block.value !== undefined ? (
          <div
            className="absolute -top-1 size-4 -translate-x-1/2 rounded-full border-2 border-white bg-primary shadow"
            style={{ left: pos(block.value) }}
            title={`${block.value} ${block.unit}`}
          />
        ) : null}
      </div>

      {block.message ? <Markdown className="text-[0.82rem]">{block.message}</Markdown> : null}
    </section>
  );
}

function Comparison({ block }: { block: ComparisonBlock }) {
  return (
    <section>
      {block.title ? <SectionTitle>{block.title}</SectionTitle> : null}
      <div className="thin-scroll overflow-x-auto rounded-lg border border-border/70">
        <table className="w-full border-collapse text-[0.82rem]">
          <thead>
            <tr className="bg-slate-900/[0.04]">
              <th className="px-3 py-2 text-left font-semibold text-slate-600">Attribute</th>
              {block.subjects.map((s, i) => (
                <th key={i} className="px-3 py-2 text-left font-semibold text-slate-600">{s}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.attributes.map((attr, i) => (
              <tr key={i} className="border-t border-border/60">
                <td className="px-3 py-2 font-medium text-slate-600">{attr}</td>
                {block.cells[i]?.map((cell, j) => (
                  <td key={j} className="px-3 py-2 tabular-nums text-slate-700">
                    {cell?.value ? <>{cell.value} {cell.unit ?? ""}{cell.citation ? <Citation refLabel={cell.citation} /> : null}</>
                      : <span className="text-muted-foreground">—</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {block.differences?.length ? (
        <ul className="mt-2 space-y-1">
          {block.differences.map((d, i) => (
            <li key={i} className="flex gap-2 text-xs text-slate-600">
              <span aria-hidden className="mt-[0.45em] size-1 shrink-0 rounded-full bg-primary/60" />{d}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Conflict({ block }: { block: ConflictBlock }) {
  return (
    <section className="rounded-lg border border-[var(--warning)]/25 bg-[var(--warning)]/[0.04] px-3.5 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <SectionTitle>{block.title ?? "Documented values"}</SectionTitle>
        <Badge tone="warning">{block.status.replace(/_/g, " ")}</Badge>
      </div>
      <ul className="space-y-2">
        {block.claims.map((c, i) => (
          <li
            key={i}
            className={cn(
              "rounded-lg border px-3 py-2",
              block.preferred_index === i ? "border-[var(--success)]/40 bg-[var(--success)]/[0.06]" : "border-border/70 bg-white/60",
            )}
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-semibold tabular-nums text-foreground">{c.value} {c.unit ?? ""}</span>
              {block.preferred_index === i ? <Badge tone="success">preferred</Badge> : null}
            </div>
            <p className="mt-0.5 text-[0.72rem] text-muted-foreground">
              {c.document_id}{c.revision ? ` · rev ${c.revision}` : ""}{c.page ? ` · p.${c.page}` : ""}
              {c.source ? ` · ${c.source}` : ""}{c.context ? ` · ${c.context}` : ""}
            </p>
          </li>
        ))}
      </ul>
      {block.resolution ? <Markdown className="mt-2 text-[0.82rem]">{block.resolution}</Markdown> : null}
    </section>
  );
}

const SEVERITY = {
  danger: { tone: "danger", icon: <OctagonAlert className="size-3.5" /> },
  warning: { tone: "warning", icon: <AlertTriangle className="size-3.5" /> },
  caution: { tone: "warning", icon: <AlertTriangle className="size-3.5" /> },
  info: { tone: "info", icon: <Info className="size-3.5" /> },
} as const;

function Safety({ block }: { block: SafetyBlock }) {
  return (
    <section className="rounded-lg border border-[var(--danger)]/22 bg-[var(--danger)]/[0.04] px-3.5 py-3">
      <div className="mb-2 flex items-center gap-2">
        <ShieldAlert className="size-4 text-[var(--danger)]" />
        <SectionTitle>Safety</SectionTitle>
      </div>
      <ul className="space-y-1.5">
        {block.flags.map((flag, i) => {
          const s = SEVERITY[flag.severity] ?? SEVERITY.info;
          return (
            <li key={i} className="flex gap-2 text-[0.82rem] text-slate-700">
              <span className={cn("mt-0.5 shrink-0",
                flag.severity === "danger" ? "text-[var(--danger)]" : flag.severity === "info" ? "text-[var(--info)]" : "text-[var(--warning)]")}>
                {s.icon}
              </span>
              <span className="min-w-0 flex-1">
                <Badge tone={s.tone} className="mr-1.5 align-middle">{flag.severity}</Badge>
                {flag.message}
                {flag.citation ? <Citation refLabel={flag.citation} /> : null}
                {flag.requires_authorization ? (
                  <span className="ml-1.5 inline-flex items-center gap-1 text-[0.68rem] font-medium text-[var(--danger)]">
                    <Lock className="size-2.5" /> authorization required
                  </span>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Graph({ block }: { block: GraphBlock }) {
  const label = (id: string) => block.nodes.find((n) => n.id === id)?.label ?? id;
  return (
    <section>
      <SectionTitle>{block.title ?? "Process topology"}</SectionTitle>
      <ul className="space-y-1.5">
        {block.edges.map((e, i) => (
          <li key={i} className="flex flex-wrap items-center gap-1.5 text-[0.82rem] text-slate-700">
            <span className="rounded bg-slate-900/[0.05] px-1.5 py-0.5 font-medium">{label(e.source)}</span>
            <span className={cn("font-mono text-[0.68rem]", e.inferred ? "text-muted-foreground italic" : "text-primary")}>
              —{e.label}{e.inferred ? " (inferred)" : ""}→
            </span>
            <span className="rounded bg-slate-900/[0.05] px-1.5 py-0.5 font-medium">{label(e.target)}</span>
            {e.citation ? <Citation refLabel={e.citation} /> : null}
          </li>
        ))}
      </ul>
      {!block.edges.length ? <p className="text-xs text-muted-foreground">No documented connections.</p> : null}
    </section>
  );
}

export function EvidenceList({ items }: { items: EvidenceItem[] }) {
  if (!items.length) return null;
  return (
    <ol className="space-y-2">
      {items.map((e, i) => (
        <li key={i} className="flex gap-2.5 rounded-lg border border-border/60 bg-white/50 px-3 py-2">
          <span className="mt-px shrink-0 rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[0.65rem] font-semibold text-primary">
            {(e.ref || `${i + 1}`).replace(/[[\]]/g, "")}
          </span>
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-1.5 text-[0.7rem] text-muted-foreground">
              <FileText className="size-3" />
              <span className="font-medium text-slate-600">{e.document_id}</span>
              {e.page ? <span>p.{e.page}</span> : null}
              {e.source ? <span className="rounded bg-slate-900/[0.05] px-1 py-px">{e.source}</span> : null}
              {e.revision ? <span>rev {e.revision}</span> : null}
            </p>
            <p className="mt-1 line-clamp-4 text-[0.78rem] leading-snug text-slate-600">{e.excerpt || e.text}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function Evidence({ block }: { block: EvidenceBlock }) {
  return (
    <section>
      <SectionTitle>{block.title ?? "Evidence"}</SectionTitle>
      <EvidenceList items={block.items} />
    </section>
  );
}

/**
 * The workbench asking the engineer a question back.
 *
 * A clarification is an answer in its own right — the run stopped because nothing anchored the
 * request — so it gets the same weight as one. The options are the documented items it thinks
 * were meant; clicking one asks that question straight away.
 */
function Clarification({ block, onPick }: { block: ClarificationBlock; onPick?: (option: string) => void }) {
  return (
    <section className="rounded-lg border border-[var(--info)]/25 bg-[var(--info)]/[0.05] px-3.5 py-3">
      <div className="flex gap-2.5">
        <HelpCircle className="mt-0.5 size-4 shrink-0 text-[var(--info)]" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">{block.title ?? "One more detail needed"}</p>
          <p className="mt-1 text-[0.87rem] text-slate-700">{block.question}</p>

          {block.options?.length ? (
            <div className="mt-2.5">
              <p className="mb-1.5 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
                Did you mean
              </p>
              <div className="flex flex-wrap gap-1.5">
                {block.options.map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => onPick?.(option)}
                    disabled={!onPick}
                    className="rounded-lg border border-border bg-white/70 px-2.5 py-1 text-xs font-medium text-slate-700 transition-colors hover:border-primary/40 hover:bg-white disabled:cursor-default"
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {block.missing?.length ? (
            <p className="mt-2 text-[0.68rem] text-muted-foreground">
              missing: {block.missing.join(", ")}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

/** One block. Unknown types fall back to their markdown so nothing is silently dropped. */
export function AnswerBlock({ block, onPickOption }: { block: Block; onPickOption?: (option: string) => void }) {
  switch (block.type) {
    case "callout": return <Callout block={block as CalloutBlock} />;
    case "text": {
      const b = block as TextBlock;
      return (
        <section>
          {b.title ? <SectionTitle>{b.title}</SectionTitle> : null}
          <Markdown>{b.markdown}</Markdown>
        </section>
      );
    }
    case "kpi": return <Kpi block={block as KpiBlock} />;
    case "table": return <Table block={block as TableBlock} />;
    case "steps": return <Steps block={block as StepsBlock} />;
    case "limit_gauge": return <LimitGauge block={block as LimitGaugeBlock} />;
    case "comparison": return <Comparison block={block as ComparisonBlock} />;
    case "conflict": return <Conflict block={block as ConflictBlock} />;
    case "safety": return <Safety block={block as SafetyBlock} />;
    case "graph": return <Graph block={block as GraphBlock} />;
    case "evidence": return <Evidence block={block as EvidenceBlock} />;
    case "clarification": return <Clarification block={block as ClarificationBlock} onPick={onPickOption} />;
    default: {
      const md = (block as unknown as { markdown?: string }).markdown;
      return md ? <Markdown>{md}</Markdown> : null;
    }
  }
}

/**
 * Small shared pieces for the operations pages: a chain-integrity chip, a key/value list, a
 * monospace box for raw output. Kept tiny so each page stays about its own subject.
 */
import { Link2, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import type { ChainState } from "@/lib/types_ext";
import { Badge } from "@/ui";

/** "chain intact · 412" or "CHAIN BROKEN at #17". Hash-chained logs are the audit's tamper evidence. */
export function ChainBadge({ chain, label = "chain" }: { chain?: ChainState | null; label?: string }) {
  if (!chain) return null;
  return chain.ok ? (
    <Badge tone="success" title={chain.head ? `head ${chain.head.slice(0, 16)}…` : undefined}>
      <Link2 className="size-2.5" /> {label} intact{typeof chain.entries === "number" ? ` · ${chain.entries}` : ""}
    </Badge>
  ) : (
    <Badge tone="danger" title={chain.detail}>
      <ShieldAlert className="size-2.5" /> {label} BROKEN{chain.first_bad_seq != null ? ` at #${chain.first_bad_seq}` : ""}
    </Badge>
  );
}

export function KeyValues({ rows, className }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("divide-y divide-border/60 text-xs", className)}>
      {rows.map(([k, v], i) => (
        <div key={i} className="flex items-start justify-between gap-3 py-1.5">
          <dt className="shrink-0 text-muted-foreground">{k}</dt>
          <dd className="min-w-0 truncate text-right font-medium text-foreground">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Mono({ children, className, maxHeight = "max-h-64" }: { children: ReactNode; className?: string; maxHeight?: string }) {
  return (
    <pre className={cn("thin-scroll overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/70 bg-slate-900/[0.04] px-3 py-2 font-mono text-[0.7rem] leading-relaxed text-slate-700", maxHeight, className)}>
      {children}
    </pre>
  );
}

export function OkBadge({ ok, yes = "ok", no = "failed" }: { ok: boolean; yes?: string; no?: string }) {
  return <Badge tone={ok ? "success" : "danger"}>{ok ? yes : no}</Badge>;
}

export function bytes(n: number | undefined | null): string {
  if (!n && n !== 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function clock(epochSeconds?: number | null): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

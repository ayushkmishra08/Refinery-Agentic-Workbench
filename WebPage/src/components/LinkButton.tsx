/** A router link that looks and focuses like a button. Keeps navigation semantics intact. */
import type { ReactNode } from "react";
import { Link } from "react-router";

import { cn } from "@/lib/cn";

const VARIANT = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm",
  outline: "border border-border bg-white/60 text-foreground hover:bg-white",
  ghost: "text-muted-foreground hover:bg-slate-900/[0.06] hover:text-foreground",
} as const;

export function LinkButton({
  to, children, variant = "outline", className,
}: { to: string; children: ReactNode; variant?: keyof typeof VARIANT; className?: string }) {
  return (
    <Link
      to={to}
      className={cn(
        "inline-flex h-8 select-none items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
        VARIANT[variant], className,
      )}
    >
      {children}
    </Link>
  );
}

/**
 * The workstation's UI primitives.
 *
 * Small and hand-written on purpose: this app needs eight or nine controls, and the design system
 * it has to match already lives in `index.css` as CSS variables. Everything here reads those
 * variables, so a change to the palette moves the whole app.
 *
 * Accessibility is not decoration in a control-room tool — dialogs trap focus and close on Escape,
 * every icon-only control carries a label, and state is announced rather than only coloured.
 */
import {
  type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode,
  type TextareaHTMLAttributes, createContext, forwardRef, useCallback, useContext, useEffect,
  useId, useMemo, useRef, useState,
} from "react";

import { cn } from "@/lib/cn";

// ---------------------------------------------------------------- surfaces

export function Panel({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("glass rounded-xl border border-border/70", className)} {...rest} />;
}

export function PanelHeader({
  title, description, actions, className,
}: { title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3 border-b border-border/70 px-4 py-3", className)}>
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold text-foreground">{title}</h2>
        {description ? <p className="mt-0.5 text-xs text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function PageHeader({
  title, description, actions,
}: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

// ---------------------------------------------------------------- button

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type ButtonSize = "sm" | "md" | "icon";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm",
  secondary: "bg-slate-900/[0.06] text-foreground hover:bg-slate-900/[0.1]",
  outline: "border border-border bg-white/60 text-foreground hover:bg-white",
  ghost: "text-muted-foreground hover:bg-slate-900/[0.06] hover:text-foreground",
  danger: "bg-[var(--danger)] text-white hover:brightness-110 shadow-sm",
};
const BUTTON_SIZE: Record<ButtonSize, string> = {
  sm: "h-8 gap-1.5 px-2.5 text-xs",
  md: "h-9 gap-2 px-3.5 text-sm",
  icon: "size-9 justify-center",
};

export function Button({
  variant = "secondary", size = "md", className, loading, children, disabled, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: ButtonSize; loading?: boolean }) {
  return (
    <button
      className={cn(
        "inline-flex select-none items-center rounded-lg font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
        "disabled:pointer-events-none disabled:opacity-50",
        BUTTON_VARIANT[variant], BUTTON_SIZE[size], className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Spinner className="size-3.5" /> : null}
      {children}
    </button>
  );
}

/**
 * A progress bar. With a `percent` it fills to it; without one it sweeps, because a document the
 * parser has not opened yet has no honest percentage and inventing one is worse than admitting it.
 */
export function ProgressBar({
  percent, label, className,
}: { percent?: number | null; label?: string; className?: string }) {
  const known = typeof percent === "number" && Number.isFinite(percent);
  const value = known ? Math.max(0, Math.min(100, Math.round(percent))) : undefined;
  return (
    <div
      className={cn("h-1.5 w-full overflow-hidden rounded-full bg-primary/15", className)}
      role="progressbar"
      aria-label={label || "Progress"}
      aria-valuenow={value}
      aria-valuemin={known ? 0 : undefined}
      aria-valuemax={known ? 100 : undefined}
    >
      {known ? (
        <span className="block h-full rounded-full bg-primary transition-[width] duration-500 ease-out"
              style={{ width: `${value}%` }} />
      ) : (
        <span className="block h-full w-1/3 rounded-full bg-primary/70 motion-safe:animate-[sweep_1.4s_ease-in-out_infinite]" />
      )}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Working"
      className={cn("inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent", className)}
    />
  );
}

// ---------------------------------------------------------------- inputs

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-lg border border-border bg-white/70 px-3 text-sm text-foreground",
        "placeholder:text-muted-foreground/70",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:opacity-60", className,
      )}
      {...rest}
    />
  );
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(
          "w-full resize-none rounded-lg border border-border bg-white/70 px-3 py-2 text-sm text-foreground",
          "placeholder:text-muted-foreground/70",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className,
        )}
        {...rest}
      />
    );
  },
);

export function Field({
  label, hint, error, children, className,
}: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id} className="block text-xs font-medium text-slate-600">{label}</label>
      <div id={id}>{children}</div>
      {error ? (
        <p role="alert" className="text-xs text-[var(--danger)]">{error}</p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

/**
 * Six boxes for an authenticator code.
 *
 * The subtlety is that a code rarely arrives one character at a time. A browser's `one-time-code`
 * autofill drops all six into the first box at once, a paste does the same, and a fast typist
 * outruns the focus move between boxes. So every input event is treated as "here are some digits,
 * starting at this box" and distributed across the remaining ones — which makes autofill, paste
 * and typing the same code path instead of three that can each break separately.
 */
export function OtpInput({
  value, onChange, length = 6, disabled, autoFocus, onComplete,
}: {
  value: string; onChange: (next: string) => void; length?: number;
  disabled?: boolean; autoFocus?: boolean; onComplete?: (code: string) => void;
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const digits = value.slice(0, length).padEnd(length, " ").split("");

  // Keystrokes can arrive faster than React commits a render — a fast typist, or a browser
  // replaying an autofill. Reading `value` from the render closure would then let each keystroke
  // start from a stale string and overwrite the one before it, so the latest value is tracked in
  // a ref and every write starts from that.
  const latest = useRef(value);
  latest.current = value;

  /** Write `incoming` starting at `index`, then park the caret after the last digit written. */
  const write = (index: number, incoming: string) => {
    const chars = incoming.replace(/\D/g, "");
    if (!chars) return;
    const next = latest.current.slice(0, length).padEnd(length, " ").split("");
    let cursor = index;
    for (const char of chars) {
      if (cursor >= length) break;
      next[cursor++] = char;
    }
    const joined = next.join("").replace(/\s+$/, "");
    latest.current = joined;
    onChange(joined);
    refs.current[Math.min(cursor, length - 1)]?.focus();
    if (joined.replace(/\s/g, "").length === length) onComplete?.(joined);
  };

  const clearAt = (index: number) => {
    const next = latest.current.slice(0, length).padEnd(length, " ").split("");
    next[index] = " ";
    const joined = next.join("").replace(/\s+$/, "");
    latest.current = joined;
    onChange(joined);
  };

  return (
    <div className="flex gap-2" role="group" aria-label={`${length}-digit authenticator code`}>
      {digits.map((digit, i) => (
        <input
          key={i}
          ref={(el) => { refs.current[i] = el; }}
          inputMode="numeric"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          autoFocus={autoFocus && i === 0}
          disabled={disabled}
          aria-label={`Digit ${i + 1} of ${length}`}
          value={digit.trim()}
          onChange={(e) => write(i, e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Backspace") {
              e.preventDefault();
              if (digit.trim()) clearAt(i);
              else if (i > 0) { clearAt(i - 1); refs.current[i - 1]?.focus(); }
            }
            if (e.key === "ArrowLeft" && i > 0) refs.current[i - 1]?.focus();
            if (e.key === "ArrowRight" && i < length - 1) refs.current[i + 1]?.focus();
          }}
          onPaste={(e) => { e.preventDefault(); write(i, e.clipboardData.getData("text")); }}
          onFocus={(e) => e.currentTarget.select()}
          className={cn(
            "h-12 w-11 rounded-lg border border-border bg-white/80 text-center font-mono text-lg font-semibold text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            "disabled:opacity-60",
          )}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- badges

export function Badge({
  children, tone = "neutral", className, title,
}: {
  children: ReactNode; className?: string; title?: string;
  tone?: "neutral" | "primary" | "success" | "warning" | "danger" | "info";
}) {
  const tones = {
    neutral: "bg-slate-900/[0.06] text-slate-600",
    primary: "bg-primary/10 text-primary",
    success: "bg-[var(--success)]/12 text-[var(--success)]",
    warning: "bg-[var(--warning)]/15 text-[color-mix(in_oklab,var(--warning),black_25%)]",
    danger: "bg-[var(--danger)]/12 text-[var(--danger)]",
    info: "bg-[var(--info)]/12 text-[var(--info)]",
  } as const;
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[0.7rem] font-semibold tracking-wide",
        tones[tone], className,
      )}
    >
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- states

export function EmptyState({
  icon, title, description, action,
}: { icon?: ReactNode; title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon ? <div className="mb-1 text-muted-foreground/60">{icon}</div> : null}
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description ? <p className="max-w-md text-xs text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/[0.06] px-4 py-3">
      <p className="text-sm font-medium text-[var(--danger)]">Something went wrong</p>
      <p className="mt-1 text-xs text-slate-600">{error}</p>
      {onRetry ? <Button size="sm" variant="outline" className="mt-2.5" onClick={onRetry}>Try again</Button> : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-slate-900/[0.07]", className)} />;
}

// ---------------------------------------------------------------- dialog

export function Dialog({
  open, onClose, title, description, children, footer, wide,
}: {
  open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode;
  children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  // Callers pass `onClose` as an inline arrow, so its identity changes on every render. Keeping it
  // in the effect's dependency list would tear the trap down and set it up again after every
  // keystroke, and the teardown restores focus to whatever was focused when the dialog opened —
  // which pulls the caret out of the field being typed into. The effect therefore keys on `open`
  // alone and reads the current handler through a ref.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); close.current(); return; }
      if (e.key !== "Tab" || !panelRef.current) return;
      // keep focus inside: a dialog that can be tabbed out of is a dialog that gets lost behind
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]),[href],input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    const t = window.setTimeout(() => {
      const panel = panelRef.current;
      if (!panel) return;
      // A field is what the person came to fill in, so it wins over a button that happens to sit
      // higher in the markup (the "Copy key" button above the code boxes, for instance).
      const target =
        panel.querySelector<HTMLElement>("input:not([disabled]),textarea:not([disabled])") ??
        panel.querySelector<HTMLElement>("button:not([disabled]),[href]");
      target?.focus();
    }, 20);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey, true);
      window.clearTimeout(t);
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/25 backdrop-blur-[2px]" onClick={onClose} aria-hidden />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        className={cn(
          "relative z-10 max-h-[88vh] w-full overflow-hidden rounded-xl border border-border bg-[var(--popover)] shadow-xl",
          wide ? "max-w-3xl" : "max-w-lg",
        )}
      >
        <div className="border-b border-border/70 px-5 py-3.5">
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {description ? <p className="mt-0.5 text-xs text-muted-foreground">{description}</p> : null}
        </div>
        <div className="thin-scroll max-h-[62vh] overflow-y-auto px-5 py-4">{children}</div>
        {footer ? <div className="flex justify-end gap-2 border-t border-border/70 px-5 py-3">{footer}</div> : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tabs

export function Tabs<T extends string>({
  tabs, value, onChange, className,
}: { tabs: { id: T; label: ReactNode; badge?: ReactNode }[]; value: T; onChange: (id: T) => void; className?: string }) {
  return (
    <div role="tablist" className={cn("flex gap-1 rounded-lg bg-slate-900/[0.05] p-1", className)}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={value === tab.id}
          onClick={() => onChange(tab.id)}
          className={cn(
            "flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
            value === tab.id ? "bg-white text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {tab.label}
          {tab.badge}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- toasts

type Toast = { id: number; message: string; tone: "info" | "success" | "danger"; detail?: string };
const ToastContext = createContext<{ push: (t: Omit<Toast, "id">) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { ...t, id }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), 6000);
  }, []);
  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto rounded-lg border px-3.5 py-2.5 shadow-lg backdrop-blur",
              t.tone === "danger" ? "border-[var(--danger)]/30 bg-[var(--danger)]/[0.09]"
                : t.tone === "success" ? "border-[var(--success)]/30 bg-[var(--success)]/[0.09]"
                : "border-border bg-white/90",
            )}
          >
            <p className="text-xs font-medium text-foreground">{t.message}</p>
            {t.detail ? <p className="mt-0.5 text-[0.7rem] text-muted-foreground">{t.detail}</p> : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

// ---------------------------------------------------------------- misc

export function CopyButton({ text, label = "Copy", className }: { text: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      className={className}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          // clipboard is blocked outside a secure context; select-and-copy still works
        }
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1800);
      }}
    >
      {copied ? "Copied" : label}
    </Button>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "primary" | "warning" }) {
  return (
    <Panel className="px-4 py-3">
      <p className="text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-2xl font-semibold tabular-nums",
        tone === "primary" ? "text-primary" : tone === "warning" ? "text-[var(--warning)]" : "text-foreground")}>
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
    </Panel>
  );
}

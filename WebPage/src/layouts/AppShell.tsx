/**
 * The signed-in shell: navigation, clearance, session expiry.
 *
 * The sidebar shows only what the signed-in role can act on. That is a convenience, not a control
 * — hiding "Approvals" from an engineer keeps the interface honest about what they can do, while
 * the API refuses the call regardless of what the browser renders.
 *
 * The expiry countdown appears in the last ten minutes of a token's life. An eight-hour session
 * that dies mid-question with no warning is the kind of small thing that makes people distrust a
 * tool, so it is surfaced before it bites.
 */
import {
  FileText, FolderTree, KeyRound, Layers, LogOut, MessageSquare, Menu, ShieldCheck, Timer, UserCircle2, X,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { cn } from "@/lib/cn";
import { countdown } from "@/lib/format";
import type { Role } from "@/lib/types";
import { ROLE_TITLE, TAG_TONE, useAuth } from "@/store/auth";
import { Badge, Button } from "@/ui";
import { RecentConversations } from "@/components/RecentConversations";

interface NavItem { to: string; label: string; icon: ReactNode; minRole?: Role; hint?: string }

const NAV: NavItem[] = [
  { to: "/chat", label: "Ask", icon: <MessageSquare className="size-4" />, hint: "Put a question to the plant documentation" },
  { to: "/overview", label: "Overview", icon: <Layers className="size-4" />, hint: "Your workspace at a glance" },
  { to: "/documents", label: "Documents", icon: <FileText className="size-4" />, hint: "What is loaded and who may read it" },
  { to: "/knowledge", label: "Knowledge", icon: <FolderTree className="size-4" />, hint: "The knowledge layer branch by branch, and your reach into it" },
  { to: "/access", label: "Access", icon: <KeyRound className="size-4" />, hint: "Requests and approvals" },
  { to: "/security", label: "Security", icon: <ShieldCheck className="size-4" />, minRole: "manager", hint: "The access model in force" },
];

const ROLE_LEVEL: Record<Role, number> = { guest: 0, user: 1, manager: 2, admin: 3 };

export default function AppShell() {
  const { session, signOut, expiresInSeconds } = useAuth();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => setMenuOpen(false), [location.pathname]);

  if (!session) return null;

  const items = NAV.filter((n) => !n.minRole || ROLE_LEVEL[session.role] >= ROLE_LEVEL[n.minRole]);
  const expiringSoon = expiresInSeconds !== null && expiresInSeconds < 600;

  const nav = (
    <nav className="flex flex-col gap-0.5" aria-label="Sections">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          title={item.hint}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              isActive ? "bg-primary/10 text-primary" : "text-slate-600 hover:bg-slate-900/[0.05] hover:text-foreground",
            )
          }
        >
          {item.icon}
          {item.label}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="app-canvas flex h-screen flex-col overflow-hidden">
      {/* ---------------------------------------------------------------- top bar */}
      <header className="flex shrink-0 items-center gap-3 border-b border-border/70 bg-white/55 px-4 py-2.5 backdrop-blur">
        <button
          className="rounded-lg p-1.5 text-slate-600 hover:bg-slate-900/[0.06] lg:hidden"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label={menuOpen ? "Close menu" : "Open menu"}
          aria-expanded={menuOpen}
        >
          {menuOpen ? <X className="size-4" /> : <Menu className="size-4" />}
        </button>

        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-[0.7rem] font-bold text-primary-foreground">
            MR
          </span>
          <div className="min-w-0 leading-tight">
            <p className="truncate text-sm font-semibold text-foreground">Engineering AI Workstation</p>
            <p className="truncate text-[0.65rem] text-muted-foreground">Answers built only from documents you may read</p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {expiringSoon ? (
            <span className="hidden items-center gap-1 rounded-md bg-[var(--warning)]/12 px-2 py-1 text-[0.68rem] font-medium text-[color-mix(in_oklab,var(--warning),black_30%)] sm:flex">
              <Timer className="size-3" /> session ends in {countdown(expiresInSeconds!)}
            </span>
          ) : null}

          <div className="hidden items-center gap-1.5 rounded-lg border border-border/70 bg-white/60 px-2.5 py-1.5 sm:flex">
            <ShieldCheck className="size-3.5 text-primary" />
            <div className="leading-tight">
              <p className="text-[0.7rem] font-semibold text-foreground">
                {session.username} · {ROLE_TITLE[session.role]}
              </p>
              <p className="flex gap-1 text-[0.6rem] text-muted-foreground">
                {session.readableTags.map((t) => (
                  <span key={t} className={cn(
                    "rounded px-1",
                    t === "SECRET" ? "bg-[var(--danger)]/10 text-[var(--danger)]"
                      : t === "CONFIDENTIAL" ? "bg-[var(--warning)]/15 text-[color-mix(in_oklab,var(--warning),black_25%)]"
                      : "bg-[var(--info)]/10 text-[var(--info)]",
                  )}>{t}</span>
                ))}
              </p>
            </div>
          </div>

          <NavLink to="/account" title="Account" className="rounded-lg p-1.5 text-slate-600 hover:bg-slate-900/[0.06]">
            <UserCircle2 className="size-4" />
          </NavLink>
          <Button size="icon" variant="ghost" onClick={() => void signOut()} aria-label="Sign out" title="Sign out">
            <LogOut className="size-4" />
          </Button>
        </div>
      </header>

      {session.mfaEnrolmentPending ? (
        <div className="shrink-0 border-b border-[var(--warning)]/30 bg-[var(--warning)]/[0.09] px-4 py-1.5 text-center text-[0.7rem] text-slate-700">
          Your role requires two-step verification and no authenticator is enrolled.{" "}
          <NavLink to="/account" className="font-semibold text-primary underline-offset-2 hover:underline">
            Enrol one now
          </NavLink>
        </div>
      ) : null}

      {/* ---------------------------------------------------------------- body */}
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-60 shrink-0 flex-col border-r border-border/70 bg-white/40 p-3 lg:flex">
          {nav}
          <RecentConversations username={session.username} />
          <div className="mt-4 rounded-lg border border-border/70 bg-white/50 p-2.5">
            <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">Clearance</p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {session.readableTags.map((t) => <Badge key={t} tone={TAG_TONE[t]}>{t}</Badge>)}
            </div>
            <p className="mt-1.5 text-[0.65rem] text-muted-foreground">
              {session.readableDocuments.length} readable
              {session.withheldDocuments.length ? `, ${session.withheldDocuments.length} withheld` : ""}
            </p>
          </div>
        </aside>

        {menuOpen ? (
          <div className="absolute inset-x-0 top-[3.25rem] z-40 border-b border-border/70 bg-white/95 p-3 backdrop-blur lg:hidden">
            {nav}
          </div>
        ) : null}

        <main className="min-w-0 flex-1 overflow-hidden">
          {location.pathname === "/chat" ? (
            <Outlet />
          ) : (
            <div className="thin-scroll h-full overflow-y-auto px-4 py-5 lg:px-8">
              <div className="mx-auto w-full max-w-5xl">
                <Outlet />
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

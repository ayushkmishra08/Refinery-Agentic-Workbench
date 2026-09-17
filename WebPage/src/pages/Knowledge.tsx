/**
 * The knowledge layer, branch by branch, with this role's reach marked on each one.
 *
 * Access is granted in units of a document, so a document is the branch this page draws. A branch
 * you may read is opened: its chapters are listed and its contents are counted. A branch you may
 * not is named, classified, and pointed at the role that can release it — and nothing else about
 * it is shown, not a chapter, not a page total, not a count. That asymmetry is the whole design:
 * knowing a document *exists* is what makes an access request possible, and knowing how *large*
 * it is would be a measurement of the tier above yours.
 *
 * An administrator can also set who reads a branch from here. By default a document follows the
 * tag ladder — INTERNAL is read by everyone signed in, SECRET by administrators. Pinning an
 * explicit list turns the branch into a compartment instead: exactly those roles, so being senior
 * stops implying being included, and "the desalter tree belongs to the manager" becomes something
 * the system can actually hold.
 */
import {
  ChevronDown, ChevronRight, FolderTree, KeyRound, Lock, LockOpen, Pencil, ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { plural } from "@/lib/format";
import type { KnowledgeBranch, KnowledgeTree, Role, Tag } from "@/lib/types";
import { ROLE_TITLE, useAuth } from "@/store/auth";
import {
  Badge, Button, Dialog, EmptyState, ErrorState, PageHeader, Panel, PanelHeader, Skeleton, Stat,
  useToast,
} from "@/ui";

const TAG_TONE: Record<Tag, "info" | "warning" | "danger"> = {
  INTERNAL: "info",
  CONFIDENTIAL: "warning",
  SECRET: "danger",
};

/** The roles that can be put on an allowlist. `guest` is a state, not an account. */
const ASSIGNABLE: Role[] = ["user", "manager", "admin"];

const COUNT_LABELS: { key: keyof NonNullable<KnowledgeBranch["counts"]>; label: string; hint: string }[] = [
  { key: "entities", label: "Equipment", hint: "pumps, columns, heaters, instruments" },
  { key: "claims", label: "Values", hint: "each carrying the page it came from" },
  { key: "procedures", label: "Procedures", hint: "ordered steps with prerequisites" },
  { key: "relations", label: "Connections", hint: "feeds, discharges to, controlled by" },
  { key: "chunks", label: "Passages", hint: "typed and indexed for retrieval" },
];

export default function Knowledge() {
  const { session } = useAuth();
  const toast = useToast();
  const [tree, setTree] = useState<KnowledgeTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<KnowledgeBranch | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setTree(await api.knowledgeTree());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const totals = useMemo(() => {
    const out = { entities: 0, claims: 0, procedures: 0, relations: 0, chunks: 0 };
    for (const b of tree?.branches ?? []) {
      for (const key of Object.keys(out) as (keyof typeof out)[]) out[key] += b.counts?.[key] ?? 0;
    }
    return out;
  }, [tree]);

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!tree) return <Skeleton className="h-64 w-full" />;

  const isAdmin = session?.role === "admin";

  return (
    <div>
      <PageHeader
        title="Knowledge layer"
        description={
          `Every document is a branch, and a branch is the unit access is granted in. You can open ` +
          `${tree.readable} of ${tree.branches.length}${tree.locked ? `; the other ${tree.locked} ` +
          `${tree.locked === 1 ? "is named but stays shut" : "are named but stay shut"}` : ""}.`
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {COUNT_LABELS.map((c) => (
          <Stat
            key={c.key}
            label={c.label}
            value={(totals[c.key as keyof typeof totals] ?? 0).toLocaleString()}
            hint={c.hint}
            tone={c.key === "entities" ? "primary" : undefined}
          />
        ))}
      </div>

      <Panel className="mt-4">
        <PanelHeader
          title="Branches"
          description="Open one to see its chapters. What is counted here is only what you may read."
          actions={
            <span className="flex items-center gap-1.5 text-[0.7rem] text-muted-foreground">
              <ShieldCheck className="size-3.5" /> {ROLE_TITLE[tree.role]}
            </span>
          }
        />
        {tree.branches.length === 0 ? (
          <EmptyState title="No documents are loaded" description="Nothing has been indexed for this workstation yet." />
        ) : (
          <ul className="divide-y divide-border/60">
            {tree.branches.map((branch) => (
              <BranchRow
                key={branch.document_id}
                branch={branch}
                expanded={!!open[branch.document_id]}
                onToggle={() =>
                  setOpen((prev) => ({ ...prev, [branch.document_id]: !prev[branch.document_id] }))
                }
                onEdit={isAdmin ? () => setEditing(branch) : undefined}
              />
            ))}
          </ul>
        )}
      </Panel>

      <p className="mt-4 text-center text-[0.7rem] text-muted-foreground">
        A locked branch reports its name and its classification, never its size. Ask for a key from
        the chat and approval opens the records that answer that one question.
      </p>

      {editing ? (
        <RolesDialog
          branch={editing}
          onClose={() => setEditing(null)}
          onSaved={async (message) => {
            setEditing(null);
            toast.push({ tone: "success", message: "Readers updated", detail: message });
            await load();
          }}
          onFailed={(message) => toast.push({ tone: "danger", message: "Could not change that", detail: message })}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------- one branch

function BranchRow({
  branch, expanded, onToggle, onEdit,
}: {
  branch: KnowledgeBranch;
  expanded: boolean;
  onToggle: () => void;
  onEdit?: () => void;
}) {
  const chapters = branch.chapters ?? [];
  const canExpand = branch.readable && chapters.length > 0;

  return (
    <li className={cn("px-4 py-3", !branch.readable && "bg-slate-900/[0.015]")}>
      <div className="flex flex-wrap items-start gap-3">
        <span
          className={cn(
            "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
            branch.readable ? "bg-primary/10 text-primary" : "bg-slate-900/[0.05] text-muted-foreground",
          )}
          aria-hidden
        >
          {branch.readable ? <LockOpen className="size-4" /> : <Lock className="size-4" />}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={canExpand ? onToggle : undefined}
              disabled={!canExpand}
              className={cn(
                "flex items-center gap-1 text-sm font-medium text-foreground",
                canExpand ? "hover:underline" : "cursor-default",
              )}
              aria-expanded={canExpand ? expanded : undefined}
            >
              {canExpand ? (
                expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />
              ) : (
                <FolderTree className="size-3.5 text-muted-foreground" />
              )}
              <span className="truncate">{branch.document_id}</span>
            </button>
            <Badge tone={TAG_TONE[branch.tag]}>{branch.tag}</Badge>
            {branch.compartmented ? (
              <Badge tone="neutral" title="This branch names its readers outright instead of following the tag ladder.">
                compartment
              </Badge>
            ) : null}
          </div>

          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {branch.title !== branch.document_id ? branch.title : branch.reason || ""}
          </p>

          {branch.readable ? (
            <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[0.7rem] text-slate-600">
              {COUNT_LABELS.map((c) =>
                branch.counts?.[c.key] ? (
                  <span key={c.key}>
                    <span className="font-semibold text-foreground">{branch.counts[c.key]!.toLocaleString()}</span>{" "}
                    {c.label.toLowerCase()}
                  </span>
                ) : null,
              )}
              {branch.pages ? <span>{plural(branch.pages, "page")}</span> : null}
              {chapters.length ? <span>{plural(chapters.length, "chapter")}</span> : null}
            </p>
          ) : (
            <p className="mt-1.5 flex items-center gap-1.5 text-[0.7rem] text-slate-600">
              <KeyRound className="size-3 shrink-0 text-[var(--warning)]" />
              Not yours to read.{" "}
              {branch.ask
                ? `Ask a ${ROLE_TITLE[branch.ask]} — ask your question in the chat and the workstation raises the request for you.`
                : "No other role is cleared for it either."}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <span className="text-[0.7rem] text-muted-foreground">
            {branch.roles.length ? branch.roles.map((r) => ROLE_TITLE[r]).join(", ") : "nobody"}
          </span>
          {onEdit ? (
            <Button variant="ghost" onClick={onEdit} title="Change who reads this branch">
              <Pencil className="size-3.5" />
            </Button>
          ) : null}
        </div>
      </div>

      {expanded && canExpand ? (
        <ol className="thin-scroll mt-2.5 max-h-64 overflow-y-auto rounded-lg border border-border/70 bg-white/55 px-3 py-2">
          {chapters.map((c, i) => (
            <li key={i} className="flex gap-2 py-0.5 text-xs text-slate-700">
              <span className="w-8 shrink-0 text-right font-mono text-[0.7rem] text-muted-foreground">
                {c.number ?? i + 1}
              </span>
              <span className="min-w-0 flex-1">{c.title}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------- who reads it

function RolesDialog({
  branch, onClose, onSaved, onFailed,
}: {
  branch: KnowledgeBranch;
  onClose: () => void;
  onSaved: (message: string) => void | Promise<void>;
  onFailed: (message: string) => void;
}) {
  const [pinned, setPinned] = useState(branch.compartmented);
  const [roles, setRoles] = useState<Role[]>(branch.roles.filter((r) => r !== "guest"));
  const [busy, setBusy] = useState(false);

  const toggle = (role: Role) =>
    setRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));

  const save = async () => {
    setBusy(true);
    try {
      const result = await api.setDocumentRoles(branch.document_id, pinned ? roles : null);
      await onSaved(
        result.compartmented
          ? `${branch.document_id} is now read by ${result.roles.map((r) => ROLE_TITLE[r]).join(", ") || "nobody"} and no one else.`
          : `${branch.document_id} follows the ${result.tag} tag again — ${ROLE_TITLE[result.min_role]} and above.`,
      );
    } catch (err) {
      onFailed(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Who reads this branch"
      description={branch.document_id}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={pinned && roles.length === 0} onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-border/70 bg-white/55 px-3 py-2.5">
          <input
            type="radio"
            name="mode"
            checked={!pinned}
            onChange={() => setPinned(false)}
            className="mt-0.5"
          />
          <span>
            <span className="text-sm font-medium text-foreground">Follow the classification</span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              The {branch.tag} tag decides: every role at that level and above reads it. This is the
              default, and it is what makes seniority mean something.
            </span>
          </span>
        </label>

        <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-border/70 bg-white/55 px-3 py-2.5">
          <input
            type="radio"
            name="mode"
            checked={pinned}
            onChange={() => setPinned(true)}
            className="mt-0.5"
          />
          <span>
            <span className="text-sm font-medium text-foreground">Name the readers</span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              Exactly the roles ticked below, and nobody else — an administrator left off the list
              cannot read it either. The tag is kept; it still says how sensitive the material is.
            </span>
          </span>
        </label>

        <div className={cn("space-y-1.5 pl-1", !pinned && "pointer-events-none opacity-45")}>
          {ASSIGNABLE.map((role) => (
            <label key={role} className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={pinned ? roles.includes(role) : branch.roles.includes(role)}
                disabled={!pinned}
                onChange={() => toggle(role)}
              />
              {ROLE_TITLE[role]}
            </label>
          ))}
          {pinned && roles.length === 0 ? (
            <p className="pt-1 text-[0.7rem] text-[var(--danger)]">
              Tick at least one role. A branch nobody can read is not something to set by accident.
            </p>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}

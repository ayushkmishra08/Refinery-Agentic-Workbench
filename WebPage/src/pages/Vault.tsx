/**
 * Envelope encryption of the knowledge branches.
 *
 * Each branch's data is encrypted once with its own content key; that key is stored wrapped by
 * the wrapping key of every role allowed to read the branch. A session unwraps only what its
 * role holds a key for, into this process's memory, and the branch is dropped again when the
 * last session that could open it signs out. Rotating a role's wrapping key re-wraps a few
 * dozen small keys, not the data; revoking a role from a branch deletes one wrapped copy.
 */
import { KeyRound, Lock, LockOpen, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ChainBadge, KeyValues, clock } from "@/components/Bits";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/format";
import type { Role } from "@/lib/types";
import type { VaultStatus } from "@/lib/types_ext";
import { ROLE_TITLE, useAuth } from "@/store/auth";
import { Badge, Button, Dialog, EmptyState, ErrorState, PageHeader, Panel, PanelHeader, Skeleton, useToast } from "@/ui";

const ROLES: Role[] = ["user", "manager", "admin"];

export default function Vault() {
  const { atLeast } = useAuth();
  const toast = useToast();
  const [v, setV] = useState<VaultStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"seal" | "rotate" | "revoke" | null>(null);
  const [shred, setShred] = useState(false);
  const [role, setRole] = useState<Role>("manager");
  const [branch, setBranch] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setV(await api.vault());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    try {
      const msg = await fn();
      toast.push({ tone: "success", message: msg });
      setDialog(null);
      await load();
    } catch (err) {
      toast.push({ tone: "danger", message: "Refused", detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!v) return <Skeleton className="h-64 w-full" />;

  const admin = atLeast("admin");
  const memory = v.branches_in_memory;
  const branches = memory.branches.length ? memory.branches : v.sealed_branches.map((s) => ({
    branch: s.branch, sealed: true, in_memory: false, roles_with_key: (v.kms.branches?.[s.branch]?.roles ?? s.roles ?? []) as string[], opened_by: [] as string[], opened_at: null,
  }));

  return (
    <div>
      <PageHeader
        title="Vault"
        description="Each knowledge branch is encrypted with its own key, and that key is wrapped once per role allowed to read it. A role without a wrapped copy structurally cannot open the branch — not hidden, impossible."
        actions={
          <div className="flex items-center gap-1.5">
            <Badge tone={v.enabled ? "success" : "neutral"}>{v.enabled ? "serving from the vault" : "sealed copies only"}</Badge>
            {admin ? <>
              <Button size="sm" variant="outline" onClick={() => setDialog("seal")}><Lock className="size-3.5" /> Seal now</Button>
              <Button size="sm" variant="outline" onClick={() => setDialog("rotate")}><RefreshCw className="size-3.5" /> Rotate role key</Button>
              <Button size="sm" variant="outline" onClick={() => { setBranch(branches[0]?.branch ?? ""); setDialog("revoke"); }}><ShieldAlert className="size-3.5" /> Revoke</Button>
            </> : null}
          </div>
        }
      />

      {!v.enabled ? (
        <p className="mb-4 rounded-lg bg-[var(--info)]/[0.07] px-3 py-2 text-xs text-slate-700">
          The vault holds sealed copies; the server is still reading the plaintext index cache. Start it with <code className="font-mono">RWB_VAULT=on</code> to decrypt branches per session instead.
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel>
          <PanelHeader title="Sealed branches" description="What is encrypted, which roles hold a wrapped key, and whether the branch is decrypted in the process right now." />
          {branches.length ? (
            <table className="w-full text-xs">
              <thead><tr className="bg-slate-900/[0.04] text-left text-[0.65rem] uppercase tracking-wide text-slate-600"><th className="px-3 py-1.5">Branch</th><th className="px-2 py-1.5">Roles with a key</th><th className="px-2 py-1.5">In memory</th><th className="px-2 py-1.5">Opened by</th></tr></thead>
              <tbody>
                {branches.map((b) => {
                  const mine = v.you.can_unwrap.includes(b.branch);
                  return (
                    <tr key={b.branch} className="border-t border-border/60">
                      <td className="px-3 py-1.5">
                        <p className="flex items-center gap-1.5 font-medium text-foreground">{mine ? <LockOpen className="size-3.5 text-[var(--success)]" /> : <Lock className="size-3.5 text-muted-foreground" />}{b.branch}</p>
                        <p className="text-[0.65rem] text-muted-foreground">{mine ? "your role can unwrap this" : "no wrapped key for your role"}</p>
                      </td>
                      <td className="px-2 py-1.5"><span className="flex flex-wrap gap-1">{b.roles_with_key.map((r) => <Badge key={r} tone={r === "admin" ? "danger" : r === "manager" ? "warning" : "info"}>{ROLE_TITLE[r as Role] ?? r}</Badge>)}{!b.roles_with_key.length ? <Badge tone="neutral">nobody</Badge> : null}</span></td>
                      <td className="px-2 py-1.5"><Badge tone={b.in_memory ? "warning" : "success"}>{b.in_memory ? "decrypted" : "sealed"}</Badge>{b.opened_at ? <span className="ml-1 text-[0.65rem] text-muted-foreground">{clock(b.opened_at)}</span> : null}</td>
                      <td className="px-2 py-1.5 text-[0.7rem] text-slate-700">{b.opened_by.join(", ") || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <EmptyState icon={<KeyRound className="size-6" />} title="Nothing sealed yet" description={admin ? "Seal now encrypts every branch index for exactly the roles that may read it." : "An administrator seals the branches."} />
          )}
          {v.plaintext_indexes_on_disk?.length ? (
            <p className="border-t border-border/70 px-4 py-2 text-[0.68rem] text-muted-foreground">
              Plaintext index caches still on disk: {v.plaintext_indexes_on_disk.join(", ")}. Seal with shredding to overwrite and delete them.
            </p>
          ) : null}
        </Panel>

        <div className="space-y-4">
          <Panel>
            <PanelHeader title="Key management" description="A local KMS on this server. No cloud, no external key service." />
            <div className="px-4 py-2">
              <KeyValues rows={[
                ["Master key", v.kms.master_key_source === "env" ? "from environment (removable token)" : v.kms.master_key_source === "file" ? "local file, mode 600" : String(v.kms.master_key_source ?? "—")],
                ["Branches", String(v.kms.branch_count ?? branches.length)],
                ["Wrapped keys", String(v.kms.wrapped_key_count ?? "—")],
                ["Last rotation", v.kms.last_rotation && typeof v.kms.last_rotation === "object" && Object.keys(v.kms.last_rotation).length ? Object.entries(v.kms.last_rotation as Record<string, number>).map(([r, ts]) => `${r} ${relativeTime(ts)}`).join(" · ") : (typeof v.kms.last_rotation === "number" ? relativeTime(v.kms.last_rotation) : "never")],
                ["You", `${v.you.principal} · ${ROLE_TITLE[v.you.role]} · can unwrap ${v.you.can_unwrap.length}`],
              ]} />
              <p className="mt-2 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">Role wrapping keys</p>
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {ROLES.map((r) => <Badge key={r} tone="neutral">{ROLE_TITLE[r]} v{v.kms.role_keys?.[r]?.version ?? 1}</Badge>)}
              </ul>
              <ul className="mt-3 space-y-1 text-[0.7rem] text-slate-600">
                <li><b className="text-foreground">Envelope encryption</b> — data is encrypted once; who can read it is decided by who holds a wrapped copy of its key.</li>
                <li><b className="text-foreground">Rotation</b> — a role's wrapping key is replaced and every branch key it held is re-wrapped; old copies stop working, the data is untouched.</li>
                <li><b className="text-foreground">Revocation</b> — one wrapped copy is deleted; that role loses the branch instantly, no re-encryption.</li>
              </ul>
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Session keyrings" description="Unwrapped keys live only in this process's memory, per session, and are zeroed on sign-out." />
            <div className="px-4 py-2 text-xs">
              <p className="text-slate-700">{v.keyrings.keyrings ?? 0} keyring{(v.keyrings.keyrings ?? 0) === 1 ? "" : "s"} held{v.keyrings.by_role ? ` · ${Object.entries(v.keyrings.by_role).map(([r, n]) => `${r}: ${n}`).join(", ")}` : ""}</p>
              {v.keyrings.sessions?.length ? (
                <ul className="mt-1 space-y-0.5 text-[0.7rem]">
                  {v.keyrings.sessions.map((s, i) => <li key={i}><span className="font-medium text-foreground">{s.principal}</span> ({s.role}) holds {s.branches.length} branch key{s.branches.length === 1 ? "" : "s"}{s.branches.length ? `: ${s.branches.join(", ")}` : ""}</li>)}
                </ul>
              ) : null}
            </div>
          </Panel>
        </div>
      </div>

      <Panel className="mt-4">
        <PanelHeader title="Key events" description="Create, wrap, rotate, revoke — chained." actions={<ChainBadge chain={v.kms.key_events_chain} />} />
        {v.key_events.length ? (
          <ul className="divide-y divide-border/60 text-[0.7rem]">
            {[...v.key_events].reverse().map((e, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 px-4 py-1.5">
                <span className="font-mono text-muted-foreground">{clock(Number(e.ts ?? 0))}</span>
                <Badge tone={String(e.event ?? "").includes("revoke") ? "danger" : String(e.event ?? "").includes("rotat") ? "warning" : "neutral"}>{String(e.event ?? "")}</Badge>
                <span className="text-slate-700">{Object.entries(e).filter(([k]) => !["ts", "event", "seq", "prev_hash", "hash"].includes(k)).map(([k, val]) => `${k}=${typeof val === "object" ? JSON.stringify(val) : String(val)}`).join(" · ")}</span>
              </li>
            ))}
          </ul>
        ) : <EmptyState title="No key events yet" />}
      </Panel>

      {/* ---------------------------------------------------------------- admin dialogs */}
      <Dialog open={dialog === "seal"} onClose={() => setDialog(null)} title="Seal every branch"
              description="Each branch's index is encrypted with a fresh content key, wrapped for exactly the roles its classification allows."
              footer={<><Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button><Button variant="primary" loading={busy} onClick={() => void run(async () => { const out = await api.vaultSeal(shred); return `${out.sealed.length} branch(es) sealed${out.shredded ? ", plaintext shredded" : ""}`; })}>Seal</Button></>}>
        <label className="flex items-start gap-2 text-xs text-slate-700">
          <input type="checkbox" checked={shred} onChange={(e) => setShred(e.target.checked)} className="mt-0.5" />
          <span>Also overwrite and delete the plaintext index caches. The server then needs <code className="font-mono">RWB_VAULT=on</code> (or a rebuild from the knowledge artefacts) to answer.</span>
        </label>
      </Dialog>

      <Dialog open={dialog === "rotate"} onClose={() => setDialog(null)} title="Rotate a role's wrapping key"
              description="Every branch key this role holds is re-wrapped under a new version. Sessions holding the old key must re-open their branches; nothing is re-encrypted."
              footer={<><Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button><Button variant="primary" loading={busy} onClick={() => void run(async () => { const out = await api.vaultRotate(role); return `${ROLE_TITLE[role]} wrapping key rotated to v${out.version}`; })}>Rotate</Button></>}>
        <RolePicker value={role} onChange={setRole} />
      </Dialog>

      <Dialog open={dialog === "revoke"} onClose={() => setDialog(null)} title="Revoke a role from a branch"
              description="Deletes that role's wrapped copy of the branch key and rotates the role's wrapping key. Instant; the ciphertext is untouched."
              footer={<><Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button><Button variant="danger" loading={busy} disabled={!branch} onClick={() => void run(async () => { const out = await api.vaultRevoke(branch, role); return `${ROLE_TITLE[role]} can no longer open ${out.branch}`; })}>Revoke</Button></>}>
        <div className="space-y-3">
          <label className="block text-xs font-medium text-slate-600">Branch
            <select value={branch} onChange={(e) => setBranch(e.target.value)} className="mt-1 h-9 w-full rounded-lg border border-border bg-white/70 px-2 text-sm">
              {branches.map((b) => <option key={b.branch} value={b.branch}>{b.branch}</option>)}
            </select>
          </label>
          <RolePicker value={role} onChange={setRole} />
        </div>
      </Dialog>
    </div>
  );
}

function RolePicker({ value, onChange }: { value: Role; onChange: (r: Role) => void }) {
  return (
    <div role="radiogroup" aria-label="Role" className="flex gap-1 rounded-lg bg-slate-900/[0.05] p-1">
      {ROLES.map((r) => (
        <button key={r} type="button" role="radio" aria-checked={value === r} onClick={() => onChange(r)}
                className={cn("flex-1 rounded-md px-3 py-1.5 text-xs font-medium", value === r ? "bg-white text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
          {ROLE_TITLE[r]}
        </button>
      ))}
    </div>
  );
}

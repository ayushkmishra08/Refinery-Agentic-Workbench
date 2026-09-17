/**
 * The account page: clearance, the authenticator, and the seeded-password warning.
 *
 * Enrolment is two steps because it has to be: a secret stored before the holder has proved their
 * app can produce a code would lock them out of their own second factor. So the secret is minted,
 * shown as a QR payload, and only written once a code comes back that matches it.
 *
 * The code from enrolment is spent by enrolling — single use is the whole point — so the page
 * says plainly that the next sign-in needs the *next* code, which is the one question this flow
 * otherwise generates.
 */
import { AlertCircle, CheckCircle2, KeyRound, ShieldCheck, ShieldOff, Smartphone } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { MfaEnrolment, MfaStatus } from "@/lib/types";
import { ROLE_TITLE, TAG_TONE, useAuth } from "@/store/auth";
import {
  Badge, Button, CopyButton, Dialog, ErrorState, OtpInput, PageHeader, Panel, PanelHeader,
  Skeleton, useToast,
} from "@/ui";

/**
 * The enrolment payload.
 *
 * Deliberately not a rendered QR code. A QR encoder is two hundred lines of Reed-Solomon and mask
 * selection, and a subtly wrong one produces a square that looks right and scans as nothing —
 * which is worse than no square at all, because the user blames their phone. Every authenticator
 * accepts a typed setup key, so the key is what is shown, in readable groups, with the
 * `otpauth://` link beside it: tapping that on a phone opens the authenticator directly.
 */
function EnrolmentSecret({ secret, uri }: { secret: string; uri: string }) {
  const grouped = secret.match(/.{1,4}/g)?.join(" ") ?? secret;
  return (
    <div className="rounded-lg border border-border bg-white/80 p-3.5">
      <p className="text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">Setup key</p>
      <p className="mt-1 break-all font-mono text-base font-semibold tracking-wider text-foreground">{grouped}</p>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <CopyButton text={secret} label="Copy key" />
        <a
          href={uri}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-white/60 px-2.5 text-xs font-medium text-foreground hover:bg-white"
        >
          <Smartphone className="size-3.5" /> Open in authenticator
        </a>
      </div>
    </div>
  );
}


export default function Account() {
  const { session, signOut, refresh } = useAuth();
  const toast = useToast();

  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enrolment, setEnrolment] = useState<MfaEnrolment | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDisable, setConfirmDisable] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setStatus(await api.mfaStatus());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const beginEnrolment = async () => {
    setBusy(true);
    try {
      setEnrolment(await api.mfaEnrol());
      setCode("");
    } catch (err) {
      toast.push({ tone: "danger", message: "Could not start enrolment", detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  const confirmEnrolment = async (value: string) => {
    setBusy(true);
    try {
      await api.mfaConfirm(value);
      setEnrolment(null);
      setCode("");
      await load();
      await refresh();
      toast.push({
        tone: "success",
        message: "Authenticator enrolled",
        detail: "That code is now spent — your next sign-in needs the next one your app shows.",
      });
    } catch (err) {
      toast.push({ tone: "danger", message: "Code not accepted", detail: err instanceof ApiError ? err.message : String(err) });
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    try {
      await api.mfaDisable();
      setConfirmDisable(false);
      await load();
      await refresh();
      toast.push({ tone: "info", message: "Authenticator removed", detail: "This account signs in with a password alone again." });
    } catch (err) {
      toast.push({ tone: "danger", message: "Could not remove it", detail: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!status || !session) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="max-w-3xl">
      <PageHeader title="Account" description="Your clearance and how you prove it." />

      {session.mustChangePassword ? (
        <div className="mb-4 flex gap-2.5 rounded-lg border border-[var(--warning)]/35 bg-[var(--warning)]/[0.08] px-3.5 py-3">
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-[var(--warning)]" />
          <div>
            <p className="text-sm font-semibold text-foreground">This account still uses its seeded password</p>
            <p className="mt-1 text-xs text-slate-600">
              The demo accounts ship with known passwords. Before this workstation is used for real
              work, set <code className="rounded bg-white/70 px-1 font-mono">RWB_ADMIN_PASSWORD</code> and
              its siblings, or run <code className="rounded bg-white/70 px-1 font-mono">setup_security.py --random-passwords</code>.
            </p>
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <Panel>
          <PanelHeader title="Clearance" description="Set by your role; it cannot be changed from this screen." />
          <div className="space-y-2.5 px-4 py-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">{ROLE_TITLE[session.role]}</span>
              <span className="text-xs text-muted-foreground">level {session.level}</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {session.readableTags.map((t) => <Badge key={t} tone={TAG_TONE[t]}>{t}</Badge>)}
            </div>
            <p className="text-xs text-muted-foreground">
              {session.readableDocuments.length} readable document{session.readableDocuments.length === 1 ? "" : "s"}
              {session.withheldDocuments.length ? `, ${session.withheldDocuments.length} above your line` : ""}.
            </p>
          </div>
        </Panel>

        <Panel>
          <PanelHeader
            title="Two-step verification"
            description={status.required_for_role
              ? `Required for ${ROLE_TITLE[session.role]} accounts.`
              : "Not required for your role, but available."}
          />
          <div className="space-y-3 px-4 py-3">
            <div className="flex items-center gap-2">
              {status.enrolled ? (
                <><CheckCircle2 className="size-4 text-[var(--success)]" />
                  <span className="text-sm text-foreground">Authenticator enrolled</span></>
              ) : (
                <><ShieldOff className="size-4 text-muted-foreground" />
                  <span className="text-sm text-foreground">No authenticator on this account</span></>
              )}
            </div>

            {status.enrolment_pending ? (
              <p className="rounded-md bg-[var(--warning)]/[0.08] px-2.5 py-1.5 text-xs text-slate-700">
                Your role requires a second factor and none is enrolled. You can still sign in, but
                enrol now — it takes a minute.
              </p>
            ) : null}

            {status.demo_codes ? (
              <p className="rounded-md bg-[var(--warning)]/[0.08] px-2.5 py-1.5 text-[0.7rem] text-slate-700">
                Demo mode is on: sign-in shows the expected code on screen. Never leave this on
                outside a demonstration.
              </p>
            ) : null}

            <div className="flex gap-2">
              {status.enrolled ? (
                <Button variant="outline" size="sm" onClick={() => setConfirmDisable(true)}>
                  <ShieldOff className="size-3.5" /> Remove authenticator
                </Button>
              ) : (
                <Button variant="primary" size="sm" loading={busy} onClick={() => void beginEnrolment()}>
                  <Smartphone className="size-3.5" /> Enrol authenticator
                </Button>
              )}
            </div>
          </div>
        </Panel>
      </div>

      <div className="mt-4">
        <Button variant="outline" onClick={() => void signOut()}>Sign out</Button>
      </div>

      {/* ---------------------------------------------------------------- enrolment */}
      <Dialog
        open={!!enrolment}
        onClose={() => setEnrolment(null)}
        title="Enrol an authenticator"
        description="Add the secret to your authenticator app, then type the code it shows to prove it works."
        footer={
          <>
            <Button variant="ghost" onClick={() => setEnrolment(null)} disabled={busy}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={code.length !== (enrolment?.digits ?? 6)}
                    onClick={() => void confirmEnrolment(code)}>
              Confirm
            </Button>
          </>
        }
      >
        {enrolment ? (
          <div className="space-y-4">
            <EnrolmentSecret secret={enrolment.secret} uri={enrolment.uri} />
            <p className="text-[0.7rem] text-muted-foreground">
              In Google Authenticator, 1Password or Authy choose "enter a setup key" and paste this.
              {" "}{enrolment.digits} digits, {enrolment.period}-second period.
            </p>

            <div>
              <p className="mb-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
                Code from your app
              </p>
              <OtpInput value={code} onChange={setCode} length={enrolment.digits} autoFocus
                        disabled={busy} onComplete={(v) => void confirmEnrolment(v)} />
            </div>

            <p className="rounded-md bg-[var(--info)]/[0.07] px-2.5 py-2 text-[0.7rem] text-slate-700">
              Nothing is stored until this code checks out. The code you use here is then spent —
              your next sign-in needs the following one.
            </p>
          </div>
        ) : null}
      </Dialog>

      {/* ---------------------------------------------------------------- disable */}
      <Dialog
        open={confirmDisable}
        onClose={() => setConfirmDisable(false)}
        title="Remove the authenticator?"
        description="This account will sign in with a password alone."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDisable(false)} disabled={busy}>Keep it</Button>
            <Button variant="danger" loading={busy} onClick={() => void disable()}>
              <KeyRound className="size-3.5" /> Remove
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-700">
          A password proves someone knows a secret. It does not prove they are you. Removing the
          second factor on a {ROLE_TITLE[session.role].toLowerCase()} account means a leaked password
          is enough to read everything that role can read.
        </p>
      </Dialog>
    </div>
  );
}

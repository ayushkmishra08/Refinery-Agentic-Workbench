/**
 * Signing in — and the second factor, where the role requires one.
 *
 * The workbench asks who you are *before* it takes a question, not after refusing one, so this is
 * the first screen. It is a two-step form because the API is a two-step protocol: the password
 * goes up alone, and a `428` comes back meaning "right password, now the code". Nothing is issued
 * at that point, so a visitor who stops at the code step holds nothing.
 *
 * The panel on the right explains the access model, because the first question a new user asks is
 * "why can't I see that document" and the answer should be on screen before they ask.
 */
import { AlertCircle, KeyRound, Lock, ShieldCheck, Timer } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, MfaRequiredError } from "@/lib/api";
import { countdown } from "@/lib/format";
import type { MfaChallenge, Role, Tag } from "@/lib/types";
import { ROLE_TITLE, useAuth } from "@/store/auth";
import { Badge, Button, Field, Input, OtpInput, Panel } from "@/ui";

/** How many times an unattended code screen re-asks for a code before it waits to be told. */
const MAX_ROTATIONS = 10;

const LADDER: { role: Role; tags: Tag[]; blurb: string }[] = [
  { role: "user", tags: ["INTERNAL"], blurb: "Published standards and vendor references." },
  { role: "manager", tags: ["INTERNAL", "CONFIDENTIAL"], blurb: "Adds unit equipment documentation." },
  { role: "admin", tags: ["INTERNAL", "CONFIDENTIAL", "SECRET"], blurb: "Adds the unit operating manuals." },
];

export default function SignIn() {
  const { signIn, signingIn } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [rotations, setRotations] = useState(0);
  const [stale, setStale] = useState(false);

  // the code on screen expires; show the user how long they have rather than letting it fail silently
  useEffect(() => {
    if (!challenge) return;
    setRemaining(challenge.demo_code_expires_in_seconds ?? challenge.seconds_remaining ?? 0);
    const id = window.setInterval(() => setRemaining((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(id);
  }, [challenge]);

  // A code lives thirty seconds. Someone who reaches this screen with four seconds left would
  // otherwise be looking at a dead code with no way to get a live one short of retyping the
  // password, so the challenge is re-issued as the step rolls over. Re-posting the password hands
  // back a 428 and nothing else: no token is minted until a code checks out. The timer is keyed on
  // the challenge rather than on the ticking countdown, so it arms once per challenge instead of
  // tearing itself down on every tick. After a few rounds an unattended screen stops asking and
  // waits to be told.
  const refreshChallenge = useCallback(async () => {
    try {
      await api.login(username.trim(), password);
    } catch (err) {
      if (err instanceof MfaRequiredError) {
        setChallenge(err.challenge);
        setStale(false);
        return;
      }
      throw err;
    }
  }, [username, password]);

  useEffect(() => {
    if (!challenge || rotations >= MAX_ROTATIONS) return;
    const wait = Math.max(0, challenge.demo_code_expires_in_seconds ?? challenge.seconds_remaining ?? 0);
    let cancelled = false;
    const id = window.setTimeout(() => {
      if (cancelled) return;
      setRotations((n) => n + 1);
      void refreshChallenge().catch(() => { if (!cancelled) setStale(true); });
    }, (wait + 1) * 1000);
    return () => { cancelled = true; window.clearTimeout(id); };
  }, [challenge, rotations, refreshChallenge]);

  const submitPassword = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      try {
        const outcome = await signIn(username.trim(), password);
        if (outcome.kind === "mfa-required") {
          setChallenge(outcome.challenge);
          setCode("");
          setRotations(0);
          setStale(false);
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Sign-in failed.");
      }
    },
    [signIn, username, password],
  );

  const submitCode = useCallback(
    async (value: string) => {
      setError(null);
      try {
        const outcome = await signIn(username.trim(), password, value);
        if (outcome.kind === "mfa-required") {
          setChallenge(outcome.challenge);
          setCode("");
          setError("That code was not accepted. Here is a fresh challenge.");
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "The code was not accepted.");
        setCode("");
      }
    },
    [signIn, username, password],
  );

  const restart = () => {
    setChallenge(null);
    setCode("");
    setError(null);
    setPassword("");
    setRotations(0);
    setStale(false);
  };

  const askForAFreshCode = () => {
    setRotations(0);
    setError(null);
    void refreshChallenge().catch((err) => {
      setStale(true);
      setError(err instanceof ApiError ? err.message : "Could not reach the workstation.");
    });
  };

  const demo = challenge?.demo_code;
  const heading = useMemo(
    () => (challenge ? "Two-step verification" : "Sign in to the workstation"),
    [challenge],
  );

  return (
    <div className="app-canvas flex min-h-screen items-center justify-center p-4">
      <div className="grid w-full max-w-4xl gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.85fr)]">
        {/* ---------------------------------------------------------------- form */}
        <Panel className="p-6">
          <div className="mb-5 flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
              {challenge ? <KeyRound className="size-5" /> : <ShieldCheck className="size-5" />}
            </span>
            <div>
              <h1 className="text-base font-semibold text-foreground">{heading}</h1>
              <p className="text-xs text-muted-foreground">MRPL Engineering AI Workstation</p>
            </div>
          </div>

          {error ? (
            <div role="alert" className="mb-4 flex gap-2 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/[0.06] px-3 py-2.5">
              <AlertCircle className="mt-0.5 size-4 shrink-0 text-[var(--danger)]" />
              <p className="text-xs text-slate-700">{error}</p>
            </div>
          ) : null}

          {!challenge ? (
            <form onSubmit={submitPassword} className="space-y-3.5">
              <Field label="Username">
                <Input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoComplete="username"
                  autoFocus
                  required
                  placeholder="user, manager or admin"
                />
              </Field>
              <Field label="Password">
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                />
              </Field>
              <Button type="submit" variant="primary" className="w-full" loading={signingIn}>
                Continue
              </Button>
              <p className="pt-1 text-center text-[0.7rem] text-muted-foreground">
                The workstation asks who you are before it takes a question. Until you sign in,
                no document is readable and nothing is searched.
              </p>
            </form>
          ) : (
            <div className="space-y-4">
              <p className="text-xs text-slate-600">
                Your password was accepted. {ROLE_TITLE[challenge.role]} accounts also need the{" "}
                {challenge.digits}-digit code from your authenticator app.
              </p>

              <OtpInput
                value={code}
                onChange={setCode}
                length={challenge.digits}
                autoFocus
                disabled={signingIn}
                onComplete={submitCode}
              />

              <div className="flex items-center gap-1.5 text-[0.7rem] text-muted-foreground">
                <Timer className="size-3" />
                {remaining > 0 ? (
                  <>Code rotates in {countdown(remaining)}</>
                ) : rotations >= MAX_ROTATIONS || stale ? (
                  <>
                    This code has rotated.{" "}
                    <button type="button" onClick={askForAFreshCode} className="underline underline-offset-2 hover:text-foreground">
                      Get a fresh one
                    </button>
                  </>
                ) : (
                  <>Fetching the next code…</>
                )}
              </div>

              {demo ? (
                <div className="rounded-lg border border-[var(--warning)]/35 bg-[var(--warning)]/[0.08] px-3 py-2.5">
                  <p className="flex items-center gap-1.5 text-[0.7rem] font-semibold text-[color-mix(in_oklab,var(--warning),black_30%)]">
                    <AlertCircle className="size-3" /> Demo mode
                  </p>
                  <p className="mt-1 text-xs text-slate-700">
                    No phone is enrolled on this machine, so the expected code is shown:{" "}
                    <button
                      type="button"
                      onClick={() => { setCode(demo); void submitCode(demo); }}
                      className="rounded bg-white/80 px-1.5 py-0.5 font-mono text-sm font-bold tracking-widest text-foreground underline-offset-2 hover:underline"
                    >
                      {demo}
                    </button>
                    {challenge.demo_code_valid_in_seconds ? (
                      <span className="ml-1 text-muted-foreground">
                        (valid in {countdown(challenge.demo_code_valid_in_seconds)})
                      </span>
                    ) : null}
                  </p>
                  <p className="mt-1 text-[0.65rem] text-muted-foreground">
                    Never enable this outside a demonstration — it is switched on by RWB_OTP_DEMO.
                  </p>
                </div>
              ) : null}

              <div className="flex gap-2">
                <Button variant="primary" className="flex-1" loading={signingIn}
                        disabled={code.length !== challenge.digits} onClick={() => submitCode(code)}>
                  Verify and sign in
                </Button>
                <Button variant="ghost" onClick={restart}>Back</Button>
              </div>
            </div>
          )}
        </Panel>

        {/* ---------------------------------------------------------------- the model */}
        <Panel className="hidden p-6 lg:block">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Lock className="size-4 text-primary" /> What you will be able to read
          </h2>
          <p className="mt-1.5 text-xs text-muted-foreground">
            Every document carries a classification, every account a role. A role reads a document
            when its level reaches the document's — its own tier in full, and nothing above it.
            A document can also name its readers outright, in which case it is read by exactly
            those roles and seniority does not carry.
          </p>

          <ul className="mt-4 space-y-2.5">
            {LADDER.map((row) => (
              <li key={row.role} className="rounded-lg border border-border/70 bg-white/55 px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-semibold text-foreground">{ROLE_TITLE[row.role]}</span>
                  {row.tags.map((t) => (
                    <Badge key={t} tone={t === "SECRET" ? "danger" : t === "CONFIDENTIAL" ? "warning" : "info"}>{t}</Badge>
                  ))}
                </div>
                <p className="mt-1 text-[0.7rem] text-muted-foreground">{row.blurb}</p>
              </li>
            ))}
          </ul>

          <div className="mt-4 rounded-lg border border-border/70 bg-white/45 px-3 py-2.5">
            <p className="text-[0.7rem] font-semibold text-foreground">Need something above your line?</p>
            <p className="mt-1 text-[0.7rem] text-muted-foreground">
              Ask anyway. The answer comes from what you can read, and the workstation tells you how
              much restricted material bears on the question — never what it says — then raises a
              request with the lowest role that can release it. Approval opens the records that
              answer that one question, in that one branch, once. Your role does not change and
              nothing else in the document opens.
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

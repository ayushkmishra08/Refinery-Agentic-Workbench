/**
 * Who is signed in, and what that entitles them to in the interface.
 *
 * Two things this deliberately does **not** do.
 *
 * *It persists a session for the life of the tab, and no longer.* The token is mirrored to
 * `sessionStorage`, so a reload or a navigation picks the same session back up — that is what
 * lets a conversation be continued where it was left. It is not `localStorage`: closing the tab
 * ends the session, and nothing is left behind on a shared control-room terminal for the next
 * person to find. The stored copy is presented to the API on start-up and dropped the moment the
 * server stops recognising it; it never decides anything on its own.
 *
 * *It does not decide what you may read.* `role` here drives navigation and labels only. Every
 * document, claim and passage is filtered by the server before it is sent; if this file were
 * edited in a debugger to say "admin", the API would still answer as the real role. The UI
 * reflects the rule — it does not implement it.
 */
import {
  type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";

import { ApiError, MfaRequiredError, api, setToken, setUnauthorizedHandler } from "@/lib/api";
import type { LoginResult, MfaChallenge, Role, Tag } from "@/lib/types";

export interface Session {
  token: string;
  username: string;
  role: Role;
  level: number;
  readableTags: Tag[];
  readableDocuments: string[];
  withheldDocuments: string[];
  expires: number;
  mustChangePassword: boolean;
  mfaEnrolled: boolean;
  mfaSatisfied: boolean;
  mfaEnrolmentPending: boolean;
}

export type SignInOutcome =
  | { kind: "signed-in"; session: Session }
  | { kind: "mfa-required"; challenge: MfaChallenge };

interface AuthValue {
  session: Session | null;
  ready: boolean;
  signingIn: boolean;
  signIn: (username: string, password: string, code?: string) => Promise<SignInOutcome>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  /** Roles at or above the argument. Navigation only — never a data gate. */
  atLeast: (role: Role) => boolean;
  expiresInSeconds: number | null;
}

const ROLE_LEVEL: Record<Role, number> = { guest: 0, user: 1, manager: 2, admin: 3 };

const AuthContext = createContext<AuthValue | null>(null);

function toSession(r: LoginResult): Session {
  return {
    token: r.token,
    username: r.username,
    role: r.role,
    level: r.level,
    readableTags: r.readable_tags ?? [],
    readableDocuments: r.readable_documents ?? [],
    withheldDocuments: r.withheld_documents ?? [],
    expires: r.expires,
    mustChangePassword: !!r.must_change_password,
    mfaEnrolled: !!r.mfa_enrolled,
    mfaSatisfied: !!r.mfa_satisfied,
    mfaEnrolmentPending: !!r.mfa_enrolment_pending,
  };
}

const STORAGE_KEY = "rwb:session";

function storeSession(s: Session): void {
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s)); } catch { /* private mode or quota; the tab just will not survive a reload */ }
}

function readStoredSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    if (!parsed || typeof parsed.token !== "string" || !parsed.token) return null;
    // an expired token is not worth presenting; the server would refuse it anyway
    if (parsed.expires && parsed.expires * 1000 <= Date.now()) return null;
    return parsed;
  } catch {
    return null;
  }
}

function forgetStoredSession(): void {
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* nothing to forget */ }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [signingIn, setSigningIn] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const clearing = useRef(false);

  // the API tells us when our token stopped being recognised; drop it rather than letting the
  // user keep clicking into 401s
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (clearing.current) return;
      clearing.current = true;
      setToken(null);
      setSession(null);
      forgetStoredSession();
      window.setTimeout(() => { clearing.current = false; }, 0);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  // Pick the session back up after a reload. The token lives in *sessionStorage*: it survives a
  // refresh and a navigation, and it dies with the tab — not localStorage, which would keep a
  // clearance signed in on a shared machine after the browser was closed. The stored copy is not
  // trusted on its own; it is presented to the API, and if the server no longer recognises it
  // (expired, revoked, restarted with a new secret) the screen goes back to sign-in cleanly.
  useEffect(() => {
    const stored = readStoredSession();
    if (!stored) { setReady(true); return; }
    let cancelled = false;
    setToken(stored.token);
    api.whoami()
      .then((who) => {
        if (cancelled) return;
        if (!who.authenticated) throw new Error("token no longer recognised");
        setSession({
          ...stored,
          role: who.role,
          level: who.level,
          readableTags: who.readable_tags ?? stored.readableTags,
          readableDocuments: who.readable_documents ?? stored.readableDocuments,
          withheldDocuments: who.withheld_documents ?? stored.withheldDocuments,
          mfaEnrolled: who.mfa_enrolled ?? stored.mfaEnrolled,
          mfaEnrolmentPending: who.mfa_enrolment_pending ?? stored.mfaEnrolmentPending,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setToken(null);
        setSession(null);
        forgetStoredSession();
      })
      .finally(() => { if (!cancelled) setReady(true); });
    return () => { cancelled = true; };
  }, []);

  // one ticking clock for the whole app, so the expiry countdown does not need a timer per view
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const signIn = useCallback(async (username: string, password: string, code?: string): Promise<SignInOutcome> => {
    setSigningIn(true);
    try {
      const result = await api.login(username, password, code);
      const next = toSession(result);
      setToken(next.token);
      setSession(next);
      storeSession(next);
      return { kind: "signed-in", session: next };
    } catch (err) {
      if (err instanceof MfaRequiredError) return { kind: "mfa-required", challenge: err.challenge };
      throw err;
    } finally {
      setSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch (err) {
      // a token the server already forgot is still a signed-out user as far as we are concerned
      if (!(err instanceof ApiError)) throw err;
    } finally {
      setToken(null);
      setSession(null);
      forgetStoredSession();
    }
  }, []);

  /** Re-read the caller's clearance, e.g. after enrolling an authenticator. */
  const refresh = useCallback(async () => {
    if (!session) return;
    const who = await api.whoami();
    setSession((prev) => prev && {
      ...prev,
      role: who.role,
      level: who.level,
      readableTags: who.readable_tags ?? prev.readableTags,
      readableDocuments: who.readable_documents ?? prev.readableDocuments,
      withheldDocuments: who.withheld_documents ?? prev.withheldDocuments,
      mfaEnrolled: who.mfa_enrolled ?? prev.mfaEnrolled,
      mfaEnrolmentPending: who.mfa_enrolment_pending ?? prev.mfaEnrolmentPending,
    });
  }, [session]);

  const atLeast = useCallback(
    (role: Role) => ROLE_LEVEL[session?.role ?? "guest"] >= ROLE_LEVEL[role],
    [session],
  );

  const expiresInSeconds = session?.expires ? Math.max(0, Math.round(session.expires - now / 1000)) : null;

  // an expired token is not usable; drop it the moment the clock says so
  useEffect(() => {
    if (session && expiresInSeconds === 0) {
      setToken(null);
      setSession(null);
      forgetStoredSession();
    }
  }, [session, expiresInSeconds]);

  const value = useMemo<AuthValue>(
    () => ({ session, ready, signingIn, signIn, signOut, refresh, atLeast, expiresInSeconds }),
    [session, ready, signingIn, signIn, signOut, refresh, atLeast, expiresInSeconds],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export const ROLE_TITLE: Record<Role, string> = {
  guest: "Guest", user: "Engineer", manager: "Manager", admin: "Administrator",
};

export const TAG_TONE: Record<Tag, "info" | "warning" | "danger"> = {
  INTERNAL: "info", CONFIDENTIAL: "warning", SECRET: "danger",
};

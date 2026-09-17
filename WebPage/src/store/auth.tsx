/**
 * Who is signed in, and what that entitles them to in the interface.
 *
 * Two things this deliberately does **not** do.
 *
 * *It does not persist a session.* The token lives in memory for the life of the tab. A reload
 * signs you out. That was asked for, and it is also the safer default for a shared control-room
 * terminal: closing the tab ends the session, and nothing sensitive is left in `localStorage`
 * for the next person or for any script on the page to read.
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
      window.setTimeout(() => { clearing.current = false; }, 0);
    });
    setReady(true);
    return () => setUnauthorizedHandler(null);
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

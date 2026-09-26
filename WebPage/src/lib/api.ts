/**
 * The only module that talks to the workbench.
 *
 * Every call goes through `request()`, so the bearer token, the error shape and the "your
 * session expired" path are handled once. Two rules the rest of the app depends on:
 *
 * **The server decides.** Nothing here filters or hides anything — the API already returns only
 * what the caller's role may read. The UI renders what it is given; it never re-implements the
 * access rule, because two copies of a security rule is one copy too many.
 *
 * **Refusals are answers, not errors.** A question the caller is not cleared for comes back as a
 * normal 200 with `status: "clarification"` and a populated `security` envelope. Only transport
 * and auth problems throw.
 */
import type {
  AccessRequest, AgentDescription, ApprovalResult, Block, ConversationSummary, DocumentRolesResult, FinalResponse,
  Health, IngestRun, KnowledgeTree, LoginResult, MfaChallenge, MfaEnrolment, MfaStatus, Role,
  SecurityOverview, SessionSnapshot, SupervisedConversation, UploadResult, WhoAmI, WorkspaceStats,
} from "@/lib/types";
import type {
  ChainRow, Draft, DraftsResponse, EgressGuardStatus, ExportResult, IntakeResult, ModelsOverview, PackageLog,
  PackageVerification, RecordedResponse, RegisterModelBody, RoutingLog, RoutingPlan, SandboxInfo, SandboxManifest,
  SandboxResult, SealResult, SovereigntyReport, ToolCallEntry, ToolDescription, ToolResult, ToolRunReport,
  VaultStatus, WorkspaceFile, ChainState, ConnectionEntry, NetworkMonitorStatus,
} from "@/lib/types_ext";

const BASE = import.meta.env.VITE_WORKBENCH_URL ? String(import.meta.env.VITE_WORKBENCH_URL) : "/api";

/** Thrown for transport and auth failures. Carries the status so callers can react to 401/428. */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Raised on HTTP 428: password accepted, authenticator code still owed. */
export class MfaRequiredError extends ApiError {
  challenge: MfaChallenge;
  constructor(challenge: MfaChallenge) {
    super(428, challenge.detail || "An authenticator code is required.", challenge);
    this.name = "MfaRequiredError";
    this.challenge = challenge;
  }
}

let authToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setToken(token: string | null) {
  authToken = token;
}
export function getToken() {
  return authToken;
}
/** Called when the server stops recognising our token, so the shell can return to sign-in. */
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if (typeof d.detail === "string") return d.detail;
    if (typeof d.message === "string") return d.message;
  }
  return fallback;
}

async function request<T>(
  path: string,
  init: RequestInit & { raw?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch (cause) {
    throw new ApiError(0, "The workbench is not reachable. Check that the API is running.", cause);
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let body: unknown = text;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    /* a non-JSON error page; keep the text */
  }

  if (res.ok) return body as T;

  const detail = (body as { detail?: unknown } | null)?.detail ?? body;

  if (res.status === 428 && detail && typeof detail === "object" && "mfa_required" in (detail as object)) {
    throw new MfaRequiredError(detail as MfaChallenge);
  }
  if (res.status === 401 && authToken) {
    // the token we hold is no longer good; let the shell decide what to do about it
    onUnauthorized?.();
  }
  throw new ApiError(res.status, messageFrom(detail, `Request failed (${res.status}).`), detail);
}

// ---------------------------------------------------------------- auth

export const api = {
  health: () => request<Health>("/health"),
  agents: () => request<AgentDescription[]>("/agents"),

  login: (username: string, password: string, code?: string) =>
    request<LoginResult>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, label: "workstation", code: code || null }),
    }),
  logout: () => request<{ revoked: boolean }>("/auth/logout", { method: "POST" }),
  whoami: () => request<WhoAmI>("/auth/whoami"),

  mfaStatus: () => request<MfaStatus>("/auth/mfa"),
  mfaEnrol: () => request<MfaEnrolment>("/auth/mfa/enrol", { method: "POST" }),
  mfaConfirm: (code: string) =>
    request<{ username: string; enrolled: boolean }>("/auth/mfa/confirm", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  mfaDisable: (username?: string) =>
    request<{ username: string; enrolled: boolean }>(
      `/auth/mfa${username ? `?username=${encodeURIComponent(username)}` : ""}`,
      { method: "DELETE" },
    ),

  // ---------------------------------------------------------------- the knowledge layer
  knowledgeTree: () => request<KnowledgeTree>("/knowledge/tree"),
  /** Pin who reads one branch, or pass null to hand it back to the tag ladder. Admin only. */
  setDocumentRoles: (documentId: string, roles: Role[] | null) =>
    request<DocumentRolesResult>(`/security/documents/${encodeURIComponent(documentId)}/roles`, {
      method: "POST",
      body: JSON.stringify({ roles }),
    }),

  // ---------------------------------------------------------------- asking

  /** Start a run in the background. Progress arrives on the event stream; see `streamRun`. */
  startRun: (body: { text: string; session_id: string; access_key?: string | null; options?: Record<string, unknown> }) =>
    request<{ run_id: string; session_id: string }>("/runs", { method: "POST", body: JSON.stringify(body) }),

  getRun: (runId: string) =>
    request<{ run_id: string; finished: boolean; final?: FinalResponse | null; error?: string | null }>(`/runs/${runId}`),

  /** Synchronous ask, for callers that do not want the stream (used by the retry-with-key path). */
  ask: (body: { text: string; session_id: string; access_key?: string | null; options?: Record<string, unknown> }) =>
    request<FinalResponse>("/ask", { method: "POST", body: JSON.stringify(body) }),

  /** The "btw" side channel: ask about a run while it is still going. */
  btw: (text: string, opts: { runId?: string; sessionId?: string }) =>
    opts.runId
      ? request<{ run_id: string; blocks: Block[] }>(`/runs/${opts.runId}/btw`, {
          method: "POST",
          body: JSON.stringify({ text }),
        })
      : request<{ run_id?: string; blocks: Block[] }>("/btw", {
          method: "POST",
          body: JSON.stringify({ text, session_id: opts.sessionId ?? null }),
        }),

  // ---------------------------------------------------------------- conversations
  /** The caller's own conversations, newest first. */
  conversations: () => request<ConversationSummary[]>("/sessions"),
  /** One conversation's turns and attachments, to redraw it and carry on. */
  conversation: (sessionId: string) => request<SessionSnapshot>(`/sessions/${encodeURIComponent(sessionId)}`),
  deleteConversation: (sessionId: string) =>
    request<{ deleted: boolean }>(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),

  /** Conversations of the ranks below the caller (manager, admin). Each read is audited. */
  supervisedConversations: () => request<SupervisedConversation[]>("/logs/conversations"),
  supervisedConversation: (owner: string, sessionId: string) =>
    request<SessionSnapshot>(`/logs/conversations/${encodeURIComponent(owner)}/${encodeURIComponent(sessionId)}`),

  /** Follow an ingest run: phase, final_status, error. */
  ingestStatus: (runId: string) => request<IngestRun>(`/runs/${runId}`),

  /** Forget everything uploaded into one conversation. */
  dropUploads: (sessionId: string) =>
    request<{ dropped: number }>(`/upload?session_id=${encodeURIComponent(sessionId)}`, { method: "DELETE" }),

  upload: (file: File, sessionId: string, note = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("session_id", sessionId);
    form.append("note", note);
    return request<UploadResult>("/upload", { method: "POST", body: form });
  },

  // ---------------------------------------------------------------- security

  security: () => request<SecurityOverview>("/security"),
  stats: () => request<WorkspaceStats>("/stats"),

  myAccessRequests: () => request<AccessRequest[]>("/access-requests"),
  requestAccess: (text: string, sessionId: string) =>
    request<{ request: AccessRequest; detail?: string }>("/access-requests", {
      method: "POST",
      body: JSON.stringify({ text, session_id: sessionId }),
    }),

  approvals: (showAll = false) => request<AccessRequest[]>(`/approvals?show_all=${showAll}`),
  approve: (requestId: string, note = "") =>
    request<ApprovalResult>(`/approvals/${requestId}/approve`, { method: "POST", body: JSON.stringify({ note }) }),
  deny: (requestId: string, note = "") =>
    request<ApprovalResult>(`/approvals/${requestId}/deny`, { method: "POST", body: JSON.stringify({ note }) }),

  reviews: () => request<Record<string, unknown>[]>("/reviews"),
  decideReview: (responseId: string, decision: "approved" | "rejected", note = "") =>
    request<Record<string, unknown>>(`/reviews/${responseId}`, {
      method: "POST",
      body: JSON.stringify({ decision, reviewer: "workstation", note }),
    }),

  audit: (sessionId: string, auditId?: string) =>
    request<Record<string, unknown>[]>(
      `/audit/${encodeURIComponent(sessionId)}${auditId ? `?audit_id=${encodeURIComponent(auditId)}` : ""}`,
    ),

  // ---------------------------------------------------------------- models & routing
  models: () => request<ModelsOverview>("/models"),
  routingLog: (limit = 50, runId?: string) =>
    request<RoutingLog>(`/models/routing?limit=${limit}${runId ? `&run_id=${encodeURIComponent(runId)}` : ""}`),
  routeText: (text: string) => request<RoutingPlan>("/models/route", { method: "POST", body: JSON.stringify({ text }) }),
  registerModel: (body: RegisterModelBody) =>
    request<{ registered: string; local_registry: string; installed: boolean }>("/models/register", {
      method: "POST", body: JSON.stringify(body),
    }),
  packages: () => request<PackageLog>("/models/packages"),
  verifyPackage: (path: string) =>
    request<PackageVerification>("/models/packages/verify", { method: "POST", body: JSON.stringify({ path }) }),
  importPackage: (path: string, dryRun = true) =>
    request<{ verification: PackageVerification; imported?: boolean; dry_run?: boolean; model?: string }>(
      "/models/packages/import", { method: "POST", body: JSON.stringify({ path, dry_run: dryRun }) },
    ),

  // ---------------------------------------------------------------- sovereignty
  sovereignty: (deep = false) => request<SovereigntyReport>(`/sovereignty?deep=${deep}`),
  connections: (limit = 100, externalOnly = false, event?: string) =>
    request<{ entries: ConnectionEntry[]; status: NetworkMonitorStatus }>(
      `/sovereignty/connections?limit=${limit}&external_only=${externalOnly}${event ? `&event=${encodeURIComponent(event)}` : ""}`,
    ),
  verifyChains: () => request<{ chains: ChainRow[]; intact: boolean; checked: number }>("/sovereignty/verify", { method: "POST" }),
  egress: (limit = 50) => request<{ guard: EgressGuardStatus; entries: Record<string, unknown>[]; chain: ChainState }>(`/sovereignty/egress?limit=${limit}`),

  // ---------------------------------------------------------------- vault
  vault: () => request<VaultStatus>("/vault"),
  vaultSeal: (shred: boolean) => request<SealResult>("/vault/seal", { method: "POST", body: JSON.stringify({ shred }) }),
  vaultRotate: (role: Role) => request<{ role: string; version: number }>(`/vault/rotate/${role}`, { method: "POST" }),
  vaultRevoke: (branch: string, role: Role) =>
    request<{ branch: string; role: string; roles_with_key: string[] }>("/vault/revoke", {
      method: "POST", body: JSON.stringify({ branch, role }),
    }),

  // ---------------------------------------------------------------- tools & sandbox
  tools: () => request<{ tools: ToolDescription[]; sandbox: SandboxInfo }>("/tools"),
  runTool: (tool: string, args: Record<string, unknown>, sessionId: string) =>
    request<ToolResult>("/tools/run", { method: "POST", body: JSON.stringify({ tool, args, session_id: sessionId }) }),
  runAgent: (goal: string, sessionId: string, maxIterations = 8) =>
    request<ToolRunReport>("/tools/agent", {
      method: "POST", body: JSON.stringify({ goal, session_id: sessionId, max_iterations: maxIterations }),
    }),
  workspace: (sessionId: string) =>
    request<{ workspace: string; files: WorkspaceFile[] }>(`/tools/workspace?session_id=${encodeURIComponent(sessionId)}`),
  workspaceFilePath: (sessionId: string, path: string) =>
    `/tools/workspace/file?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,
  toolCalls: (limit = 100) => request<{ entries: ToolCallEntry[]; chain: ChainState }>(`/tools/calls?limit=${limit}`),
  sandboxRun: (body: { code: string; tests?: string | null; inputs?: Record<string, string>; task_id?: string }) =>
    request<SandboxResult>("/sandbox/run", { method: "POST", body: JSON.stringify(body) }),
  sandboxRuns: (limit = 50) =>
    request<{ entries: Record<string, unknown>[]; chain: ChainState; sandbox: SandboxInfo }>(`/sandbox/runs?limit=${limit}`),
  sandboxManifest: () => request<SandboxManifest>("/sandbox/manifest"),

  // ---------------------------------------------------------------- intake
  intake: (file: File, sessionId: string, opts: { purpose?: string; runVision?: boolean; maxPages?: number; createDraft?: boolean } = {}) => {
    const form = new FormData();
    form.append("file", file);
    form.append("session_id", sessionId);
    form.append("purpose", opts.purpose ?? "general");
    form.append("run_vision", String(opts.runVision ?? true));
    form.append("max_pages", String(opts.maxPages ?? 6));
    form.append("create_draft", String(opts.createDraft ?? true));
    return request<IntakeResult>("/intake", { method: "POST", body: form });
  },

  // ---------------------------------------------------------------- deliverables & review
  exportDeliverable: (responseId: string, format: "docx" | "pptx" | "xlsx" | "md", title?: string) =>
    request<ExportResult>("/deliverables", {
      method: "POST", body: JSON.stringify({ response_id: responseId, format, title: title ?? null }),
    }),
  recordedResponses: (limit = 30) => request<RecordedResponse[]>(`/deliverables/responses?limit=${limit}`),
  drafts: (status?: string) => request<DraftsResponse>(`/drafts${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  draft: (draftId: string) => request<Draft>(`/drafts/${encodeURIComponent(draftId)}`),
  resolveFigure: (draftId: string, figureId: string, body: { action: "accepted" | "corrected" | "removed"; corrected_value?: string | null; note?: string }) =>
    request<Draft>(`/drafts/${encodeURIComponent(draftId)}/figures/${encodeURIComponent(figureId)}/resolve`, {
      method: "POST", body: JSON.stringify(body),
    }),
  signoffDraft: (draftId: string, note = "") =>
    request<Draft>(`/drafts/${encodeURIComponent(draftId)}/signoff`, { method: "POST", body: JSON.stringify({ note }) }),
  rejectDraft: (draftId: string, note = "") =>
    request<Draft>(`/drafts/${encodeURIComponent(draftId)}/reject`, { method: "POST", body: JSON.stringify({ note }) }),
};

/**
 * Fetch a file the API serves (a deliverable, a workspace file) with the bearer token and hand
 * back a Blob. A plain link cannot carry the Authorization header, which is why this exists.
 */
export async function download(path: string): Promise<Blob> {
  const headers = new Headers();
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { headers });
  } catch (cause) {
    throw new ApiError(0, "The workbench is not reachable. Check that the API is running.", cause);
  }
  if (!res.ok) {
    let detail: unknown = null;
    try { detail = (await res.json())?.detail; } catch { /* not JSON */ }
    if (res.status === 401 && authToken) onUnauthorized?.();
    throw new ApiError(res.status, messageFrom(detail, `Download failed (${res.status}).`), detail);
  }
  return res.blob();
}

/** Save a Blob through the browser's download prompt. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------------------------------------------------------------- progress stream

/**
 * Subscribe to a run's progress.
 *
 * `EventSource` cannot carry an Authorization header, and the events describe retrieval over
 * documents, so this reads the SSE body with `fetch` and parses the frames itself. Returns a
 * function that aborts the stream — call it when the component unmounts, or a finished run keeps
 * a connection open.
 */
export function streamRun(
  runId: string,
  handlers: {
    onEvent?: (ev: import("@/lib/types").ProgressEvent) => void;
    onFinal?: (payload: { response_id?: string; status?: string }) => void;
    onError?: (err: Error) => void;
    onClose?: () => void;
  },
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const headers = new Headers({ Accept: "text/event-stream" });
      if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
      const res = await fetch(`${BASE}/runs/${runId}/events`, { headers, signal: controller.signal });
      if (!res.ok || !res.body) throw new ApiError(res.status, `Progress stream refused (${res.status}).`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let split = buffer.indexOf("\n\n");
        while (split !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          split = buffer.indexOf("\n\n");

          let name = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;
          let payload: unknown;
          try {
            payload = JSON.parse(dataLines.join("\n"));
          } catch {
            continue;
          }
          if (name === "final") {
            handlers.onFinal?.(payload as { response_id?: string; status?: string });
            continue;
          }
          // The server replays whatever already happened before we connected, but the replayed
          // frames are a reduced shape — no phase, no step_id, no reasoning text. Folding those
          // in alongside the live ones produces a duplicated, half-empty trace, so they are
          // dropped: we subscribe immediately after starting the run, and the fold builds a phase
          // on demand for anything that arrives without one.
          if ((payload as { replay?: boolean }).replay) continue;
          handlers.onEvent?.(payload as import("@/lib/types").ProgressEvent);
        }
      }
      handlers.onClose?.();
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      handlers.onError?.(err as Error);
    }
  })();

  return () => controller.abort();
}

/**
 * The workbench API contract, as the browser sees it.
 *
 * These mirror the pydantic models in `workbench/core` — `FinalResponse`, the block union and
 * the security envelope. They are hand-written rather than generated so the fields the UI
 * actually depends on are visible in one place; `GET /schema` on the API returns the
 * authoritative JSON Schema if they ever need checking against it.
 */

export type Role = "guest" | "user" | "manager" | "admin";
export type Tag = "INTERNAL" | "CONFIDENTIAL" | "SECRET";
export type Effort = "low" | "medium" | "high" | "ultra";

/** answered | clarification | restricted | unauthorized | blocked | failed | needs_review */
export type AnswerStatus =
  | "answered"
  | "clarification"
  | "restricted"
  | "unauthorized"
  | "blocked"
  | "failed"
  | "needs_review";

// ---------------------------------------------------------------- blocks

export interface BlockBase {
  id: string;
  type: string;
  title?: string | null;
  citations?: string[];
}

export interface TextBlock extends BlockBase { type: "text"; markdown: string }
export interface CalloutBlock extends BlockBase {
  type: "callout";
  level: "info" | "success" | "warning" | "danger";
  markdown: string;
}
export interface KpiItem {
  label: string; value: string; unit?: string | null;
  qualifier?: string | null; citation?: string | null;
}
export interface KpiBlock extends BlockBase { type: "kpi"; items: KpiItem[] }
export interface TableBlock extends BlockBase {
  type: "table";
  columns: string[];
  rows: (string | number | null)[][];
  row_citations?: string[][];
  caption?: string | null;
}
export interface StepItem {
  sequence: number; text: string; page?: number | null; citation?: string | null;
  is_prerequisite?: boolean; warnings?: string[]; mentions?: string[];
}
export interface StepsBlock extends BlockBase {
  type: "steps";
  procedure_type?: string | null;
  section_path?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  prerequisites?: StepItem[];
  steps: StepItem[];
}
export interface GraphNode { id: string; label: string; type?: string | null; is_focus?: boolean }
export interface GraphEdge { source: string; target: string; label: string; citation?: string | null; inferred?: boolean }
export interface GraphBlock extends BlockBase { type: "graph"; nodes: GraphNode[]; edges: GraphEdge[]; mermaid?: string | null }
export interface PlanTask {
  id: string; title: string; agent: string; depends_on: string[];
  status: "pending" | "running" | "done" | "failed" | "skipped";
  summary?: string | null; safety_sensitive?: boolean;
}
export interface PlanBlock extends BlockBase { type: "plan"; goal: string; tasks: PlanTask[] }
export interface EvidenceItem {
  ref: string; document_id: string; page?: number | null; section_path?: string | null;
  source?: string; excerpt?: string; text?: string; revision?: string | null;
}
export interface EvidenceBlock extends BlockBase { type: "evidence"; items: EvidenceItem[] }
export interface ComparisonCell { value?: string | null; unit?: string | null; citation?: string | null }
export interface ComparisonBlock extends BlockBase {
  type: "comparison"; subjects: string[]; attributes: string[];
  cells: ComparisonCell[][]; differences?: string[];
}
export interface LimitMarker { label: string; value: number }
export interface LimitGaugeBlock extends BlockBase {
  type: "limit_gauge"; entity: string; parameter: string;
  value?: number | null; unit: string; markers: LimitMarker[];
  verdict: "within_normal" | "within_design" | "outside_design" | "unknown";
  message: string;
}
export interface ConflictClaim {
  value: string; unit?: string | null; document_id: string; revision?: string | null;
  page?: number | null; source?: string; context?: string | null; citation?: string | null;
}
export interface ConflictBlock extends BlockBase {
  type: "conflict"; subject: string; parameter: string; status: string;
  claims: ConflictClaim[]; preferred_index?: number | null; resolution?: string | null;
}
export interface SafetyFlagItem {
  severity: "info" | "caution" | "warning" | "danger";
  message: string; citation?: string | null; requires_authorization?: boolean;
}
export interface SafetyBlock extends BlockBase { type: "safety"; flags: SafetyFlagItem[] }
export interface ConfidenceBlock extends BlockBase {
  type: "confidence"; score: number; level: string; basis: string; uncertainties?: string[];
}
export interface ClarificationBlock extends BlockBase {
  type: "clarification"; question: string; missing: string[]; options?: string[];
}
export interface AuditPhaseRow { name: string; agent: string; status: string; duration_ms: number; note?: string | null }
export interface AuditBlock extends BlockBase {
  type: "audit"; audit_id: string; phases?: AuditPhaseRow[];
  llm_calls?: number; backend?: string;
}
export interface ImageBlock extends BlockBase { type: "image"; url?: string; caption?: string | null }

export type Block =
  | TextBlock | CalloutBlock | KpiBlock | TableBlock | StepsBlock | GraphBlock | PlanBlock
  | EvidenceBlock | ComparisonBlock | LimitGaugeBlock | ConflictBlock | SafetyBlock
  | ConfidenceBlock | ClarificationBlock | AuditBlock | ImageBlock;

/** Blocks that restate the reasoning rather than the answer; the chat hides them behind the trace. */
export const REASONING_BLOCKS = new Set(["confidence", "audit", "plan"]);

// ---------------------------------------------------------------- response

export interface SecurityEnvelope {
  access_control: boolean;
  principal: string;
  role: Role;
  authenticated: boolean;
  classification: Tag | null;
  source_documents: string[];
  readable_documents: string[];
  withheld_documents: string[];
  withheld_summary: string;
  withheld_records: number;
  escalation_target: Role | null;
  access_request_id: string | null;
  /** Documents uploaded into this conversation; readable by the uploader and nobody else. */
  attached_documents?: string[];
  grant_id: string | null;
  released_records: number;
  release_blocked: boolean;
  /** Why a key sent with the question was refused; null when none was sent or it was accepted. */
  access_key_error?: string | null;
}

export interface Confidence { score: number; level: string; basis: string; uncertainties?: string[] }

export interface FinalResponse {
  response_id: string;
  session_id: string;
  created_at: string;
  task_type: string;
  secondary_task_types?: string[];
  status: AnswerStatus;
  answer_markdown: string;
  blocks: Block[];
  evidence: EvidenceItem[];
  confidence: Confidence;
  safety_flags: SafetyFlagItem[];
  requires_human_review: boolean;
  review_reason?: string | null;
  plan?: { goal: string; steps: { step_id: string; agent: string; goal: string; status: string; depends_on: string[] }[] } | null;
  audit_trail_id: string;
  entities: string[];
  timing_ms: number;
  llm_calls: number;
  backend: string;
  warnings: string[];
  security: SecurityEnvelope;
}

// ---------------------------------------------------------------- progress stream

export interface ProgressEvent {
  event:
    | "phase_started" | "phase_finished" | "agent_started" | "agent_finished"
    | "plan_created" | "replan" | "llm_call" | "warning" | "final" | "error";
  phase?: string | null;
  agent?: string | null;
  step_id?: string | null;
  message: string;
  data?: Record<string, unknown>;
  thinking?: string | null;
  model?: string | null;
  decision?: string | null;
  ts: number;
}

// ---------------------------------------------------------------- auth

export interface LoginResult {
  token: string;
  username: string;
  role: Role;
  level: number;
  readable_tags: Tag[];
  expires: number;
  must_change_password: boolean;
  mfa_enrolled: boolean;
  mfa_satisfied: boolean;
  mfa_enrolment_pending: boolean;
  readable_documents: string[];
  withheld_documents: string[];
}

/** HTTP 428: the password was right and an authenticator code is still owed. */
export interface MfaChallenge {
  mfa_required: true;
  username: string;
  role: Role;
  digits: number;
  period: number;
  seconds_remaining: number;
  detail: string;
  demo_code?: string;
  demo_code_valid_in_seconds?: number;
  demo_code_expires_in_seconds?: number;
  demo_notice?: string;
}

export interface MfaStatus {
  username: string; role: Role; required_for_role: boolean;
  enrolled: boolean; enrolment_pending: boolean;
  required_roles: Role[]; demo_codes: boolean;
}

export interface MfaEnrolment { username: string; secret: string; uri: string; digits: number; period: number }

export interface WhoAmI {
  username: string; role: Role; authenticated: boolean; level: number;
  readable_tags: Tag[]; access_control: boolean;
  readable_documents: string[]; withheld_documents: string[];
  documents: DocumentRow[];
  mfa_enrolled?: boolean; mfa_enrolment_pending?: boolean; mfa_required_for_role?: boolean;
}

// ---------------------------------------------------------------- documents & security

export interface DocumentRow {
  document_id: string; title: string; tag: Tag; min_role: Role;
  reason?: string; pages?: number | null; assigned_by?: string;
  /** Every role that may read this document. With `compartmented`, this is the whole list. */
  roles?: Role[];
  /** True when the document carries its own reader allowlist instead of following the tag ladder. */
  compartmented?: boolean;
}

// ---------------------------------------------------------------- the knowledge layer

/** One document's branch of the knowledge layer, as this role is allowed to see it. */
export interface KnowledgeBranch {
  document_id: string;
  title: string;
  tag: Tag;
  reason?: string;
  roles: Role[];
  compartmented: boolean;
  readable: boolean;
  assigned_by?: string;
  /** Present only on a branch this role may read — a locked branch reports no size at all. */
  pages?: number | null;
  counts?: { chunks?: number; entities?: number; claims?: number; relations?: number; procedures?: number };
  chapters?: { number?: number | string | null; title: string }[];
  /** On a locked branch: the role to ask for a key. */
  ask?: Role | null;
}

export interface KnowledgeTree {
  role: Role;
  principal: string;
  access_control: boolean;
  readable: number;
  locked: number;
  branches: KnowledgeBranch[];
}

export interface DocumentRolesResult {
  document_id: string; tag: Tag; roles: Role[]; compartmented: boolean; min_role: Role;
}

export interface RoleSchemaRow { role: Role; level: number; title: string; reads: Tag[]; description?: string }

export interface SecurityOverview {
  enabled: boolean;
  schema: { roles?: RoleSchemaRow[]; tags?: { tag: Tag; level: number; meaning?: string }[] } & Record<string, unknown>;
  documents: DocumentRow[];
  you: {
    principal: string; role: Role; authenticated: boolean; level: number;
    readable_tags: Tag[]; readable_documents: string[]; withheld_documents: string[];
  };
  escalation: {
    enabled: boolean; your_approver: Role | null;
    key_lifetime_minutes: number; uses_per_key: number;
  };
}

// ---------------------------------------------------------------- escalation

export interface ScopeManifest {
  document_ids: string[];
  record_ids: string[];
  tags: Tag[];
  fingerprint: string;
  truncated: boolean;
}

export interface PreviewRow {
  id: string; kind: string; document?: string; page?: number | null;
  text?: string; note?: string;
}

export interface AccessRequest {
  request_id: string;
  requester: string;
  requester_role: Role;
  question: string;
  session_id: string;
  scope: ScopeManifest;
  approver_role: Role;
  status: "pending" | "approved" | "denied" | "expired";
  created: number;
  expires: number;
  decided_by?: string | null;
  decided_at?: number | null;
  note?: string;
  grant_id?: string | null;
  preview?: PreviewRow[];
}

export interface ApprovalResult {
  request: AccessRequest;
  key?: string;
  grant?: { grant_id: string; expires: number; uses: number; max_uses: number; record_count?: number };
  detail?: string;
}

// ---------------------------------------------------------------- workspace

export interface WorkspaceStats {
  principal: string;
  role: Role;
  documents: { readable: number; withheld: number; total: number; by_tag: Record<string, number>; pages: number };
  knowledge: { entities: number; claims: number; relations: number; procedures: number; chunks: number };
  activity: { runs_total: number; runs_active: number; runs_mine: number };
  escalation: { my_open_requests: number; my_requests: number; awaiting_my_approval: number };
  answers: { effort: Effort; llm: string | null; llm_available: boolean; composed: boolean };
}

export interface Health {
  status: string; backend: string; llm: string; llm_available: boolean;
  profile: string; effort: Effort; documents: string[];
  active_runs: number; access_control: boolean; answer_style: string;
}

export interface AgentDescription { key: string; class: string; phase: string; description: string }

export interface SessionTurn {
  request: string; task_type?: string; status?: string;
  answer_preview?: string; response_id?: string; entities?: unknown[];
}

export interface UploadResult {
  document_id: string; pages?: number; chunks?: number; note?: string; filename?: string;
  detail?: string;
  /** "indexing" for a PDF (poll `run_id`), "described" for an image, "unsupported" otherwise. */
  status?: "indexing" | "described" | "unsupported";
  kind?: "pdf" | "image";
  run_id?: string;
  description?: string;
}

/** Just enough of a run to follow an ingest to its end. */
export interface IngestRun {
  run_id: string;
  phase?: string;
  finished?: number | null;
  final_status?: string | null;
  error?: string | null;
  /** Real counts from the parser — pages read out of pages to read — never an invented estimate. */
  progress?: { done: number; total: number; unit: string; percent: number } | null;
  recent_events?: { t?: number; event?: string; agent?: string; message?: string }[];
}

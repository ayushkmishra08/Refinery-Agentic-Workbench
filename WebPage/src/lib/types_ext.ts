/**
 * Contract for the subsystems added in September 2026: models & routing, sovereignty, the vault,
 * local tools & the sandbox, multimodal intake, deliverables and human review.
 *
 * Mirrors `workbench/app/api_ext.py`. Re-exported from `types.ts` so callers import one module.
 */
import type { Role } from "@/lib/types";

// ---------------------------------------------------------------- audit block extras (models & routing)

export interface RoutingRow { kind: string; purpose?: string; model?: string | null; reason?: string }

/** Fields the audit block carries now; read them off the last block with a cast. */
export interface AuditBlockExtras {
  models_used?: string[];
  routing?: RoutingRow[];
  chained_audit_hash?: string | null;
}

// ---------------------------------------------------------------- models & routing

export interface ModelRow {
  name: string; family: string; size_gb: number; context: number; modalities: string[];
  installed: boolean; fits_vram: boolean; min_vram_mb: number; resident: boolean;
  capabilities: Record<string, number>; notes: string; source: string; thinking: boolean;
}

export interface InstalledModel { name: string; size_gb: number; family: string; parameter_size: string; quantization: string }

export interface ModelsOverview {
  routing_enabled: boolean; budget: string; vram_mb: number; profile: string;
  default_model: string; vision_model: string | null; resident: string | null;
  installed: InstalledModel[]; models: ModelRow[]; best_per_kind: Record<string, string | null>;
  registry_files: { builtin: string; local: string };
}

export interface ChainState { ok: boolean; entries?: number; head?: string; first_bad_seq?: number | null; detail?: string; path?: string }

export interface RoutingCandidate { name: string; score: number; capability?: number; installed: boolean; fits_vram: boolean; resident?: boolean; note?: string }

export interface RoutingEntry {
  seq?: number; ts: number; kind: string; purpose?: string; chosen: string | null; reason: string;
  needs_vision?: boolean; session?: string | null; run_id?: string | null; candidates?: RoutingCandidate[];
}

export interface RoutingLog { entries: RoutingEntry[]; total: number; chain: ChainState }

export interface RoutingPlan {
  text: string;
  subtasks: { kind: string; description: string; needs_vision: boolean; deterministic_tool?: string | null }[];
  decisions: { kind: string; chosen: string | null; reason: string; candidates?: RoutingCandidate[] }[];
  hybrid: boolean;
}

export interface RegisterModelBody {
  name: string; family?: string; size_gb?: number; context?: number; modalities?: string[];
  min_vram_mb?: number; thinking?: boolean; capabilities: Record<string, number>; notes?: string;
}

export interface PackageVerification {
  ok: boolean; name?: string; version?: string; key_id?: string; files_checked?: number;
  mismatches?: string[]; signature_ok?: boolean; signer_trusted?: boolean; detail?: string; package?: string;
}

export interface PackageLog { trusted_signers: string[]; trust_dir: string; entries: Record<string, unknown>[]; chain?: ChainState }

// ---------------------------------------------------------------- sovereignty

export interface NetInterface {
  name: string; isup: boolean; speed_mbps: number; mtu?: number; addresses: string[];
  bytes_sent: number; bytes_recv: number; loopback: boolean;
}

export interface ConnectionEntry {
  seq?: number; ts: number; event: string; pid?: number | null; process?: string; laddr?: string; raddr?: string;
  status?: string; class?: string; workbench_process?: boolean; name?: string;
}

export interface NetworkMonitorStatus {
  running: boolean; started?: number | null; uptime_seconds?: number; scope?: string; interval_seconds?: number;
  samples?: number; errors?: number; connections_seen?: number; external_connections?: number;
  workbench_external_connections?: number; recent_external?: ConnectionEntry[]; recent_workbench_external?: ConnectionEntry[];
  interface_events?: { event: string; name: string; ts: number; speed_mbps?: number }[];
  uplinks_up?: number; uplinks_total?: number; physically_disconnected?: boolean; interfaces?: NetInterface[];
  log_path?: string; log_entries?: number; chain?: ChainState; verdict: string;
}

export interface EgressGuardStatus {
  installed: boolean; allowed_hosts?: string[]; allow_private?: boolean; blocked_count?: number;
  recent_blocked?: { ts: number; host: string; port?: number | null; reason: string }[];
  offline_env?: Record<string, string | null>;
}

export interface ChainRow { log: string; path: string; exists: boolean; ok: boolean; entries: number; detail: string; first_bad_seq?: number | null; head?: string }

export interface TlsState {
  configured: boolean; active?: boolean; mtls?: boolean; dir?: string; hint?: string; error?: string;
  ca_fingerprint?: string; server_fingerprint?: string; client_fingerprint?: string; expires?: string | number; hostnames?: string[];
}

export interface SovereigntyReport {
  airgap_enforced: boolean; egress_guard: EgressGuardStatus; network_monitor: NetworkMonitorStatus;
  chains: ChainRow[]; chains_intact: boolean; verdict: string; generated: number; deep?: boolean; tls?: TlsState;
}

// ---------------------------------------------------------------- vault

export interface VaultBranchMemory {
  branch: string; sealed: boolean; in_memory: boolean; roles_with_key: string[]; opened_by: string[]; opened_at?: number | null;
}

export interface VaultStatus {
  vault_dir?: string;
  sealed_branches: (Record<string, unknown> & { branch: string; roles?: string[]; bytes_plain?: number; bytes_cipher?: number; sealed_at?: number })[];
  plaintext_indexes_on_disk?: string[];
  kms: {
    master_key_source?: string; role_keys?: Record<string, { version: number; versions_kept?: number }>;
    last_rotation?: number | Record<string, number> | null; branches?: Record<string, { roles: string[]; rwk_versions?: Record<string, number> }>;
    branch_count?: number; wrapped_key_count?: number; key_events?: number; key_events_chain?: ChainState;
  };
  enabled: boolean;
  branches_in_memory: { enabled: boolean; branches: VaultBranchMemory[]; in_memory: string[]; recent_events?: Record<string, unknown>[] };
  keyrings: { keyrings?: number; by_role?: Record<string, number>; sessions?: { principal: string; role: string; branches: string[]; opened_at?: number; last_used?: number }[] };
  you: { principal: string; role: Role; can_unwrap: string[] };
  key_events: (Record<string, unknown> & { ts?: number; event?: string })[];
}

export interface SealResult { sealed: { branch: string; roles: string[]; bytes_plain: number; bytes_cipher: number }[]; shredded: boolean }

// ---------------------------------------------------------------- tools & sandbox

export interface ToolDescription {
  name: string; description: string;
  parameters: { type?: string; properties?: Record<string, { type?: string; description?: string }>; required?: string[] };
}

export interface SandboxInfo {
  enabled: boolean; backend?: string; python?: string;
  limits?: { timeout_seconds: number; memory_mb: number; cpu_seconds: number; max_output_bytes: number; max_disk_write_mb: number; max_processes: number };
  manifest_hash?: string; vendor_path?: string | null; manifest_error?: string | null; root?: string; workspace_root?: string;
}

export interface ToolEvidence { document_id?: string; page?: number | null; chunk_id?: string | null; claim_id?: string | null; text?: string; confidence?: number; source?: string }

export interface ToolResult {
  tool: string; ok: boolean; output: string; data: Record<string, unknown>; evidence: ToolEvidence[];
  files: string[]; steps: string[]; duration_ms: number; error: string | null; principal?: string; workspace?: string;
}

export interface ToolIteration { n: number; tool: string; args: Record<string, unknown>; ok: boolean; output_preview?: string; duration_ms?: number; error?: string | null }

export interface ToolRunReport {
  goal: string; mode: string; iterations: ToolIteration[]; final_output: string; files: string[];
  evidence: ToolEvidence[]; steps: string[]; ok: boolean; reason: string; llm_calls: number; duration_ms: number;
  log_hashes: string[]; principal?: string; workspace?: string;
}

export interface WorkspaceFile { path: string; bytes: number; modified: number }

export interface ToolCallEntry { seq?: number; ts: number; kind?: string; tool: string; session?: string; ok: boolean; duration_ms?: number; files?: string[]; evidence?: number; error?: string | null }

export interface SandboxResult {
  run_id: string; backend: string; ok: boolean; exit_code: number; stdout: string; stderr: string; timed_out: boolean;
  limit_hit: string | null; duration_ms: number; peak_rss_mb: number; cpu_seconds: number;
  files_written: { path: string; sha256: string; bytes: number }[]; workdir_destroyed: boolean; egress_attempts: string[];
  static_analysis: { ok?: boolean; syntax_error?: string | null; banned_imports?: string[]; banned_calls?: string[]; pyflakes?: string[] } | null;
  verification: { tests_total: number; tests_passed: number; failures: string[]; passed: boolean } | null;
  verified: boolean; log_hash: string; python_version?: string; manifest_hash?: string; job_object?: boolean; error?: string | null;
}

export interface SandboxManifest { hash?: string; python?: string; stdlib_allowed?: string[]; banned_imports?: string[]; vendored?: { path: string; sha256: string; bytes: number }[]; verified?: boolean; error?: string }

// ---------------------------------------------------------------- intake

export interface OcrLine { text: string; confidence: number; bbox?: number[]; page?: number | null; note?: string }

export interface IntakeResult {
  source: string; kind: "pdf_text" | "pdf_scanned" | "image" | string; pages: number;
  ocr: { lines: OcrLine[]; text: string; mean_confidence: number; low_confidence_lines: number; engine: string; duration_ms: number } | null;
  vision: { purpose: string; model: string; text: string; tags_found: string[]; numbers_found: string[]; illegible_spans?: string[]; duration_ms: number; ok: boolean; error?: string | null }[];
  figures_ocr?: unknown[];
  flagged: { page?: number | null; text: string; confidence: number; reason: string; source?: string }[];
  text: string; summary: Record<string, unknown>; duration_ms: number; threshold?: number;
  draft?: Draft | null;
}

/** What `/upload` adds for an image now that intake runs on it. */
export interface UploadIntakeSummary {
  kind: string; flagged: { page?: number | null; text: string; confidence: number; reason: string }[];
  ocr_lines: number; ocr_mean_confidence: number | null; vision_calls: number; chunks: number; duration_ms: number;
}

// ---------------------------------------------------------------- deliverables & review

export type DraftStatus = "pending_signoff" | "signed_off" | "rejected";

export interface DraftFigure {
  figure_id: string; label: string; value: string; unit?: string | null;
  source: { document_id?: string; page?: number | null; chunk_id?: string | null; claim_id?: string | null; text?: string; source_kind?: string };
  confidence: number; flagged: boolean; flag_reason?: string | null;
  resolution?: { by: string; ts: number; action: string; corrected_value?: string | null; note?: string } | null;
}

export interface Draft {
  draft_id: string; kind: string; title: string; path?: string | null; response_id?: string | null; session_id: string;
  owner: string; owner_role: string; classification?: string | null; created: number; status: DraftStatus;
  figures: DraftFigure[]; signed_off_by?: string | null; signed_off_at?: number | null; signed_off_note?: string;
  rejected_by?: string | null; rejected_at?: number | null; rejected_note?: string; sha256?: string | null;
  history: { ts: number; event: string; by: string; detail?: string }[];
  open_flags: number; flagged: number; pending: boolean;
}

export interface DraftsResponse { drafts: Draft[]; summary: Record<string, number>; reviewer: boolean; chain?: ChainState }

export interface ExportResult {
  path: string; fmt: string; bytes: number; sha256: string; figures_count: number; evidence_count: number;
  status_line: string; classification?: string | null; title?: string; draft: Draft | null; download: string | null;
}

export interface RecordedResponse {
  response_id: string; session_id: string; owner: string; question: string; task_type: string; status: string;
  saved: number; evidence: number; requires_human_review: boolean;
}

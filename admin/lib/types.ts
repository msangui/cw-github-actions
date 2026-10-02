/** Shared types for the run store and the pipeline → panel events contract. */

export type RunStatus =
  | "QUEUED"
  | "IN_PROGRESS"
  | "INGESTING"
  | "CURATING"
  | "WRITING"
  | "EDITING"
  | "GENERATING_AUDIO"
  | "STITCHING"
  | "PUBLISHING"
  | "PUBLISHED"
  | "SAFE_MODE"
  | "DRY_RUN"
  | "DRY_RUN_COMPLETE"
  | "AUDIO_READY"
  | "PAUSED"
  | "FAILED"
  | "ALREADY_PUBLISHED"
  | "CANCELLED"
  | "UNKNOWN";

export const TERMINAL_STATUSES: RunStatus[] = ["PUBLISHED", "SAFE_MODE", "DRY_RUN", "AUDIO_READY", "PAUSED", "FAILED", "ALREADY_PUBLISHED", "CANCELLED"];

/** One event as POSTed by pipeline/log.py. `type: "log"` events carry the log payload fields. */
export interface RunEvent {
  seq: number;
  ts: string;
  type: "log" | "status" | "script" | "cost" | "result" | string;
  level?: "debug" | "info" | "warning" | "error";
  event?: string;
  stage?: string;
  component?: string;
  status?: string;
  script?: string;
  title?: string;
  word_count?: number;
  cost?: CostSummary;
  [key: string]: unknown;
}

export interface CostSummary {
  llm_usd: number;
  llm_by_agent?: Record<string, number>;
  tts_usd: number;
  tts_chars?: number;
  serper_usd: number;
  serper_queries?: number;
  total_usd: number;
}

export interface RunRecord {
  run_id: string;
  repo?: string;
  run_url?: string;
  episode_date?: string;
  dry_run?: boolean;
  skip_aisle?: boolean;
  fresh?: boolean;
  started_at?: string;
  updated_at?: string;
  finished_at?: string;
  status: RunStatus | string;
  status_history: { status: string; at: string }[];
  title?: string;
  word_count?: number;
  cost?: CostSummary;
  result?: Record<string, unknown>;
  /** Number of events stored; the UI polls with ?since=<seq>. */
  event_count: number;
  /** Source of the latest status: pipeline events, or the GitHub Actions API fallback. */
  source: "events" | "github" | "panel";
  github?: GitHubRunSummary;
  triggered_by?: string;
  triggered_inputs?: Record<string, string | boolean>;
}

export interface GitHubRunSummary {
  id: number;
  status: "queued" | "in_progress" | "completed" | string;
  conclusion: string | null;
  html_url: string;
  created_at: string;
  updated_at: string;
  run_number: number;
  event: string;
  display_title?: string;
}

/** Request body for POST /api/runs/{id}/events (see pipeline/log.py). */
export interface EventsPayload {
  run?: Partial<RunRecord> & { run_id?: string };
  events: RunEvent[];
}

export interface ConfigSnapshot {
  /** Commit SHA of the branch head the files were read from. Commits use it as the parent. */
  base_sha: string;
  branch: string;
  repo: string;
  files: Record<string, string>;
}

export interface ConfigPatchProposal {
  id: string;
  title: string;
  rationale: string;
  file: string;
  ops: import("./yaml-patch").PatchOp[];
  before: string;
  after: string;
  diff: string;
  error?: string;
}

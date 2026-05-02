/* TypeScript types for the WorldFork backend API.
 * Trimmed to the fields the UI actually consumes. */

export type RunSummary = {
  id: string;
  name: string;
  status: string;
  created_at: string;
  multiverse_count: number;
};

export type AgentEnvelope<T> = {
  ok: boolean;
  data: T;
  meta: { total?: number; limit?: number; offset?: number; verbosity?: string };
};

export type BigBang = {
  id: string;
  name: string;
  description: string | null;
  scenario_input: Record<string, unknown>;
  status: string;
  current_config_version: number;
  source_snapshot_id: string | null;
  created_at: string;
  updated_at: string;
};

export type MultiverseStatus = "active" | "running" | "paused" | "completed" | "terminated" | "failed" | "frozen" | string;

export type Multiverse = {
  id: string;
  big_bang_id: string;
  parent_multiverse_id: string | null;
  fork_tick_index: number | null;
  ui_label: string;
  version: number;
  depth: number;
  status: MultiverseStatus;
  branch_reason: string | null;
  branch_probability: number;
  path_probability: number;
  state: Record<string, unknown> & {
    idle_streak?: number;
    graph_summary?: unknown;
    [k: string]: unknown;
  };
  report_status: string;
  ended_at: string | null;
  continued_from_report_version_id: string | null;
  created_at: string;
  updated_at: string;
};

export type Report = {
  id: string;
  big_bang_id: string;
  multiverse_id: string | null;
  report_type: "multiverse" | "final_big_bang" | string;
  status: string;
  current_version: number;
  created_at: string;
  updated_at: string;
};

export type ReportVersion = {
  id: string;
  report_id: string;
  version: number;
  title: string;
  summary: string | null;
  source_multiverse_ids: string[] | null;
  content: Record<string, unknown> | null;
  generation_metadata: Record<string, unknown> | null;
  model: string | null;
  markdown_artifact_id: string | null;
  pdf_artifact_id: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentLog = {
  id: string;
  source: string;
  status: string;
  message: string;
  created_at: string;
};

export type ReadyzResult = {
  ok: boolean;
  checks: Record<string, boolean>;
};

export type RenderResult = {
  report_version_id: string;
  format: "pdf" | "md";
  artifact_id: string;
  content_type: string;
  path: string;
};

export type CreateBigBangPayload = {
  name: string;
  description?: string;
  scenario_text?: string;
  scenario_input?: Record<string, unknown>;
  simulation_config?: { max_ticks?: number; tick_duration_minutes?: number };
  branch_policy?: {
    max_branch_depth?: number;
    max_active_multiverses?: number;
    max_branches_per_tick?: number;
    branch_score_threshold?: number;
  };
  use_initializer_agent?: boolean;
  initializer_prompt?: string;
};

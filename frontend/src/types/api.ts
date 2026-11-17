/* Types mirroring the FastAPI response schemas (backend/app/schemas). Keep in sync. */

export type TaskStatus =
  | "queued"
  | "planning"
  | "awaiting_approval"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "rejected"
  | "interrupted";

export type ExecutionMode = "live" | "demo";
export type AgentKey = "planner" | "research" | "analysis" | "verification" | "report";

export const ACTIVE_STATUSES: TaskStatus[] = ["queued", "planning", "awaiting_approval", "running"];

export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string; details?: Record<string, unknown> };
}

export interface User {
  id: number | null;
  username: string;
}

export interface ProviderStatus {
  name: string;
  configured: boolean;
  default_model: string | null;
}

export interface LimitField {
  name: string;
  default: number | null;
  min: number | null;
  max: number | null;
  kind: "int" | "float";
}

export interface SystemStatus {
  app_env: string;
  auth_enabled: boolean;
  live_mode_available: boolean;
  default_llm_provider: string;
  llm_providers: ProviderStatus[];
  search_provider: ProviderStatus;
  missing_for_live: string[];
  version: string;
  limits: LimitField[];
  approval_cost_threshold_usd: number | null;
  approval_timeout_hours: number | null;
}

export interface TaskSummary {
  id: string;
  title: string;
  prompt: string;
  mode: ExecutionMode;
  status: TaskStatus;
  execution_mode: "parallel" | "sequential";
  approval_mode: "none" | "plan" | "tools" | "all";
  current_stage: string | null;
  progress_done: number;
  progress_total: number;
  iterations: number;
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  tool_calls: number;
  cost_usd: number | null;
  estimated_cost_usd: number | null;
  budget_exhausted: boolean;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  report_id: number | null;
}

export interface TaskList {
  items: TaskSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApprovalRequest {
  id: number;
  task_id: string;
  kind: "plan" | "tools" | "cost";
  payload: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "expired";
  comment: string | null;
  created_at: string;
  decided_at: string | null;
  task_title: string | null;
}

export interface AgentConfig {
  key: AgentKey;
  name: string;
  description: string;
  role: string;
  allowed_tools: string[];
  enabled: boolean;
  provider: string | null;
  model: string | null;
  effort: string | null;
  max_output_tokens: number;
  instructions: string;
  max_retries: number;
  timeout_s: number;
  updated_at: string;
  effective_provider: string;
  effective_model: string | null;
  required: boolean;
}

export type ApprovalMode = "none" | "plan" | "tools" | "all";

export interface TaskCreate {
  prompt: string;
  mode: ExecutionMode;
  execution_mode: "parallel" | "sequential";
  approval_mode: ApprovalMode;
  limits: Partial<Record<string, number>>;
  disabled_agents: ("analysis" | "verification")[];
}

export interface OverviewAnalytics {
  totals: {
    tasks: number;
    completed: number;
    failed: number;
    active: number;
    awaiting_approval: number;
    success_rate: number | null;
    avg_duration_s: number | null;
    agents: number;
    agents_enabled: number;
    total_tokens: number;
    total_cost_usd: number | null;
    demo_tasks: number;
  };
  series: { date: string; completed: number; failed: number; other: number }[];
  status_breakdown: Partial<Record<TaskStatus, number>>;
  per_agent: Record<string, { runs: number; failed: number; avg_ms: number | null; tokens: number }>;
  recent: { id: string; title: string; status: TaskStatus; mode: ExecutionMode; created_at: string; cost_usd: number | null }[];
}

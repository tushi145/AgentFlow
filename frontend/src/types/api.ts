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

export interface Subtask {
  id: number;
  plan_key: string;
  title: string;
  description: string;
  agent_key: string;
  depends_on: string[];
  queries: string[];
  status: "pending" | "running" | "done" | "failed" | string;
  iteration: number;
  error: string | null;
}

export interface AgentExecution {
  id: number;
  agent_key: AgentKey | string;
  subtask_id: number | null;
  iteration: number;
  /** running | completed | failed | cancelled | aborted */
  status: string;
  mode: string;
  provider: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  tool_calls: number;
  cost_usd: number | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
  output: Record<string, unknown> | null;
}

export interface TaskDetail extends TaskSummary {
  limits: Record<string, number | null>;
  subtasks: Subtask[];
  agent_executions: AgentExecution[];
  pending_approval_id: number | null;
}

export interface ResearchSource {
  id: number;
  ref: string;
  url: string;
  title: string;
  domain: string;
  snippet: string;
  search_query: string;
  provider: string;
  relevance_score: number | null;
  injection_suspected: boolean;
  injection_rules: string[];
  mode: string;
  retrieved_at: string;
}

/** Verdict vocabulary of the Verification Agent (backend/app/schemas/agent_io.py). */
export type Verdict = "supported" | "partially_supported" | "unsupported" | "conflicting" | "unverifiable";

export interface VerificationResult {
  id: number;
  iteration: number;
  claim: string;
  verdict: Verdict | string;
  confidence: number;
  reasoning: string;
  supporting_refs: string[];
  conflicting_refs: string[];
}

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  tool_calls: number;
  cost_usd: number | null;
  estimated_cost_usd: number | null;
  cost_note: string | null;
}

export interface AgentUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  llm_calls: number;
  tool_calls: number;
  cost_usd: number | null;
}

export interface TaskUsage {
  per_agent: Record<string, AgentUsage>;
  totals: UsageTotals;
  limits: Record<string, number | null>;
}

export interface ReportSummary {
  id: number;
  task_id: string;
  title: string;
  summary: string;
  source_count: number;
  avg_confidence: number | null;
  citation_violations: number;
  mode: string;
  created_at: string;
  has_pdf: boolean;
}

export interface ReportDetail extends ReportSummary {
  markdown: string;
  prompt: string;
  sources: ResearchSource[];
  /** Latest verification iteration only. */
  verifications: VerificationResult[];
}

export type ReportList = Paged<ReportSummary>;

/* ------------------------------------------------------------------ agents / models */

export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

export interface ModelInfo {
  id: string;
  display_name: string;
  provider: string;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
}

export interface ProviderModels {
  provider: string;
  configured: boolean;
  default_model: string | null;
  /** valid | invalid | unverified | provider_not_configured | null */
  default_model_status: string | null;
  error: string | null;
  models: ModelInfo[];
}

/** PUT /agents/{key}. Omitted fields are left unchanged by the backend. */
export interface AgentConfigUpdate {
  enabled?: boolean;
  provider?: "anthropic" | "gemini";
  model?: string;
  clear_model_override?: boolean;
  effort?: Effort;
  clear_effort?: boolean;
  max_output_tokens?: number;
  instructions?: string;
  max_retries?: number;
  timeout_s?: number;
}

/* ------------------------------------------------------------------ evaluation */

export interface EvaluationSummary {
  live_runs_evaluated: number;
  task_completion_rate: number | null;
  citation_integrity_rate: number | null;
  source_coverage_rate: number | null;
  claims_with_source_rate: number | null;
  supported_claim_rate: number | null;
  mean_confidence: number | null;
  agent_failures: Record<string, { runs: number; failed: number; failure_rate: number | null }>;
  verification_benchmark: { accuracy: number | null; binary_accuracy: number | null; model: string | null; run_id: number; created_at: string } | null;
  duration_s: { runs: number; mean: number | null; median: number | null; max: number | null };
  tokens: { runs: number; total: number | null; mean_per_run: number | null };
}

/** Per-run metrics recorded by the `evaluate` graph node (backend/app/evaluation/metrics.py). */
export interface RunMetrics {
  mode: string;
  completed: boolean;
  budget_exhausted: boolean;
  iterations: number;
  citation_integrity?: { valid: number; removed: number; rate: number | null };
  source_coverage?: { subtasks: number; subtasks_with_sources: number; rate: number | null; sources: number;
                      unique_domains: number; claims_with_source_rate: number | null; flagged_injection_sources: number };
  verification?: { claims: number; verdicts: Record<string, number>; supported_rate: number | null;
                   mean_confidence: number | null; retrieval_method: string | null };
  reliability?: { agent_failures: number; retries: number };
}

export interface EvaluationRun {
  task_id: string;
  title: string;
  mode: ExecutionMode;
  status: TaskStatus;
  created_at: string;
  duration_s: number | null;
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  tool_calls: number;
  cost_usd: number | null;
  metrics: RunMetrics;
}

export interface BenchmarkRun {
  id: number;
  suite: string;
  status: "running" | "completed" | "failed" | string;
  provider: string | null;
  model: string | null;
  metrics: Record<string, unknown>;
  cost_usd: number | null;
  created_at: string;
}

/** One persisted execution event, as streamed over SSE (`event: agentflow`). */
export interface ExecutionEvent {
  id: number;
  task_id: string;
  type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
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

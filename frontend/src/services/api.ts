/* Thin typed client for the AgentFlow API. All requests are same-origin (Vite proxy in dev,
   reverse proxy in production) and authenticated with the httpOnly session cookie. */

import type {
  AgentConfig,
  ApiErrorBody,
  ApprovalRequest,
  OverviewAnalytics,
  ReportSummary,
  ResearchSource,
  SystemStatus,
  TaskCreate,
  TaskDetail,
  TaskList,
  TaskSummary,
  TaskUsage,
  User,
  VerificationResult,
} from "@/types/api";

export const API_BASE = "/api/v1";

/** Same-origin URLs (the session cookie authenticates them). */
export const urls = {
  taskEvents: (id: string) => `${API_BASE}/tasks/${encodeURIComponent(id)}/events`,
  reportMarkdown: (id: number) => `${API_BASE}/reports/${id}/download.md`,
  reportPdf: (id: number) => `${API_BASE}/reports/${id}/download.pdf`,
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | undefined;

  constructor(status: number, code: string, message: string, requestId: string | null,
              details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }

  get isUnauthorized() {
    return this.status === 401;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function request<T>(path: string, init: RequestInit & { query?: Query } = {}): Promise<T> {
  const { query, headers, ...rest } = init;
  let res: Response;
  try {
    res = await fetch(withQuery(`${API_BASE}${path}`, query), {
      credentials: "same-origin",
      headers: { Accept: "application/json", ...(rest.body ? { "Content-Type": "application/json" } : {}), ...headers },
      ...rest,
    });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the AgentFlow API. Is the backend running?", null);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    throw new ApiError(
      res.status,
      err?.code ?? `http_${res.status}`,
      err?.message ?? (res.status >= 500 ? "The server returned an error." : res.statusText || "Request failed."),
      err?.request_id ?? res.headers.get("x-request-id"),
      err?.details,
    );
  }
  return body as T;
}

const json = (data: unknown) => JSON.stringify(data);

export const api = {
  // auth
  me: () => request<User>("/auth/me"),
  login: (username: string, password: string) =>
    request<User>("/auth/login", { method: "POST", body: json({ username, password }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),

  // system
  systemStatus: () => request<SystemStatus>("/system/status"),
  health: () => request<{ status: string; database: string }>("/health"),

  // analytics
  overview: (mode?: string) => request<OverviewAnalytics>("/analytics/overview", { query: { mode } }),

  // tasks
  listTasks: (q: { status?: string; q?: string; mode?: string; page?: number; page_size?: number } = {}) =>
    request<TaskList>("/tasks", { query: q }),
  createTask: (body: TaskCreate) => request<TaskSummary>("/tasks", { method: "POST", body: json(body) }),
  cancelTask: (id: string) => request<TaskSummary>(`/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  retryTask: (id: string) => request<TaskSummary>(`/tasks/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  deleteTask: (id: string) => request<void>(`/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }),

  getTask: (id: string) => request<TaskDetail>(`/tasks/${encodeURIComponent(id)}`),
  taskSources: (id: string) => request<ResearchSource[]>(`/tasks/${encodeURIComponent(id)}/sources`),
  taskVerifications: (id: string) => request<VerificationResult[]>(`/tasks/${encodeURIComponent(id)}/verifications`),
  taskUsage: (id: string) => request<TaskUsage>(`/tasks/${encodeURIComponent(id)}/usage`),

  // reports
  getReport: (id: number) => request<ReportSummary>(`/reports/${id}`),

  // approvals
  listApprovals: (status = "pending", taskId?: string) =>
    request<ApprovalRequest[]>("/approvals", { query: { status, task_id: taskId } }),
  decideApproval: (id: number, approved: boolean, comment?: string) =>
    request<ApprovalRequest>(`/approvals/${id}/decision`, { method: "POST", body: json({ approved, comment: comment || null }) }),

  // agents
  listAgents: () => request<AgentConfig[]>("/agents"),
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/services/api";
import type { TaskCreate } from "@/types/api";

export const taskKeys = {
  all: ["tasks"] as const,
  list: (params: Record<string, unknown>) => ["tasks", "list", params] as const,
  overview: (mode?: string) => ["analytics", "overview", mode ?? "all"] as const,
  detail: (id: string) => ["tasks", "detail", id] as const,
  sources: (id: string) => ["tasks", "detail", id, "sources"] as const,
  verifications: (id: string) => ["tasks", "detail", id, "verifications"] as const,
  usage: (id: string) => ["tasks", "detail", id, "usage"] as const,
  approvals: (id: string) => ["approvals", "task", id] as const,
  report: (id: number) => ["reports", id] as const,
};

/* Execution-page queries. They are refreshed by SSE events (see TaskExecutionPage), so they only
   fall back to polling when `poll` is given (stream not connected). */
export function useTaskDetail(id: string, poll?: number | false) {
  return useQuery({ queryKey: taskKeys.detail(id), queryFn: () => api.getTask(id), refetchInterval: poll });
}

export function useTaskSources(id: string) {
  return useQuery({ queryKey: taskKeys.sources(id), queryFn: () => api.taskSources(id) });
}

export function useTaskVerifications(id: string) {
  return useQuery({ queryKey: taskKeys.verifications(id), queryFn: () => api.taskVerifications(id) });
}

export function useTaskUsage(id: string) {
  return useQuery({ queryKey: taskKeys.usage(id), queryFn: () => api.taskUsage(id) });
}

export function useTaskApprovals(id: string, poll?: number | false) {
  return useQuery({ queryKey: taskKeys.approvals(id), queryFn: () => api.listApprovals("pending", id), refetchInterval: poll });
}

export function useReport(id: number | null | undefined) {
  return useQuery({ queryKey: taskKeys.report(id ?? 0), queryFn: () => api.getReport(id!), enabled: !!id });
}

/** Invalidate everything derived from task state after a mutation. */
function useInvalidateTaskData() {
  const qc = useQueryClient();
  return () => Promise.all([
    qc.invalidateQueries({ queryKey: ["tasks"] }),
    qc.invalidateQueries({ queryKey: ["approvals"] }),
    qc.invalidateQueries({ queryKey: ["analytics"] }),
  ]);
}

export function useOverview(mode?: string) {
  return useQuery({ queryKey: taskKeys.overview(mode), queryFn: () => api.overview(mode), refetchInterval: 15_000 });
}

export function useTaskList(params: { status?: string; q?: string; mode?: string; page?: number; page_size?: number },
                            opts: { poll?: number } = {}) {
  return useQuery({
    queryKey: taskKeys.list(params),
    queryFn: () => api.listTasks(params),
    placeholderData: keepPreviousData,
    refetchInterval: opts.poll,
  });
}

export function useCreateTask() {
  const invalidate = useInvalidateTaskData();
  return useMutation({ mutationFn: (body: TaskCreate) => api.createTask(body), onSuccess: invalidate });
}

export function useCancelTask() {
  const invalidate = useInvalidateTaskData();
  return useMutation({ mutationFn: api.cancelTask, onSettled: invalidate });
}

export function useRetryTask() {
  const invalidate = useInvalidateTaskData();
  return useMutation({ mutationFn: api.retryTask, onSuccess: invalidate });
}

export function useDeleteTask() {
  const invalidate = useInvalidateTaskData();
  return useMutation({ mutationFn: api.deleteTask, onSuccess: invalidate });
}

export function useDecideApproval() {
  const invalidate = useInvalidateTaskData();
  return useMutation({
    mutationFn: ({ id, approved, comment }: { id: number; approved: boolean; comment?: string }) =>
      api.decideApproval(id, approved, comment),
    onSettled: invalidate,
  });
}

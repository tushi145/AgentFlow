import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/services/api";
import type { TaskCreate } from "@/types/api";

export const taskKeys = {
  all: ["tasks"] as const,
  list: (params: Record<string, unknown>) => ["tasks", "list", params] as const,
  overview: (mode?: string) => ["analytics", "overview", mode ?? "all"] as const,
};

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

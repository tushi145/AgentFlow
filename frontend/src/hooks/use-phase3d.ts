/* Queries and mutations for Reports, Agent Configuration, Evaluation and Settings. */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/services/api";
import type { AgentConfig, AgentConfigUpdate } from "@/types/api";

export const keys = {
  reports: (p: Record<string, unknown>) => ["reports", "list", p] as const,
  agents: ["agents"] as const,
  models: ["models"] as const,
  evalSummary: ["evaluation", "summary"] as const,
  evalRuns: (p: Record<string, unknown>) => ["evaluation", "runs", p] as const,
  benchmarks: ["evaluation", "benchmarks"] as const,
  health: ["system", "health"] as const,
};

export function useReportList(params: { q?: string; mode?: string; page?: number; page_size?: number }) {
  return useQuery({ queryKey: keys.reports(params), queryFn: () => api.listReports(params), placeholderData: keepPreviousData });
}

export function useAgents() {
  return useQuery({ queryKey: keys.agents, queryFn: api.listAgents });
}

/** Models per provider, straight from the provider APIs (cached by the backend registry). */
export function useModels() {
  return useQuery({ queryKey: keys.models, queryFn: () => api.listModels(), staleTime: 60_000 });
}

export function useRefreshModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.listModels(true),
    onSuccess: (data) => qc.setQueryData(keys.models, data),
  });
}

export function useUpdateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, body }: { key: string; body: AgentConfigUpdate }) => api.updateAgent(key, body),
    onSuccess: (saved: AgentConfig) => {
      qc.setQueryData<AgentConfig[]>(keys.agents, (prev) => prev?.map((a) => (a.key === saved.key ? saved : a)));
      void qc.invalidateQueries({ queryKey: ["analytics"] }); // enabled-agent counts on Overview
    },
  });
}

export function useEvaluationSummary() {
  return useQuery({ queryKey: keys.evalSummary, queryFn: api.evaluationSummary });
}

export function useEvaluationRuns(params: { mode?: string; page?: number; page_size?: number }) {
  return useQuery({ queryKey: keys.evalRuns(params), queryFn: () => api.evaluationRuns(params), placeholderData: keepPreviousData });
}

/** Polls only while a benchmark run is still in progress. */
export function useBenchmarks() {
  return useQuery({
    queryKey: keys.benchmarks, queryFn: api.listBenchmarks,
    refetchInterval: (q) => (q.state.data?.some((b) => b.status === "running") ? 3000 : false),
  });
}

export function useRunBenchmark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (suite: string) => api.runBenchmark(suite),
    onSettled: () => qc.invalidateQueries({ queryKey: ["evaluation"] }),
  });
}

export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: api.health, refetchInterval: 30_000, retry: false });
}

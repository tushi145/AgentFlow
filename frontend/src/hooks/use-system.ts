import { useQuery } from "@tanstack/react-query";

import { api } from "@/services/api";

export function useSystemStatus() {
  return useQuery({ queryKey: ["system", "status"], queryFn: api.systemStatus, staleTime: 60_000 });
}

/** Real count of approvals awaiting a decision (polled; drives the top-bar indicator). */
export function usePendingApprovals() {
  return useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api.listApprovals("pending"),
    refetchInterval: 15_000,
  });
}

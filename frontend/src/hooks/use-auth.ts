import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/services/api";
import type { User } from "@/types/api";

export const authKeys = { me: ["auth", "me"] as const };

/** Resolves to the signed-in user, or `null` when there is no valid session (HTTP 401).
 *  Treating 401 as data (not an error) avoids repeated re-checks on every mount. */
async function fetchMe(): Promise<User | null> {
  try {
    return await api.me();
  } catch (err) {
    if (err instanceof ApiError && err.isUnauthorized) return null;
    throw err;
  }
}

export function useCurrentUser() {
  return useQuery({ queryKey: authKeys.me, queryFn: fetchMe, staleTime: 5 * 60_000, retry: 1 });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      api.login(username, password),
    onSuccess: (user) => {
      qc.setQueryData(authKeys.me, user);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      // Drop all cached data from the session, and record "signed out" without a refetch.
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      qc.setQueryData(authKeys.me, null);
    },
  });
}

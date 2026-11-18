/* Default execution preferences for the New Task form. Stored in this browser only (localStorage):
   the backend has no per-user preference store, and these are UI defaults, not server settings. */

import type { ApprovalMode } from "@/types/api";

export interface ExecutionPreferences {
  /** "auto" = Live when available, otherwise Demo. */
  mode: "auto" | "live" | "demo";
  execution_mode: "parallel" | "sequential";
  approval_mode: ApprovalMode;
}

export const DEFAULT_PREFERENCES: ExecutionPreferences = { mode: "auto", execution_mode: "parallel", approval_mode: "none" };
const KEY = "agentflow.execution-preferences.v1";

const MODES = ["auto", "live", "demo"] as const;
const EXEC = ["parallel", "sequential"] as const;
const APPROVAL = ["none", "plan", "tools", "all"] as const;

export function loadPreferences(): ExecutionPreferences {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<ExecutionPreferences> | null;
    if (!raw || typeof raw !== "object") return DEFAULT_PREFERENCES;
    return {
      mode: MODES.includes(raw.mode as never) ? raw.mode! : DEFAULT_PREFERENCES.mode,
      execution_mode: EXEC.includes(raw.execution_mode as never) ? raw.execution_mode! : DEFAULT_PREFERENCES.execution_mode,
      approval_mode: APPROVAL.includes(raw.approval_mode as never) ? raw.approval_mode! : DEFAULT_PREFERENCES.approval_mode,
    };
  } catch {
    return DEFAULT_PREFERENCES;
  }
}

/** Returns false when the browser refuses storage (private mode, blocked site data). */
export function savePreferences(p: ExecutionPreferences): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
    return true;
  } catch {
    return false;
  }
}

/** The mode the New Task form should start with. A Live preference falls back to Demo when live
 *  execution is unavailable — the form never pre-selects an option that cannot run. */
export function initialMode(p: ExecutionPreferences, liveAvailable: boolean): "live" | "demo" {
  if (p.mode === "demo") return "demo";
  return liveAvailable ? "live" : "demo";
}

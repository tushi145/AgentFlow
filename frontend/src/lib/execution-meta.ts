/* Display vocabulary for the Live Execution page (labels, tones and icons per backend value). */

import {
  AlertTriangle, Ban, Bot, CheckCircle2, Circle, CircleDot, CircleSlash, FileText, Flag, Globe, Hand, Info, Link2,
  ListChecks, Loader2, MinusCircle, Play, RefreshCw, RotateCcw, Search, ShieldAlert, ShieldCheck, SkipForward, Split,
  Wrench, XCircle, type LucideIcon,
} from "lucide-react";

import type { NodeState } from "@/lib/workflow";

export type Tone = "running" | "success" | "warning" | "danger" | "neutral";

export const NODE_STATE: Record<NodeState, { label: string; tone: Tone; icon: LucideIcon; spin?: boolean }> = {
  pending: { label: "Pending", tone: "neutral", icon: Circle },
  running: { label: "Running", tone: "running", icon: Loader2, spin: true },
  completed: { label: "Completed", tone: "success", icon: CheckCircle2 },
  failed: { label: "Failed", tone: "danger", icon: XCircle },
  skipped: { label: "Skipped", tone: "neutral", icon: MinusCircle },
  stopped: { label: "Stopped", tone: "warning", icon: Ban },
  not_run: { label: "Not run", tone: "neutral", icon: CircleSlash },
};

export const APPROVAL_KIND_LABEL: Record<string, string> = {
  plan: "Plan review", tools: "Search approval", cost: "Cost approval",
};

/** Verdict vocabulary of the Verification Agent (backend/app/schemas/agent_io.py), shown as-is. */
export const VERDICT: Record<string, { label: string; tone: Tone; hint: string }> = {
  supported: { label: "Supported", tone: "success", hint: "Evidence supports the claim." },
  partially_supported: { label: "Partially supported", tone: "warning", hint: "Evidence supports part of the claim." },
  unsupported: { label: "Unsupported", tone: "danger", hint: "Retrieved evidence does not support the claim." },
  conflicting: { label: "Conflicting", tone: "danger", hint: "Sources contradict the claim or each other." },
  unverifiable: { label: "Unverifiable", tone: "neutral", hint: "Not enough evidence to judge the claim." },
};
export const verdictMeta = (v: string) => VERDICT[v] ?? { label: v, tone: "neutral" as Tone, hint: "" };

export type EventCategory = "agents" | "tools" | "sources" | "issues" | "approvals" | "progress";

const EVENT_META: Record<string, { icon: LucideIcon; tone: Tone; cat: EventCategory }> = {
  task_started: { icon: Play, tone: "running", cat: "agents" },
  stage_changed: { icon: Flag, tone: "running", cat: "agents" },
  agent_started: { icon: Bot, tone: "running", cat: "agents" },
  agent_progress: { icon: CircleDot, tone: "neutral", cat: "agents" },
  agent_completed: { icon: CheckCircle2, tone: "success", cat: "agents" },
  agent_failed: { icon: XCircle, tone: "danger", cat: "issues" },
  agent_skipped: { icon: SkipForward, tone: "neutral", cat: "agents" },
  plan_created: { icon: ListChecks, tone: "success", cat: "agents" },
  plan_adjusted: { icon: ListChecks, tone: "warning", cat: "issues" },
  dispatch: { icon: Split, tone: "running", cat: "agents" },
  progress: { icon: CircleDot, tone: "neutral", cat: "progress" },
  tool_call: { icon: Search, tone: "neutral", cat: "tools" },
  tool_result: { icon: Wrench, tone: "neutral", cat: "tools" },
  tool_error: { icon: AlertTriangle, tone: "danger", cat: "issues" },
  source_added: { icon: Globe, tone: "neutral", cat: "sources" },
  injection_flagged: { icon: ShieldAlert, tone: "warning", cat: "issues" },
  citation_violation: { icon: Link2, tone: "warning", cat: "issues" },
  warning: { icon: AlertTriangle, tone: "warning", cat: "issues" },
  retry: { icon: RefreshCw, tone: "warning", cat: "issues" },
  repair: { icon: RefreshCw, tone: "warning", cat: "issues" },
  verification_completed: { icon: ShieldCheck, tone: "success", cat: "agents" },
  verification_guard: { icon: ShieldAlert, tone: "warning", cat: "issues" },
  iteration_started: { icon: RotateCcw, tone: "running", cat: "agents" },
  iteration_limit: { icon: AlertTriangle, tone: "warning", cat: "issues" },
  approval_requested: { icon: Hand, tone: "warning", cat: "approvals" },
  approval_decided: { icon: Hand, tone: "success", cat: "approvals" },
  paused: { icon: Hand, tone: "warning", cat: "approvals" },
  report_ready: { icon: FileText, tone: "success", cat: "agents" },
  evaluation_completed: { icon: CheckCircle2, tone: "neutral", cat: "agents" },
  task_finished: { icon: Flag, tone: "neutral", cat: "agents" },
};
const EVENT_FALLBACK = { icon: Info, tone: "neutral" as Tone, cat: "agents" as EventCategory };
export const eventMeta = (type: string) => EVENT_META[type] ?? EVENT_FALLBACK;

export const AGENT_LABEL: Record<string, string> = {
  planner: "Planner", research: "Research", analysis: "Analysis", verification: "Verification", report: "Report",
};

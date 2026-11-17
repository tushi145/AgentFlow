/* Status vocabulary shared by every page: one colour + icon per state, everywhere. */

import {
  Ban, CheckCircle2, CircleDashed, CircleSlash, Clock, Hand, Loader2, PauseCircle, XCircle,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { ExecutionMode, TaskStatus } from "@/types/api";

type Tone = "running" | "success" | "warning" | "danger" | "neutral";

export const TONE_CLASSES: Record<Tone, string> = {
  running: "text-status-running bg-status-running/10 border-status-running/25",
  success: "text-status-success bg-status-success/10 border-status-success/25",
  warning: "text-status-warning bg-status-warning/10 border-status-warning/25",
  danger: "text-status-danger bg-status-danger/10 border-status-danger/25",
  neutral: "text-status-neutral bg-status-neutral/10 border-status-neutral/25",
};

export const TASK_STATUS: Record<TaskStatus, { label: string; tone: Tone; icon: LucideIcon; spin?: boolean }> = {
  queued: { label: "Queued", tone: "neutral", icon: Clock },
  planning: { label: "Planning", tone: "running", icon: Loader2, spin: true },
  running: { label: "Running", tone: "running", icon: Loader2, spin: true },
  awaiting_approval: { label: "Awaiting approval", tone: "warning", icon: Hand },
  completed: { label: "Completed", tone: "success", icon: CheckCircle2 },
  failed: { label: "Failed", tone: "danger", icon: XCircle },
  cancelled: { label: "Cancelled", tone: "neutral", icon: Ban },
  rejected: { label: "Rejected", tone: "danger", icon: CircleSlash },
  interrupted: { label: "Interrupted", tone: "warning", icon: PauseCircle },
};

export function StatusBadge({ status, className }: { status: TaskStatus; className?: string }) {
  const meta = TASK_STATUS[status] ?? { label: status, tone: "neutral" as Tone, icon: CircleDashed };
  const Icon = meta.icon;
  return (
    <Badge variant="outline" className={cn("gap-1 font-medium", TONE_CLASSES[meta.tone], className)}>
      <Icon className={cn("size-3", meta.spin && "animate-spin")} aria-hidden />
      {meta.label}
    </Badge>
  );
}

/** LIVE vs DEMO is always explicit. Demo is never presented as AI execution. */
export function ModeBadge({ mode, className }: { mode: ExecutionMode; className?: string }) {
  if (mode === "demo") {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="outline"
            className={cn("border-status-demo/40 bg-status-demo/10 font-semibold tracking-wide text-status-demo", className)}
          >
            DEMO
          </Badge>
        </TooltipTrigger>
        <TooltipContent className="max-w-64">
          Demo mode: fixed templates and synthetic demo:// sources. No AI model or web search was used.
        </TooltipContent>
      </Tooltip>
    );
  }
  return (
    <Badge variant="outline" className={cn("border-primary/30 bg-primary/10 font-semibold tracking-wide text-primary", className)}>
      LIVE
    </Badge>
  );
}

export function StatusDot({ tone, pulse = false, className }: { tone: Tone; pulse?: boolean; className?: string }) {
  const bg: Record<Tone, string> = {
    running: "bg-status-running", success: "bg-status-success", warning: "bg-status-warning",
    danger: "bg-status-danger", neutral: "bg-status-neutral",
  };
  return (
    <span className={cn("relative inline-flex size-2 rounded-full", bg[tone], className)}>
      {pulse && <span className={cn("absolute inset-0 animate-ping rounded-full opacity-60", bg[tone])} />}
    </span>
  );
}

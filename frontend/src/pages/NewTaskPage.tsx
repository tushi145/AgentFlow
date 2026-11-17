import { useQuery } from "@tanstack/react-query";
import {
  Bot, ChevronDown, FlaskConical, GitBranch, Hand, Info, ListOrdered, Loader2, Lock, Rocket, Sparkles,
} from "lucide-react";
import { type FormEvent, type ReactNode, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { ErrorState, PageHeader } from "@/components/common";
import { ModeBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useSystemStatus } from "@/hooks/use-system";
import { useCreateTask } from "@/hooks/use-tasks";
import { formatCost } from "@/lib/format";
import { cn } from "@/lib/utils";
import { api, ApiError } from "@/services/api";
import type { ApprovalMode, ExecutionMode, LimitField, TaskCreate } from "@/types/api";

const MIN_PROMPT = 10;
const MAX_PROMPT = 4000;

/* Example objectives (UI copy only — they pre-fill the editor, they are not data). */
const EXAMPLES = [
  "Research the top 5 AI companies hiring fresh graduates in India, compare their job roles, required skills and eligibility criteria, and prepare a detailed report.",
  "Compare the leading open-source vector databases on performance, licensing, managed-cloud options and community activity.",
  "Summarise the current evidence on four-day work weeks: productivity outcomes, employee wellbeing and notable company trials.",
  "Evaluate the main options for deploying a FastAPI application on AWS, including cost, scalability and operational overhead.",
];

const LIMIT_META: Record<string, { label: string; unit?: string; hint: string }> = {
  max_total_tokens: { label: "Token budget", unit: "tokens", hint: "Total input + output tokens for the whole run." },
  max_llm_calls: { label: "Max LLM calls", hint: "Model requests across all agents." },
  max_tool_calls: { label: "Max tool calls", hint: "Web searches, page reads and calculations." },
  timeout_seconds: { label: "Time limit", unit: "seconds", hint: "Wall-clock limit for active execution." },
  max_cost_usd: { label: "Cost cap", unit: "USD", hint: "Stops when priced spend reaches this. Empty = no cap." },
  max_iterations: { label: "Research iterations", hint: "Verification may request additional research rounds." },
  max_subtasks: { label: "Max subtasks", hint: "Upper bound on the planner's decomposition." },
  max_searches_per_subtask: { label: "Searches per subtask", hint: "Search queries each research subtask may run." },
};

const APPROVAL_OPTIONS: { value: ApprovalMode; title: string; description: string }[] = [
  { value: "none", title: "Autonomous", description: "Run end-to-end without pausing." },
  { value: "plan", title: "Approve plan", description: "Pause after planning to review subtasks and estimated cost." },
  { value: "tools", title: "Approve searches", description: "Pause before each batch of web searches." },
  { value: "all", title: "Plan + searches", description: "Review the plan and every search batch." },
];

function OptionCard({ selected, disabled, onSelect, icon, title, description, badge, name, value }: {
  selected: boolean; disabled?: boolean; onSelect: () => void; icon: ReactNode; title: string;
  description: ReactNode; badge?: ReactNode; name: string; value: string;
}) {
  return (
    <label
      className={cn(
        "relative flex cursor-pointer gap-3 rounded-lg border p-3 text-left transition-colors",
        selected ? "border-primary/60 bg-primary/5 ring-1 ring-primary/40" : "hover:bg-accent/40",
        disabled && "cursor-not-allowed opacity-55 hover:bg-transparent",
      )}
    >
      <input type="radio" name={name} value={value} checked={selected} disabled={disabled} onChange={onSelect}
             className="sr-only" />
      <div className="mt-0.5 text-muted-foreground">{icon}</div>
      <div className="min-w-0 space-y-0.5">
        <div className="flex items-center gap-2 text-sm font-medium">{title}{badge}</div>
        <div className="text-xs text-muted-foreground">{description}</div>
      </div>
    </label>
  );
}

function limitError(f: LimitField, raw: string): string | null {
  if (raw.trim() === "") return null;
  const v = Number(raw);
  if (!Number.isFinite(v)) return "Enter a number.";
  if (f.kind === "int" && !Number.isInteger(v)) return "Whole numbers only.";
  if (f.min !== null && v < f.min) return `Minimum ${f.min}.`;
  if (f.max !== null && v > f.max) return `Maximum ${f.max}.`;
  return null;
}

export default function NewTaskPage() {
  const navigate = useNavigate();
  const status = useSystemStatus();
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.listAgents });
  const create = useCreateTask();

  const liveAvailable = !!status.data?.live_mode_available;
  const [prompt, setPrompt] = useState("");
  const [modeChoice, setModeChoice] = useState<ExecutionMode | null>(null);
  const mode: ExecutionMode = modeChoice ?? (liveAvailable ? "live" : "demo");
  const [executionMode, setExecutionMode] = useState<"parallel" | "sequential">("parallel");
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>("none");
  const [disabled, setDisabled] = useState<Set<"analysis" | "verification">>(new Set());
  const [limits, setLimits] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState(false);

  const limitFields = useMemo(() => status.data?.limits ?? [], [status.data?.limits]);
  const limitErrors = useMemo(
    () => Object.fromEntries(limitFields.map((f) => [f.name, limitError(f, limits[f.name] ?? "")])),
    [limitFields, limits],
  );
  const promptLen = prompt.trim().length;
  const promptError = touched && promptLen < MIN_PROMPT ? `Describe the task in at least ${MIN_PROMPT} characters.` : null;
  const hasLimitErrors = Object.values(limitErrors).some(Boolean);
  const changedLimits = limitFields.filter((f) => (limits[f.name] ?? "").trim() !== "" && Number(limits[f.name]) !== f.default);

  const toggleAgent = (key: "analysis" | "verification", enabled: boolean) => {
    setDisabled((prev) => {
      const next = new Set(prev);
      if (enabled) next.delete(key); else next.add(key);
      return next;
    });
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (promptLen < MIN_PROMPT || promptLen > MAX_PROMPT || hasLimitErrors) return;
    if (mode === "live" && !liveAvailable) return;
    const body: TaskCreate = {
      prompt: prompt.trim(), mode, execution_mode: executionMode, approval_mode: approvalMode,
      limits: Object.fromEntries(changedLimits.map((f) => [f.name, Number(limits[f.name])])),
      disabled_agents: [...disabled],
    };
    create.mutate(body, {
      onSuccess: (task) => {
        toast.success(mode === "demo" ? "Demo task started (templates, not AI)" : "Task started", {
          description: task.title,
        });
        navigate(`/executions?highlight=${task.id}`);
      },
    });
  };

  const fieldErrors = create.error instanceof ApiError && create.error.code === "validation_error"
    ? (create.error.details?.errors as { loc: string[]; msg: string }[] | undefined) : undefined;

  return (
    <form onSubmit={submit} noValidate>
      <PageHeader
        title="New task"
        description="Describe the outcome you need. AgentFlow plans, researches, verifies and reports."
      />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px] [&>*]:min-w-0">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Objective</CardTitle>
              <CardDescription>Be specific about scope, comparisons and the deliverable you expect.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Label htmlFor="prompt" className="sr-only">Research objective</Label>
              <Textarea
                id="prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} onBlur={() => setTouched(true)}
                rows={8} maxLength={MAX_PROMPT} aria-invalid={!!promptError} aria-describedby="prompt-help"
                placeholder="e.g. Research the top 5 AI companies hiring fresh graduates in India and compare their roles, skills and eligibility…"
                className="min-h-44 resize-y text-[15px] leading-relaxed"
              />
              <div id="prompt-help" className="flex items-center justify-between text-xs">
                <span className={promptError ? "text-status-danger" : "text-muted-foreground"}>
                  {promptError ?? "Plain English is fine."}
                </span>
                <span className="tabular text-muted-foreground">{promptLen}/{MAX_PROMPT}</span>
              </div>
              <div className="space-y-2 pt-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <Sparkles className="size-3.5" /> Examples
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {EXAMPLES.map((ex) => (
                    <button key={ex} type="button" onClick={() => setPrompt(ex)}
                            className="rounded-md border px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground">
                      <span className="line-clamp-2">{ex}</span>
                    </button>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Execution</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <fieldset className="space-y-2">
                <legend className="mb-2 text-xs font-medium text-muted-foreground">Mode</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  <OptionCard name="mode" value="live" selected={mode === "live"} disabled={!liveAvailable}
                              onSelect={() => setModeChoice("live")} icon={<Rocket className="size-4" />}
                              title="Live AI execution" badge={<ModeBadge mode="live" />}
                              description={liveAvailable
                                ? "Real model calls and web search. Uses API credits."
                                : <>Unavailable — set <code className="font-mono">{status.data?.missing_for_live.join(", ")}</code> in .env.</>} />
                  <OptionCard name="mode" value="demo" selected={mode === "demo"} onSelect={() => setModeChoice("demo")}
                              icon={<FlaskConical className="size-4" />} title="Demo" badge={<ModeBadge mode="demo" />}
                              description="Fixed templates and synthetic demo:// sources. Not AI, not research." />
                </div>
              </fieldset>
              <fieldset className="space-y-2">
                <legend className="mb-2 text-xs font-medium text-muted-foreground">Research scheduling</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  <OptionCard name="exec" value="parallel" selected={executionMode === "parallel"} onSelect={() => setExecutionMode("parallel")}
                              icon={<GitBranch className="size-4" />} title="Parallel"
                              description="Independent subtasks run concurrently; dependencies are respected." />
                  <OptionCard name="exec" value="sequential" selected={executionMode === "sequential"} onSelect={() => setExecutionMode("sequential")}
                              icon={<ListOrdered className="size-4" />} title="Sequential"
                              description="One subtask at a time, in dependency order." />
                </div>
              </fieldset>
              <fieldset className="space-y-2">
                <legend className="mb-2 text-xs font-medium text-muted-foreground">Human approval</legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {APPROVAL_OPTIONS.map((o) => (
                    <OptionCard key={o.value} name="approval" value={o.value} selected={approvalMode === o.value}
                                onSelect={() => setApprovalMode(o.value)} icon={<Hand className="size-4" />}
                                title={o.title} description={o.description} />
                  ))}
                </div>
                {status.data?.approval_cost_threshold_usd != null && (
                  <p className="flex items-start gap-1.5 pt-1 text-xs text-muted-foreground">
                    <Info className="mt-0.5 size-3.5 shrink-0" />
                    Live runs whose estimated cost exceeds {formatCost(status.data.approval_cost_threshold_usd)} always pause for approval.
                    {status.data.approval_timeout_hours ? ` Unanswered approvals expire after ${status.data.approval_timeout_hours}h.` : ""}
                  </p>
                )}
              </fieldset>
            </CardContent>
          </Card>

          <Collapsible>
            <Card>
              <CardHeader>
                <CollapsibleTrigger asChild>
                  <button type="button" className="group flex w-full items-center justify-between text-left">
                    <div>
                      <CardTitle className="text-sm">Limits</CardTitle>
                      <CardDescription className="mt-1">
                        {changedLimits.length ? `${changedLimits.length} override${changedLimits.length > 1 ? "s" : ""} set` : "Using server defaults"}
                      </CardDescription>
                    </div>
                    <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
                  </button>
                </CollapsibleTrigger>
              </CardHeader>
              <CollapsibleContent>
                <CardContent className="grid gap-4 sm:grid-cols-2">
                  {limitFields.map((f) => {
                    const meta = LIMIT_META[f.name] ?? { label: f.name, hint: "" };
                    const err = limitErrors[f.name];
                    return (
                      <div key={f.name} className="space-y-1.5">
                        <Label htmlFor={`limit-${f.name}`} className="flex items-center justify-between">
                          <span>{meta.label}</span>
                          {meta.unit && <span className="text-xs font-normal text-muted-foreground">{meta.unit}</span>}
                        </Label>
                        <Input id={`limit-${f.name}`} inputMode="decimal" value={limits[f.name] ?? ""}
                               placeholder={f.default === null ? "No limit" : String(f.default)} aria-invalid={!!err}
                               onChange={(e) => setLimits((l) => ({ ...l, [f.name]: e.target.value }))} />
                        <p className={cn("text-xs", err ? "text-status-danger" : "text-muted-foreground")}>
                          {err ?? `${meta.hint}${f.min !== null || f.max !== null ? ` Range ${f.min ?? "—"}–${f.max ?? "∞"}.` : ""}`}
                        </p>
                      </div>
                    );
                  })}
                </CardContent>
              </CollapsibleContent>
            </Card>
          </Collapsible>
        </div>

        <div className="space-y-6 lg:sticky lg:top-20 lg:self-start">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Agents</CardTitle>
              <CardDescription>Per-task switches. Models and instructions live in Agent Configuration.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-1">
              {agents.isLoading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
              {agents.error && <ErrorState error={agents.error} onRetry={() => agents.refetch()} />}
              {agents.data?.map((a) => {
                const optional = a.key === "analysis" || a.key === "verification";
                const globallyOff = !a.enabled;
                const on = a.enabled && !(optional && disabled.has(a.key as "analysis" | "verification"));
                return (
                  <div key={a.key} className="flex items-center gap-3 py-1.5">
                    <Bot className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm">{a.name}</div>
                      <div className="truncate font-mono text-[11px] text-muted-foreground">
                        {mode === "demo" ? "demo-template" : a.effective_model ?? "no model set"}
                      </div>
                    </div>
                    {a.required ? (
                      <span title="Required agent"><Lock className="size-3.5 text-muted-foreground" aria-label="Required" /></span>
                    ) : (
                      <Switch checked={on} disabled={globallyOff} aria-label={`Enable ${a.name}`}
                              onCheckedChange={(v) => toggleAgent(a.key as "analysis" | "verification", v)} />
                    )}
                  </div>
                );
              })}
              {agents.data?.some((a) => !a.enabled) && (
                <p className="pt-2 text-xs text-muted-foreground">Agents disabled in Agent Configuration can't be enabled per task.</p>
              )}
              {disabled.has("verification") && (
                <p className="pt-2 text-xs text-status-warning">Without verification, every claim is reported as unverified.</p>
              )}
            </CardContent>
          </Card>

          {create.error && <ErrorState error={create.error} />}
          {fieldErrors && (
            <ul className="list-disc pl-5 text-xs text-status-danger">
              {fieldErrors.map((e, i) => <li key={i}>{e.loc.slice(1).join(".")}: {e.msg}</li>)}
            </ul>
          )}

          <Button type="submit" size="lg" className="w-full"
                  disabled={create.isPending || promptLen > MAX_PROMPT || hasLimitErrors || (mode === "live" && !liveAvailable)}>
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <Rocket className="size-4" />}
            {mode === "demo" ? "Start demo run" : "Start live execution"}
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            {mode === "demo" ? "Demo runs use templates only — no model or web search is called." :
              "Live runs call real APIs and are bounded by the limits above."}
          </p>
        </div>
      </div>
    </form>
  );
}

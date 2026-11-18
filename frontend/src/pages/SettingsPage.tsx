/* Settings: provider/connection status, model defaults, execution defaults, health, appearance.
   Credentials are never handled here: the API reports only whether a key is configured. */

import { Activity, CheckCircle2, CircleSlash, KeyRound, Loader2, Monitor, Moon, RefreshCw, Save, Server, Sun, XCircle } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { ErrorState, KeyValue, LoadingRows, PageHeader } from "@/components/common";
import { TONE_CLASSES } from "@/components/status";
import { type Theme, useTheme } from "@/components/theme-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAgents, useHealth, useModels, useRefreshModels } from "@/hooks/use-phase3d";
import { useSystemStatus } from "@/hooks/use-system";
import { formatCost, formatNumber } from "@/lib/format";
import { DEFAULT_PREFERENCES, type ExecutionPreferences, loadPreferences, savePreferences } from "@/lib/preferences";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/services/api";

const ENV_KEY: Record<string, string> = { anthropic: "ANTHROPIC_API_KEY", gemini: "GEMINI_API_KEY", tavily: "TAVILY_API_KEY" };
const MODEL_STATUS: Record<string, { label: string; tone: keyof typeof TONE_CLASSES }> = {
  valid: { label: "verified", tone: "success" }, invalid: { label: "not found at provider", tone: "danger" },
  unverified: { label: "could not verify", tone: "warning" }, provider_not_configured: { label: "provider not configured", tone: "neutral" },
};

function Section({ title, description, children, action }: { title: string; description?: ReactNode; children: ReactNode; action?: ReactNode }) {
  return (
    <Card className="gap-4 py-5">
      <CardHeader className="flex flex-row items-start justify-between gap-3 px-4 sm:px-6">
        <div className="space-y-1"><CardTitle className="text-base">{title}</CardTitle>{description && <CardDescription>{description}</CardDescription>}</div>
        {action}
      </CardHeader>
      <CardContent className="px-4 sm:px-6">{children}</CardContent>
    </Card>
  );
}

function Configured({ ok }: { ok: boolean }) {
  return ok ? (
    <Badge variant="outline" className={cn("gap-1", TONE_CLASSES.success)}><CheckCircle2 className="size-3" /> Configured</Badge>
  ) : (
    <Badge variant="outline" className={cn("gap-1", TONE_CLASSES.neutral)}><CircleSlash className="size-3" /> Not configured</Badge>
  );
}

function PreferencesForm({ liveAvailable }: { liveAvailable: boolean }) {
  const [saved, setSaved] = useState<ExecutionPreferences>(loadPreferences);
  const [draft, setDraft] = useState<ExecutionPreferences>(saved);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const field = <K extends keyof ExecutionPreferences>(k: K, label: string, options: [ExecutionPreferences[K], string][]) => (
    <div className="space-y-1.5">
      <Label htmlFor={`pref-${k}`}>{label}</Label>
      <Select value={draft[k]} onValueChange={(v) => setDraft((d) => ({ ...d, [k]: v }))}>
        <SelectTrigger id={`pref-${k}`} className="w-full" aria-label={label}><SelectValue /></SelectTrigger>
        <SelectContent>{options.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  );
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        {field("mode", "Execution mode", [["auto", "Live when available, else Demo"], ["live", "Live (AI + web)"], ["demo", "Demo (templates)"]])}
        {field("execution_mode", "Research subtasks", [["parallel", "Parallel"], ["sequential", "Sequential"]])}
        {field("approval_mode", "Human approval", [["none", "Autonomous"], ["plan", "Approve plan"], ["tools", "Approve searches"], ["all", "Approve plan + searches"]])}
      </div>
      {draft.mode === "live" && !liveAvailable && (
        <p className="text-xs text-status-warning">Live execution is not available right now; New Task will start in Demo until provider keys are configured.</p>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-muted-foreground">Saved in this browser only. Used as the starting values on New Task; each task can still override them.</p>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={JSON.stringify(draft) === JSON.stringify(DEFAULT_PREFERENCES)}
                  onClick={() => setDraft(DEFAULT_PREFERENCES)}>Reset to defaults</Button>
          <Button size="sm" disabled={!dirty} onClick={() => {
            if (savePreferences(draft)) { setSaved(draft); toast.success("Execution defaults saved"); }
            else toast.error("This browser blocked local storage; preferences could not be saved.");
          }}><Save className="size-3.5" /> Save defaults</Button>
        </div>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const status = useSystemStatus();
  const health = useHealth();
  const models = useModels();
  const refresh = useRefreshModels();
  const agents = useAgents();
  const { theme, setTheme } = useTheme();
  const s = status.data;

  return (
    <>
      <PageHeader title="Settings" description="Connection status, model defaults and execution preferences." />
      <div className="space-y-6">
        {status.error && <ErrorState error={status.error} onRetry={() => status.refetch()} />}

        <Section title="System health" description="Live checks against the backend.">
          <dl className="grid gap-x-8 sm:grid-cols-2">
            <KeyValue label="API">
              {health.isLoading ? <Loader2 className="ml-auto size-3.5 animate-spin" /> : health.data ? (
                <span className="inline-flex items-center gap-1 text-status-success" data-health="ok"><CheckCircle2 className="size-3.5" /> Reachable</span>
              ) : <span className="inline-flex items-center gap-1 text-status-danger" data-health="down"><XCircle className="size-3.5" /> {errorMessage(health.error)}</span>}
            </KeyValue>
            <KeyValue label="Database">{health.data ? (health.data.database === "ok" ? "OK" : health.data.database) : "—"}</KeyValue>
            <KeyValue label="Version" mono>{s?.version ?? "—"}</KeyValue>
            <KeyValue label="Environment" mono>{s?.app_env ?? "—"}</KeyValue>
            <KeyValue label="Authentication">{s ? (s.auth_enabled ? "Enabled" : "Disabled") : "—"}</KeyValue>
            <KeyValue label="Live execution">
              {s ? (s.live_mode_available ? <span className="text-status-success">Available</span> : <span className="text-status-warning">Unavailable — demo only</span>) : "—"}
            </KeyValue>
          </dl>
        </Section>

        <Section title="Providers" description={<>API keys are read from the server's <span className="font-mono">.env</span> and never sent to the browser. To change one, edit <span className="font-mono">.env</span> and restart the backend.</>}
                 action={
                   <Button variant="outline" size="sm" disabled={refresh.isPending || !(models.data ?? []).some((p) => p.configured)}
                           onClick={() => refresh.mutate(undefined, { onSuccess: () => toast.success("Model lists refreshed"), onError: (e) => toast.error(errorMessage(e)) })}>
                     {refresh.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />} Test connections
                   </Button>
                 }>
          {status.isLoading || models.isLoading ? <LoadingRows rows={3} /> : (
            <div className="space-y-3">
              {s && s.missing_for_live.length > 0 && (
                <div role="note" className={cn("flex items-start gap-2 rounded-lg border px-3 py-2 text-sm", TONE_CLASSES.warning)}>
                  <KeyRound className="mt-0.5 size-4 shrink-0" />
                  <span>Live execution needs: {s.missing_for_live.map((m) => <code key={m} className="mx-0.5 font-mono">{m}</code>)}</span>
                </div>
              )}
              <ul className="divide-y rounded-lg border">
                {(s?.llm_providers ?? []).map((p) => {
                  const pm = models.data?.find((m) => m.provider === p.name);
                  const st = pm?.default_model_status ? MODEL_STATUS[pm.default_model_status] : null;
                  return (
                    <li key={p.name} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between" data-provider={p.name}>
                      <div className="min-w-0 space-y-0.5">
                        <div className="flex items-center gap-2">
                          <Server className="size-4 text-muted-foreground" />
                          <span className="font-medium capitalize">{p.name}</span>
                          {s?.default_llm_provider === p.name && <Badge variant="outline">Default</Badge>}
                        </div>
                        <p className="text-xs text-muted-foreground">
                          Key: <span className="font-mono">{ENV_KEY[p.name]}</span>
                          {p.default_model && <> · default model <span className="font-mono">{p.default_model}</span></>}
                          {st && <> · <span className={cn(st.tone === "danger" && "text-status-danger", st.tone === "success" && "text-status-success", st.tone === "warning" && "text-status-warning")}>{st.label}</span></>}
                          {pm?.configured && !pm.error && <> · {pm.models.length} model{pm.models.length === 1 ? "" : "s"} listed</>}
                          {pm?.error && <> · <span className="text-status-danger">listing failed ({pm.error})</span></>}
                        </p>
                      </div>
                      <Configured ok={p.configured} />
                    </li>
                  );
                })}
                {s && (
                  <li className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between" data-provider="tavily">
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2"><Activity className="size-4 text-muted-foreground" /><span className="font-medium">Tavily web search</span></div>
                      <p className="text-xs text-muted-foreground">Key: <span className="font-mono">{ENV_KEY.tavily}</span></p>
                    </div>
                    <Configured ok={s.search_provider.configured} />
                  </li>
                )}
              </ul>
            </div>
          )}
        </Section>

        <Section title="Models" description="Effective model per agent: the agent's own override, otherwise the server default."
                 action={<Button asChild variant="outline" size="sm"><Link to="/agents">Agent configuration</Link></Button>}>
          {agents.isLoading ? <LoadingRows rows={3} /> : agents.error ? <ErrorState error={agents.error} onRetry={() => agents.refetch()} /> : (
            <dl className="grid gap-x-8 sm:grid-cols-2">
              <KeyValue label="Default provider" mono>{s?.default_llm_provider ?? "—"}</KeyValue>
              {(agents.data ?? []).map((a) => (
                <KeyValue key={a.key} label={a.name} mono>
                  {!a.enabled ? "disabled" : a.effective_model ? `${a.effective_provider}:${a.effective_model}${a.model ? "" : " (default)"}` : "no model configured"}
                </KeyValue>
              ))}
            </dl>
          )}
        </Section>

        <Section title="Execution defaults" description="Starting values for the New Task form.">
          <PreferencesForm liveAvailable={!!s?.live_mode_available} />
          {s && (
            <div className="mt-6 border-t pt-4">
              <h3 className="mb-1 text-sm font-medium">Server limits</h3>
              <p className="mb-2 text-xs text-muted-foreground">Configured on the server (.env). Each task may override a limit within the allowed range.</p>
              <dl className="grid gap-x-8 sm:grid-cols-2">
                {s.limits.map((l) => (
                  <KeyValue key={l.name} label={l.name.replaceAll("_", " ")} mono>
                    {l.default === null ? "none" : l.kind === "float" ? formatCost(l.default) : formatNumber(l.default)}
                    <span className="text-muted-foreground"> ({l.min ?? "–"}–{l.max ?? "∞"})</span>
                  </KeyValue>
                ))}
                <KeyValue label="approval cost threshold" mono>{s.approval_cost_threshold_usd === null ? "disabled" : formatCost(s.approval_cost_threshold_usd)}</KeyValue>
                <KeyValue label="approval timeout" mono>{s.approval_timeout_hours === null ? "none" : `${s.approval_timeout_hours} h`}</KeyValue>
              </dl>
            </div>
          )}
        </Section>

        <Section title="Appearance">
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Theme">
            {([["light", "Light", Sun], ["dark", "Dark", Moon], ["system", "System", Monitor]] as [Theme, string, typeof Sun][]).map(([v, l, Icon]) => (
              <Button key={v} variant={theme === v ? "secondary" : "outline"} size="sm" role="radio" aria-checked={theme === v} onClick={() => setTheme(v)}>
                <Icon className="size-3.5" /> {l}
              </Button>
            ))}
          </div>
        </Section>
      </div>
    </>
  );
}

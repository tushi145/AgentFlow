import { AlertTriangle, Bot, Loader2, Lock, RefreshCw, RotateCcw, Save, Wrench } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/common";
import { TONE_CLASSES } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useAgents, useModels, useRefreshModels, useUpdateAgent } from "@/hooks/use-phase3d";
import { useSystemStatus } from "@/hooks/use-system";
import {
  type AgentDraft, BOUNDS, buildUpdate, DEFAULT, draftFrom, EFFORTS, modelChoice, parseModelChoice, validateDraft,
} from "@/lib/agent-form";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ApiError, errorMessage } from "@/services/api";
import type { AgentConfig, ProviderModels } from "@/types/api";

function Field({ id, label, hint, error, children }: { id: string; label: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error ? <p id={`${id}-error`} role="alert" className="text-xs text-status-danger">{error}</p>
        : hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function ModelSelect({ agent, draft, providers, onChange }: {
  agent: AgentConfig; draft: AgentDraft; providers: ProviderModels[] | undefined; onChange: (v: string) => void;
}) {
  const configured = (providers ?? []).filter((p) => p.configured);
  const current = parseModelChoice(draft.model);
  const savedListed = !current || configured.some((p) => p.provider === current.provider && p.models.some((m) => m.id === current.model));
  return (
    <Select value={draft.model} onValueChange={onChange} disabled={!configured.length && draft.model === DEFAULT}>
      <SelectTrigger id={`${agent.key}-model`} className="w-full" aria-label="Model"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value={DEFAULT}>
          Server default{agent.effective_model && draft.model === DEFAULT ? ` (${agent.effective_provider}: ${agent.effective_model})` : ""}
        </SelectItem>
        {!savedListed && current && (
          <SelectItem value={draft.model}>{current.provider}: {current.model} (not in provider list)</SelectItem>
        )}
        {configured.map((p) => (
          <SelectGroup key={p.provider}>
            <SelectLabel className="capitalize">{p.provider}</SelectLabel>
            {p.models.map((m) => (
              <SelectItem key={m.id} value={modelChoice(p.provider, m.id)}>
                {m.display_name}{m.display_name !== m.id ? ` · ${m.id}` : ""}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}

function AgentForm({ agent, providers, liveConfigured }: { agent: AgentConfig; providers: ProviderModels[] | undefined; liveConfigured: boolean }) {
  const update = useUpdateAgent();
  const [draft, setDraft] = useState<AgentDraft>(() => draftFrom(agent));
  const [serverError, setServerError] = useState<{ message: string; suggestions: string[] } | null>(null);
  const set = <K extends keyof AgentDraft>(k: K, v: AgentDraft[K]) => { setDraft((d) => ({ ...d, [k]: v })); setServerError(null); };

  const selected = parseModelChoice(draft.model);
  const modelInfo = selected ? providers?.find((p) => p.provider === selected.provider)?.models.find((m) => m.id === selected.model) : undefined;
  const errors = validateDraft(draft, agent, modelInfo?.max_output_tokens);
  const body = buildUpdate(agent, draft);
  const invalid = Object.keys(errors).length > 0;
  const effortApplies = (selected?.provider ?? agent.effective_provider) === "anthropic";

  const save = () => {
    if (!body || invalid) return;
    update.mutate({ key: agent.key, body }, {
      onSuccess: () => toast.success(`${agent.name} saved`),
      onError: (e) => {
        const suggestions = e instanceof ApiError && Array.isArray(e.details?.suggestions) ? (e.details!.suggestions as string[]) : [];
        setServerError({ message: errorMessage(e), suggestions });
      },
    });
  };

  return (
    <Card className="gap-0 py-5" data-agent-form={agent.key}>
      <CardContent className="space-y-6 px-4 sm:px-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">{agent.name}</h2>
              {agent.required && <Badge variant="outline" className="gap-1"><Lock className="size-3" /> Required</Badge>}
              {!agent.enabled && <Badge variant="outline" className={TONE_CLASSES.neutral}>Disabled</Badge>}
            </div>
            <p className="text-xs font-medium text-muted-foreground">{agent.role}</p>
            <p className="max-w-2xl text-sm text-muted-foreground">{agent.description}</p>
            <div className="flex flex-wrap items-center gap-1.5 pt-1 text-xs text-muted-foreground">
              <Wrench className="size-3" /> Tools:
              {agent.allowed_tools.length ? agent.allowed_tools.map((t) => <Badge key={t} variant="outline" className="font-mono text-[11px]">{t}</Badge>) : <span>none</span>}
            </div>
          </div>
          <label className="flex shrink-0 items-center gap-2 text-sm" htmlFor={`${agent.key}-enabled`}>
            <Switch id={`${agent.key}-enabled`} checked={draft.enabled} disabled={agent.required}
                    onCheckedChange={(v) => set("enabled", v)} aria-describedby={agent.required ? `${agent.key}-req` : undefined} />
            Enabled
          </label>
        </div>
        {agent.required && <p id={`${agent.key}-req`} className="-mt-4 text-xs text-muted-foreground">Planner, Research and Report are required by every execution.</p>}

        <div className="grid gap-5 md:grid-cols-2">
          <Field id={`${agent.key}-model`} label="Model" error={errors.model}
                 hint={!liveConfigured ? "No LLM provider is configured, so no models can be listed. Demo runs use templates regardless of this setting."
                   : modelInfo?.max_output_tokens ? `Max output ${modelInfo.max_output_tokens.toLocaleString("en-US")} tokens` : "Models listed by the provider API."}>
            <ModelSelect agent={agent} draft={draft} providers={providers} onChange={(v) => set("model", v)} />
          </Field>
          <Field id={`${agent.key}-effort`} label="Effort" hint={effortApplies ? "Reasoning effort sent to Anthropic models. Default lets the provider decide." : "Only Anthropic models use this setting."}>
            <Select value={draft.effort} onValueChange={(v) => set("effort", v as AgentDraft["effort"])}>
              <SelectTrigger id={`${agent.key}-effort`} className="w-full" aria-label="Effort"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT}>Provider default</SelectItem>
                {EFFORTS.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}
              </SelectContent>
            </Select>
          </Field>
          <Field id={`${agent.key}-mot`} label="Max output tokens" error={errors.max_output_tokens}
                 hint={`${BOUNDS.max_output_tokens.min.toLocaleString("en-US")}–${BOUNDS.max_output_tokens.max.toLocaleString("en-US")}`}>
            <Input id={`${agent.key}-mot`} inputMode="numeric" value={draft.max_output_tokens} aria-invalid={!!errors.max_output_tokens}
                   onChange={(e) => set("max_output_tokens", e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id={`${agent.key}-retries`} label="Max retries" error={errors.max_retries} hint="0–5">
              <Input id={`${agent.key}-retries`} inputMode="numeric" value={draft.max_retries} aria-invalid={!!errors.max_retries}
                     onChange={(e) => set("max_retries", e.target.value)} />
            </Field>
            <Field id={`${agent.key}-timeout`} label="Timeout (s)" error={errors.timeout_s} hint="10–900">
              <Input id={`${agent.key}-timeout`} inputMode="numeric" value={draft.timeout_s} aria-invalid={!!errors.timeout_s}
                     onChange={(e) => set("timeout_s", e.target.value)} />
            </Field>
          </div>
        </div>

        <Field id={`${agent.key}-instructions`} label="Additional instructions" error={errors.instructions}
               hint={<>Appended to the agent's built-in system prompt as operator instructions. The built-in prompt and its safety rules cannot be edited. <span className="tabular">{draft.instructions.length}/{BOUNDS.instructions.max}</span></>}>
          <Textarea id={`${agent.key}-instructions`} rows={5} value={draft.instructions} aria-invalid={!!errors.instructions}
                    placeholder="e.g. Prefer primary sources and official statistics." onChange={(e) => set("instructions", e.target.value)} />
        </Field>

        {serverError && (
          <div role="alert" className={cn("flex items-start gap-2 rounded-lg border px-3 py-2 text-sm", TONE_CLASSES.danger)}>
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div>
              <p>{serverError.message}</p>
              {serverError.suggestions.length > 0 && <p className="text-xs">Suggestions: {serverError.suggestions.join(", ")}</p>}
            </div>
          </div>
        )}

        <div className="flex flex-col-reverse gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            Last saved {formatDateTime(agent.updated_at)}{body ? " · unsaved changes" : ""}
          </p>
          <div className="flex gap-2">
            <Button variant="outline" disabled={!body || update.isPending} onClick={() => { setDraft(draftFrom(agent)); setServerError(null); }}>
              <RotateCcw className="size-3.5" /> Discard
            </Button>
            <Button disabled={!body || invalid || update.isPending} onClick={save}>
              {update.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />} Save changes
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function AgentsPage() {
  const agents = useAgents();
  const models = useModels();
  const refresh = useRefreshModels();
  const status = useSystemStatus();
  const [tab, setTab] = useState<string | null>(null);
  const list = useMemo(() => agents.data ?? [], [agents.data]);
  const active = tab ?? list[0]?.key;
  const liveConfigured = (models.data ?? []).some((p) => p.configured);

  return (
    <>
      <PageHeader
        title="Agent configuration"
        description="Models, limits and additional instructions for each agent. Changes apply to executions started afterwards."
        actions={
          <Button variant="outline" size="sm" disabled={refresh.isPending || !liveConfigured}
                  onClick={() => refresh.mutate(undefined, { onError: (e) => toast.error(errorMessage(e)) })}>
            {refresh.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />} Refresh models
          </Button>
        }
      />
      {models.data && !liveConfigured && (
        <div role="note" className={cn("mb-4 rounded-lg border px-4 py-3 text-sm", TONE_CLASSES.warning)}>
          No LLM provider is configured{status.data ? ` (set ${status.data.llm_providers.map((p) => `${p.name.toUpperCase()}_API_KEY`).join(" or ")} on the server)` : ""}. Limits and instructions can still be edited; model selection needs a configured provider.
        </div>
      )}
      {models.data?.filter((p) => p.configured && p.error).map((p) => (
        <div key={p.provider} role="alert" className={cn("mb-4 rounded-lg border px-4 py-3 text-sm", TONE_CLASSES.danger)}>
          Could not list {p.provider} models ({p.error}). The saved configuration still works; try Refresh models.
        </div>
      ))}
      {agents.error && <ErrorState error={agents.error} onRetry={() => agents.refetch()} />}
      {agents.isLoading ? <LoadingRows rows={6} /> : !list.length ? (
        !agents.error && <EmptyState icon={Bot} title="No agents found" description="The backend seeds the five agents on startup." />
      ) : (
        <Tabs value={active} onValueChange={setTab} className="gap-4">
          <TabsList className="h-auto w-full flex-wrap justify-start sm:w-fit">
            {list.map((a) => (
              <TabsTrigger key={a.key} value={a.key} className="flex-none">
                {a.name.replace(/ Agent$/, "")}
                {!a.enabled && <span className="text-[10px] text-muted-foreground">(off)</span>}
              </TabsTrigger>
            ))}
          </TabsList>
          {list.map((a) => (
            <TabsContent key={a.key} value={a.key}>
              {/* Remount on save so the form starts from the server's stored values. */}
              <AgentForm key={a.updated_at} agent={a} providers={models.data} liveConfigured={liveConfigured} />
            </TabsContent>
          ))}
        </Tabs>
      )}
    </>
  );
}

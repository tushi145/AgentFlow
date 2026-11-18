/* Agent configuration form model: draft ⇄ API, validation mirroring backend/app/schemas/agents.py,
   and a minimal PUT body containing only what changed. */

import type { AgentConfig, AgentConfigUpdate, Effort } from "@/types/api";

export const DEFAULT = "__default__";
export const EFFORTS: Effort[] = ["low", "medium", "high", "xhigh", "max"];
export const BOUNDS = {
  max_output_tokens: { min: 256, max: 64_000 },
  max_retries: { min: 0, max: 5 },
  timeout_s: { min: 10, max: 900 },
  instructions: { max: 4000 },
  model: { max: 128 },
} as const;

export interface AgentDraft {
  enabled: boolean;
  /** DEFAULT (server default model) or "<provider>::<model id>" */
  model: string;
  effort: Effort | typeof DEFAULT;
  max_output_tokens: string;
  max_retries: string;
  timeout_s: string;
  instructions: string;
}

export const modelChoice = (provider: string, model: string) => `${provider}::${model}`;
export function parseModelChoice(choice: string): { provider: "anthropic" | "gemini"; model: string } | null {
  if (choice === DEFAULT) return null;
  const i = choice.indexOf("::");
  return { provider: choice.slice(0, i) as "anthropic" | "gemini", model: choice.slice(i + 2) };
}

export function draftFrom(a: AgentConfig): AgentDraft {
  return {
    enabled: a.enabled,
    model: a.provider && a.model ? modelChoice(a.provider, a.model) : DEFAULT,
    effort: (a.effort as Effort | null) ?? DEFAULT,
    max_output_tokens: String(a.max_output_tokens),
    max_retries: String(a.max_retries),
    timeout_s: String(a.timeout_s),
    instructions: a.instructions,
  };
}

export type DraftErrors = Partial<Record<keyof AgentDraft, string>>;

function intError(raw: string, label: string, min: number, max: number): string | undefined {
  const v = raw.trim();
  if (!/^\d+$/.test(v)) return `${label} must be a whole number.`;
  const n = Number(v);
  if (n < min || n > max) return `${label} must be between ${min.toLocaleString("en-US")} and ${max.toLocaleString("en-US")}.`;
  return undefined;
}

/** `modelOutputLimit`: the selected model's max output tokens, when the provider reports it. */
export function validateDraft(d: AgentDraft, agent: AgentConfig, modelOutputLimit?: number | null): DraftErrors {
  const e: DraftErrors = {};
  if (!d.enabled && agent.required) e.enabled = `${agent.name} is required and cannot be disabled.`;
  const mot = intError(d.max_output_tokens, "Max output tokens", BOUNDS.max_output_tokens.min, BOUNDS.max_output_tokens.max);
  if (mot) e.max_output_tokens = mot;
  else if (modelOutputLimit && Number(d.max_output_tokens) > modelOutputLimit) {
    e.max_output_tokens = `The selected model allows at most ${modelOutputLimit.toLocaleString("en-US")} output tokens.`;
  }
  const mr = intError(d.max_retries, "Max retries", BOUNDS.max_retries.min, BOUNDS.max_retries.max);
  if (mr) e.max_retries = mr;
  const to = intError(d.timeout_s, "Timeout", BOUNDS.timeout_s.min, BOUNDS.timeout_s.max);
  if (to) e.timeout_s = to;
  if (d.instructions.length > BOUNDS.instructions.max) e.instructions = `Instructions are limited to ${BOUNDS.instructions.max} characters.`;
  const m = parseModelChoice(d.model);
  if (m && (!m.model || m.model.length > BOUNDS.model.max)) e.model = "Choose a valid model.";
  return e;
}

/** Only changed fields; `undefined` when nothing changed. */
export function buildUpdate(agent: AgentConfig, d: AgentDraft): AgentConfigUpdate | undefined {
  const base = draftFrom(agent);
  const body: AgentConfigUpdate = {};
  if (d.enabled !== base.enabled) body.enabled = d.enabled;
  if (d.model !== base.model) {
    const m = parseModelChoice(d.model);
    if (m) { body.provider = m.provider; body.model = m.model; } else body.clear_model_override = true;
  }
  if (d.effort !== base.effort) {
    if (d.effort === DEFAULT) body.clear_effort = true; else body.effort = d.effort;
  }
  for (const k of ["max_output_tokens", "max_retries", "timeout_s"] as const) {
    if (d[k].trim() !== base[k]) body[k] = Number(d[k].trim());
  }
  if (d.instructions !== base.instructions) body.instructions = d.instructions;
  return Object.keys(body).length ? body : undefined;
}

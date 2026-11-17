# AgentFlow — Architecture

> Status: **v1.1 (approved design + review amendments)**. This document is the source of truth for
> design decisions. Sections marked 🆕 were added in the review round (model configuration,
> cost/token governance, prompt-injection defence, human approval, evaluation, auth scope,
> live/demo separation).

## 1. Goals and non-goals

**Goals:** accept a natural-language objective, decompose it into a dependency graph of subtasks,
execute them with specialised agents that use real tools, verify claims against retrieved
evidence, and produce a cited report — while showing every step live and keeping a full audit
trail.

**Non-goals (v1):** multi-tenant SaaS, distributed workers, arbitrary code execution by agents,
browsing behind logins.

## 2. System overview

```
┌──────────────────────── Frontend (React 19 + Vite + TS) ─────────────────────────┐
│ Overview · New Task · Active · History · Reports · Agents · Evaluation · Settings │
│ TanStack Query (REST)     EventSource (SSE live stream)    Recharts   React Flow  │
└───────────────┬───────────────────────────────┬──────────────────────────────────┘
                │ REST /api/v1/*                │ SSE /api/v1/tasks/{id}/events
┌───────────────▼───────────────────────────────▼──────────────────────────────────┐
│ FastAPI: auth · validation · rate limiting · error envelope · request IDs         │
│   api/routes ─► services/ (TaskService, ApprovalService, ReportService, …)        │
│                    │                                                             │
│                    ▼                                                             │
│   ExecutionManager — asyncio runs, concurrency semaphore, cancel, resume          │
│                    │                                                             │
│                    ▼                                                             │
│   graph/ LangGraph StateGraph + checkpointer ──► agents/ (5 agents)               │
│        │            │                                │                            │
│        │      BudgetGuard 🆕                  llm/ providers                       │
│        │   (tokens/calls/time/cost)          (Anthropic · Gemini · Demo)           │
│        │                                     ModelRegistry 🆕 (live model lists)    │
│        ▼                                     tools/ (search, fetch, calculator,    │
│   EventBus (in-proc pub/sub +                  evidence retrieval)                 │
│   persisted execution_events)                security/ContentFirewall 🆕            │
│        │                                                                          │
│        ▼                                     evaluation/ 🆕 (run metrics,          │
│   database/ SQLAlchemy 2 async + Alembic       benchmark suites)                  │
│   SQLite (dev) / PostgreSQL (prod)                                               │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### Key decisions

| # | Decision | Why | Trade-off |
|---|---|---|---|
| D1 | **SSE** for live updates (not WebSockets) | Updates only flow from server to client; SSE reconnects natively and replays with `Last-Event-ID`. | No client→server channel. Approvals use plain REST. |
| D2 | **In-process asyncio execution** behind an `ExecutionManager` interface | No Redis/Celery to operate for a single-user app. | Not horizontally scalable; the interface keeps the swap to Arq/Celery to one change. |
| D3 | **LangGraph with a persistent checkpointer** (SQLite/Postgres saver) | Required for `interrupt()`-based human approval that survives restarts. Also gives replayable state. | Extra tables managed by LangGraph. |
| D4 | **Provider abstraction** returning validated Pydantic objects + usage | Agents never touch SDKs directly; tests inject fakes; Gemini/Demo are drop-in. | Lowest-common-denominator features. Provider-specific extras (fallbacks, effort) live in the provider config. |
| D5 | **Citations enforced in code** | The LLM may only cite `S<n>` IDs of rows actually stored; a post-processor strips and logs anything else. | The report can lose a sentence's citation, which is recorded as a violation. |
| D6 | **Progress = completed graph nodes/subtasks ÷ planned total** | No fake percentages. | Progress jumps when the plan is revised; the UI shows "replanned". |
| D7 | **ReportLab** for PDF | Pure Python, works on Windows without GTK. | Simpler typography than HTML→PDF. |
| D8 🆕 | **No model ID is hard-coded in code** | Models change; defaults come from env, per-agent overrides from the DB, and both are validated against the provider's live `models.list()`. | Live validation needs a key; offline we flag the ID as "unverified" rather than reject it. |
| D9 🆕 | **Web content is untrusted data**, isolated structurally (see §7) | Prompt injection is the main attack surface of a research agent. | Some legitimate text is flagged; flags are surfaced, never silently dropped. |
| D10 🆕 | **Live and demo are separate execution modes chosen at task creation** and stored on every row | Demo output can never be mistaken for real AI execution. | Live runs are refused (HTTP 409) if keys are missing, rather than silently degrading to demo. |

## 3. Multi-agent workflow (LangGraph)

```
 START
   │
   ▼
┌──────────┐   ExecutionPlan (subtask DAG, success criteria, cost estimate)
│ planner  │
└────┬─────┘
     ▼
◇ approval gate ◇  🆕 interrupt() if approval_mode ∈ {plan, all}
     │               or estimated cost > threshold → status=awaiting_approval
     ▼
┌──────────┐   picks every research subtask whose depends_on are complete
│ dispatch │── Send() fan-out ──► research(subtask) × N   (parallel mode)
└──────────┘                      or one at a time          (sequential mode)
     ▲   (tool approval gate 🆕 before search/fetch when approval_mode ∈ {tools, all})
     │                                  │ reducers merge sources/facts
     │                                  ▼
     │                           ┌──────────┐
     │                           │ analysis │  comparisons, patterns, calculator tool
     │                           └────┬─────┘
     │                                ▼
     │                         ┌──────────────┐ per-claim verdict + confidence + reasoning,
     │                         │ verification │ evidence retrieved by embeddings/BM25
     │                         └──────┬───────┘
     │                                ▼
     └── gap_queries ◄── NO ── ◇ sufficient? ◇   (and iteration < max and budget left)
                                      │ YES / limits reached (gaps disclosed)
                                      ▼
                                ┌──────────┐
                                │  report  │ Markdown + PDF, citation filter (D5)
                                └────┬─────┘
                                     ▼
                               ┌────────────┐
                               │ evaluate 🆕 │ deterministic run metrics
                               └────┬───────┘
                                    ▼
                                   END
```

### Shared state (`AgentFlowState`, TypedDict with reducers)

`task_id`, `objective`, `mode`, `settings`, `plan`, `subtask_status` (merge), `sources` (append,
dedup by URL), `facts` (append), `analysis`, `claims`, `verifications`, `gap_queries`,
`iteration`, `errors` (append), `budget_exhausted`, `report`.

Token/call/cost accounting is **not** kept in graph state. It is held in a per-task
`BudgetGuard` object and persisted on `agent_executions`, so parallel branches can't race on it.

### Agent contracts

| Agent | Input | Output (Pydantic) | Tools allowed | Failure policy |
|---|---|---|---|---|
| Planner | objective, enabled agents, limits | `ExecutionPlan` | none | Fatal after retries |
| Research | one subtask + queries | `ResearchResult{facts[], coverage_notes}` | `web_search`, `fetch_page` | Non-fatal: subtask marked failed, gap disclosed |
| Analysis | facts + source index | `AnalysisResult{findings[], comparisons[], calculations[], claims[]}` | `calculator` | Fatal after retries |
| Verification | claims + retrieved evidence chunks | `VerificationReport{verdicts[], sufficient, gap_queries[]}` | `retrieve_evidence` | Fatal after retries |
| Report | verified findings + sources | `ReportDraft{sections[], …}` → MD/PDF | none | Fatal after retries |

Tool access is enforced by an **allowlist per agent** in code, not by prompt.

### Reliability

* Provider calls use tenacity exponential backoff with jitter on 429, 5xx, timeouts and
  connection errors. The SDK's own retries are disabled so the policy is visible and testable.
* Each node has a LangGraph `RetryPolicy`. Structured-output validation failures get one repair
  retry with the validation error fed back.
* Hard caps: `max_iterations`, `max_subtasks`, `max_searches_per_subtask`, plus every budget in §5.
* On restart, runs left in `running` are marked `interrupted`. Runs in `awaiting_approval` stay
  resumable, because their state lives in the checkpointer.

## 4. 🆕 Model configuration and validation

* **Environment:** `DEFAULT_LLM_PROVIDER`, `ANTHROPIC_DEFAULT_MODEL`, `GEMINI_DEFAULT_MODEL`,
  optional `AGENT_MODEL_<AGENT>` overrides. `.env.example` holds suggested values. The code has
  no literal model IDs.
* **Per-agent settings (DB):** `provider`, `model` (nullable means use the env default),
  `effort`, `max_output_tokens`, `instructions`, `enabled`.
* **`ModelRegistry`:** calls `client.models.list()` for each configured provider and caches the
  result for 1 hour. It runs
  1. when an agent setting is saved, rejecting unknown IDs with 422 and a list of valid ones;
  2. at task start, failing fast with a clear error if any enabled agent's model is unavailable;
  3. on `GET /models`, which feeds the frontend model picker.
* If the provider can't be reached, the ID is marked `unverified` and the UI warns. It is never
  silently accepted as valid.

## 5. 🆕 Cost and token governance

Per-task **limits** (defaults from env, overridable per task, bounded by server maxima):

| Limit | Enforcement point |
|---|---|
| `max_total_tokens` | `BudgetGuard.reserve()` before each LLM call. `max_tokens` for the call is clamped to what remains. |
| `max_llm_calls`, `max_tool_calls` | Checked before each call; tool calls include search and fetch. |
| `timeout_seconds` | `asyncio.timeout` around the whole run. |
| `max_cost_usd` (optional) | Checked against accrued cost before each call when pricing is known. |

A breach raises `BudgetExceeded`. The graph routes to **report-with-partial-results** when enough
verified material exists; otherwise it fails with `budget_exhausted`. Either way the reason is
shown.

**Accounting:** every `agent_executions` row records `input_tokens`, `output_tokens`,
`cache_read_tokens`, `llm_calls`, `tool_calls` and `cost_usd`. Task totals are sums, shown per
agent in the UI.

**Pricing:** a `pricing.json` table of `{provider: {model: {input_per_mtok, output_per_mtok}}}`
plus search credits, overridable with `PRICING_FILE`. If a model has no price entry, the cost is
`null` and labelled "pricing not configured". It is never guessed.

**Pre-run estimate:** after planning, `CostEstimator` multiplies the planned agent calls by
per-agent token profiles (rolling averages from past runs, or conservative defaults). The result
is always labelled *estimate* and drives the approval threshold.

## 6. 🆕 Human approval checkpoints

`approval_mode` per task: `none` | `plan` | `tools` | `all`, plus
`approval_cost_threshold_usd`. The cost threshold applies in every mode, `none` included.

| Checkpoint | When | What the user sees | Possible decisions |
|---|---|---|---|
| Plan | after planner | Subtasks, dependencies, estimated cost and tokens | approve / reject (+ comment) |
| Tool batch | before each research dispatch | Exact search queries, max fetches | approve / reject |
| Expensive task | estimate > threshold | Estimate breakdown | approve / reject |

**Implementation:** the node calls LangGraph `interrupt(payload)`. The run persists in the
checkpointer, an `approval_requests` row is created, and the task becomes `awaiting_approval`
with an SSE event. `POST /approvals/{id}/decision` resumes the graph with
`Command(resume=decision)`. Rejecting ends the run as `rejected`. Pending approvals time out after
`APPROVAL_TIMEOUT_HOURS`.

## 7. 🆕 Prompt-injection defence (`security/ContentFirewall`)

All fetched pages, search snippets and titles are **untrusted data**. The defence is layered:

1. **Channel separation:** untrusted text never goes into `system`. It appears only inside user
   content wrapped as
   `<untrusted_source id="S3" url="…">…</untrusted_source>`.
2. **Sanitisation:** strips control and zero-width characters, neutralises any
   `<untrusted_source` or `</untrusted_source` sequence inside content (so the wrapper can't be
   closed early), collapses whitespace, and truncates to a per-source budget with an explicit
   marker.
3. **Detection:** a heuristic scanner catches instruction-like patterns ("ignore previous
   instructions", role or system-prompt claims, tool-call JSON, "you are now…", hidden-text
   tricks) and sets `injection_suspected` with matched rules on the source. This raises an
   event, lowers trust in verification and shows a badge in the UI. Content is never silently
   dropped.
4. **Standing instructions:** every agent's system prompt states that content in
   `untrusted_source` tags is evidence to analyse, never instructions, and that requests found in
   it must be reported, not followed.
5. **Capability limits:** agents have no side-effecting tools. Search queries come from the
   planner or the verifier, never from page text. `fetch_page` only fetches URLs returned by the
   search provider for this task, so a page can't steer the agent to new URLs.
6. **SSRF guard:** http/https only, DNS resolved and private, loopback and link-local ranges
   rejected, redirects re-checked, size and time limits, content-type allowlist.
7. **Output checks:** schemas are validated, citation IDs must exist (D5), and output that echoes
   an injection marker is flagged.

## 8. 🆕 Evaluation module

| Metric | How it's measured | Source of truth |
|---|---|---|
| **Claim-verification accuracy** | Benchmark suite: labelled `(claim, evidence, gold_verdict)` items run through the real Verification agent. Reports accuracy, per-class precision/recall and a confusion matrix. | `evals/datasets/verification.jsonl` (hand-labelled, synthetic evidence clearly marked) |
| **Citation correctness** | Per run: (a) *integrity*, the percentage of report citations that resolve to stored sources (deterministic); (b) *support*, the percentage of sampled cited sentences whose cited evidence entails them (optional LLM judge, cost-capped, method recorded). | Run data |
| **Research source coverage** | Per run: share of research subtasks with ≥1 accepted source, sources per subtask, unique domains, share of claims with ≥1 supporting source. | Run data |
| **Task completion rate** | Completed ÷ terminal tasks, by mode and over time. | `tasks` |
| **Agent execution failures** | Failure and retry counts and rates by agent and error class. | `agent_executions` |

Per-run metrics are computed by the `evaluate` node and stored in `evaluation_results`. Benchmark
runs are stored in `benchmark_runs`. Live benchmarks need keys and are never run in demo mode.

## 9. 🆕 Live vs demo mode

* `mode` is chosen when the task is created and stored on `tasks`, `agent_executions`,
  `research_sources` and `reports`.
* **Live** requires a configured LLM provider and search provider. Otherwise `POST /tasks`
  returns 409 with setup instructions. There is no silent fallback.
* **Demo** uses `app/demo/` (`DemoLLMProvider`, `DemoSearchProvider`), deterministic templated
  output. Demo sources use the `demo://` scheme and the title prefix `[DEMO]`, so they can't be
  mistaken for real URLs. The UI shows a persistent DEMO banner. Reports carry a watermark and a
  disclaimer section. Demo runs are excluded from evaluation aggregates.
* The demo package is imported only by the provider factory, and real agents contain no demo
  branches.

## 10. 🆕 Authentication (deliberately simple)

A single local user is created from `ADMIN_USERNAME` and `ADMIN_PASSWORD_HASH` (or
`ADMIN_PASSWORD` in dev) on first start. Login returns a signed JWT in an httpOnly, SameSite=Lax
cookie. `AUTH_ENABLED=false` is allowed only when bound to localhost. There's no registration,
roles or OAuth. The `users` table exists so ownership is modelled correctly for later.

## 11. Data model

```
users              id, username UNIQUE, password_hash, created_at
agent_configs      id, key UNIQUE, name, description, enabled, provider, model NULL,
                   effort, max_output_tokens, instructions, max_retries, timeout_s, updated_at
tasks              id uuid, user_id, prompt, title, mode(live|demo),
                   status(queued|planning|awaiting_approval|running|completed|failed|
                          cancelled|rejected|interrupted),
                   execution_mode(parallel|sequential), approval_mode, limits_json,
                   progress_done, progress_total, iterations,
                   input_tokens, output_tokens, llm_calls, tool_calls, cost_usd NULL,
                   estimated_cost_usd NULL, budget_exhausted, error_code, error_message,
                   created_at, started_at, finished_at
subtasks           id, task_id, plan_key, title, description, agent_key, depends_on json,
                   queries json, status, iteration, result_json, error
agent_executions   id, task_id, subtask_id NULL, agent_key, iteration, attempt, status, mode,
                   provider, model, input_summary json, output_json,
                   input_tokens, output_tokens, cache_read_tokens, llm_calls, tool_calls,
                   cost_usd NULL, duration_ms, error_code, error_message, started_at, finished_at
execution_events   id (monotonic), task_id, agent_execution_id NULL, type, message,
                   payload json, created_at
research_sources   id, task_id, ref "S<n>", url, title, domain, snippet, content_excerpt,
                   search_query, provider, relevance_score, injection_suspected,
                   injection_rules json, mode, retrieved_at          UNIQUE(task_id, url)
verification_results id, task_id, iteration, claim, verdict(supported|partially_supported|
                   unsupported|conflicting|unverifiable), confidence, reasoning,
                   supporting_refs json, conflicting_refs json
reports            id, task_id UNIQUE, title, summary, markdown, pdf_path, source_count,
                   avg_confidence, citation_violations, mode, created_at
approval_requests  id, task_id, kind(plan|tools|cost), payload json,
                   status(pending|approved|rejected|expired), comment, created_at, decided_at
evaluation_results id, task_id UNIQUE, metrics json, created_at
benchmark_runs     id, suite, provider, model, metrics json, item_results json, status,
                   cost_usd NULL, created_at
```

## 12. API surface (`/api/v1`)

See `docs/api_documentation.md` for request and response schemas. Summary:

* **auth:** `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
* **tasks:** `POST /tasks`, `GET /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`,
  `POST /tasks/{id}/retry`, `DELETE /tasks/{id}`, `GET /tasks/{id}/events` (SSE),
  `GET /tasks/{id}/sources`, `GET /tasks/{id}/verifications`, `GET /tasks/{id}/usage`
* **approvals 🆕:** `GET /approvals?status=pending`, `POST /approvals/{id}/decision`
* **reports:** `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/download.md`,
  `GET /reports/{id}/download.pdf`
* **agents and models:** `GET /agents`, `PUT /agents/{key}`, `GET /models`
* **evaluation 🆕:** `GET /evaluations/summary`, `GET /evaluations/tasks/{id}`,
  `GET /evaluations/benchmarks`, `POST /evaluations/benchmarks/{suite}/run`
* **analytics and system:** `GET /analytics/overview`, `GET /system/status`, `GET /health`

Errors share one envelope: `{"error": {"code", "message", "request_id"}}`.

## 13. Security checklist

Secrets come only from env and are never returned by the API; a logging filter redacts
key-shaped strings. Pydantic validates every input with size limits on prompts and instructions.
Rate limits apply to `POST /tasks`, login and benchmark runs. Downloads are served by ID only and
their paths are resolved and checked against the reports directory. CORS is restricted to the
configured origin. Plus the request timeouts, backoff, and prompt-injection controls in §7.

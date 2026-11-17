# AgentFlow — Implementation Roadmap

Every stage ends with the app runnable and its tests green. Status: ✅ done · 🔄 in progress · ⬜ todo.

## Phase 1 — Architecture and scaffolding
- ✅ Environment inspection, architecture (`docs/architecture.md`), roadmap
- ✅ Repo scaffold, `.gitignore`, `.env.example`, backend package layout, venv + pinned deps

## Phase 2 — Backend
- **2A Foundation:** config/settings, structured logging with secret redaction, async DB
  + all ORM models, error envelope, health/system status, simple auth, agent-config seed
- **2B AI infrastructure:** provider abstraction (Anthropic, Gemini, Demo), ModelRegistry
  (live model validation), BudgetGuard + pricing + cost estimator, ContentFirewall, tools (search,
  SSRF-safe fetch, calculator, evidence retrieval)
- **2C Agents:** Planner, Research, Analysis, Verification, Report with Pydantic contracts and prompts
- **2D Orchestration:** LangGraph graph, parallel fan-out, verification loop, approval interrupts,
  checkpointer, ExecutionManager, EventBus, persistence of every step
- **2E APIs:** tasks, SSE events, approvals, reports (MD/PDF), agents/models, analytics, evaluation

## Phase 3 — Frontend
- **3A** Vite/TS/Tailwind/shadcn shell, themes, routing, API client, auth
- **3B** Overview dashboard, New Task, History
- **3C** Live Execution page (SSE, DAG, timeline, approvals, budgets)
- **3D** Reports, Agent configuration, Evaluation, Settings

## Phase 4 — Advanced functionality
- Iterative gap research tuning, embeddings retrieval (Gemini) with BM25 fallback, PDF polish,
  cancel/retry/resume, benchmark suites, cost-estimate calibration from history

## Phase 5 — QA
- Full backend + frontend test runs, live E2E (`pytest -m live`) when keys exist,
  responsive checks, injection red-team fixtures, bug fixes

## Phase 6 — Documentation and deployment
- Dockerfiles, compose (Postgres on host port 5433), README with diagrams, API docs, user guide,
  sample report from a real live run

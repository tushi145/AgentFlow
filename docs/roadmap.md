## Progress
- [x] Phase 1 – Architecture and scaffold
- [x] Phase 2 – Backend (agents, LangGraph orchestration, REST + SSE API, reports, evaluation)
- [x] Phase 3A – Frontend foundation: design system, app shell, auth, system status
- [x] Phase 3B – Overview, New Task, Active Executions, Execution History (all data from backend APIs)
- [x] Phase 3C – Live Execution: workflow graph, SSE timeline (reconnect + replay), sources, verification, approvals, outputs
- [x] Phase 3D – Reports viewer, Agent configuration, Evaluation dashboard, Settings
- [x] Phase 4 – Advanced AI capabilities (implemented; live runs with credentials pending, see [phase4.md](phase4.md))
- [ ] Phase 5 – next (not started)

### Phase 3D notes
- Reports render with react-markdown + remark-gfm. Raw HTML is never rendered (reports contain text derived
  from untrusted pages); only http(s) URLs become links; `demo://` is shown as text; remote images are not loaded.
- Backend (additive): `/evaluations/summary` gained `duration_s` and `tokens` (completed LIVE runs, null when
  none); new `GET /evaluations/runs` lists per-run recorded metrics (mode-filterable, demo rows labelled).
- Execution defaults (mode, parallelism, approval) are stored in the browser (localStorage); the backend has
  no per-user preference store. API keys are only reported as configured / not configured.
- Agent model selection needs a configured provider; without keys only limits and instructions are editable.
- `npm audit`: 4 high-severity findings in Vite's dependency chain (picomatch); pre-existing, fix requires a
  breaking Vite change.

### Phase 3C notes
- Event stream: native EventSource reconnect (Last-Event-ID) + manual `?after=<id>` reconnect with backoff + a
  35 s heartbeat watchdog (the Vite dev proxy keeps a dead upstream stream open silently; prod proxies may too).
- Backend fix: `approval_decided` is now published by `ExecutionManager.decide_approval`; previously it was never
  emitted (the resumed node returned before its emit).
- The SSE restart test (`e2e/execution-3c.spec.ts`) needs `E2E_BACKEND_RESTART_CMD`; without it the test approves
  while connected instead. Run that spec in its own Playwright invocation (the restarted server may not outlive
  the worker on Windows).
- Verdicts use the backend vocabulary (supported / partially_supported / unsupported / conflicting / unverifiable);
  there is no separate "contradicted" value — "conflicting" covers it.
- Report links are downloads (MD/PDF); the in-app report viewer is Phase 3D.
- Browser tests cover demo mode only (no live API keys configured).

### Phase 3B notes
- Live execution is gated on `ANTHROPIC_API_KEY` + `TAVILY_API_KEY`; browser tests so far cover demo mode only.
- Production bundle: main chunk ~504 kB and Overview (Recharts) ~384 kB minified; further splitting is a follow-up.
- Mobile: the Agent performance table scrolls horizontally rather than reflowing.


### Database migrations
- Alembic is wired in (`backend/migrations`, revisions `0001_baseline`, `0002_tasks_runtime_state`). The app
  migrates on startup; `python -m app.database.migrations` does the same manually.
- SQLite databases are backed up (online backup API, verified) to `<data_dir>/backups/` before any schema change;
  if the backup fails, nothing is migrated. Pre-Alembic databases are adopted (stamped at the baseline, then
  upgraded). Fresh databases are created from the models and stamped at head.
- New schema changes: `alembic revision -m "..."` from `backend/`, then edit the generated file.

## Known limitations / follow-ups (tracked)
- Postgres: migrations run without an automatic backup (a warning is logged); take a `pg_dump` first (Phase 6).
- Embedding-based evidence retrieval (Gemini) not wired yet; BM25 is used and reported.
- Live (credentialed) Phase 4 verification still to run: `pytest -m live -s` and the live verification benchmark.
- LangGraph checkpoints use a SQLite file even when the main DB is Postgres (fine for single-node).
- SSRF guard resolves DNS before connecting; a DNS-rebinding race is theoretically possible (documented).

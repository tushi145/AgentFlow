## Progress
- [x] Phase 1 – Architecture and scaffold
- [x] Phase 2 – Backend (agents, LangGraph orchestration, REST + SSE API, reports, evaluation)
- [x] Phase 3A – Frontend foundation: design system, app shell, auth, system status
- [x] Phase 3B – Overview, New Task, Active Executions, Execution History (all data from backend APIs)
- [x] Phase 3C – Live Execution: workflow graph, SSE timeline (reconnect + replay), sources, verification, approvals, outputs
- [x] Phase 3D – Reports viewer, Agent configuration, Evaluation dashboard, Settings
- [x] Phase 4 – Advanced AI capabilities: **implemented and verified offline** (see [phase4.md](phase4.md))
- [ ] Live AI integration & end-to-end validation – **pending**: test suite and spend caps ready, awaiting
      API keys and approval to run paid tests (runbook: [phase4.md § Live verification runbook](phase4.md#live-verification-runbook))
- [ ] Phase 5 – not started

### Finalization (2026-11-25)
Full offline re-verification, demo-mode user journey and security sweep. Two genuine bugs fixed, each
with a regression test:
- **Budget exhaustion in parallel branches crashed the task.** When two parallel Research branches hit a
  limit in the same step, both wrote `budget_note` (a plain LangGraph channel) → `InvalidUpdateError` and the
  task failed instead of producing a budget-limited report. `budget_note` now has a reducer
  (`graph/state.py`); test: `test_budget_exhausted_in_parallel_branches_still_produces_report`.
- **Production accepted the `.env.example` JWT placeholder.** Startup now also refuses
  `change-me…` secrets when `APP_ENV=production`; test: `test_production_refuses_known_placeholder_jwt_secret`.
- **Pending-approval card overflowed on phones.** On Active Executions at 390 px the card was 8 px wider
  than the screen (a grid item can't shrink below its nowrap content). Fixed with `min-w-0`; the 3B
  Playwright responsive check now creates a pending approval so it covers this case.
- E2E: the 3C spec's expected "not found" 404s are now excluded by URL instead of by timing (it raced
  when a sibling request logged its 404 late).
- Docs: README rewritten; stale references removed (docker compose, React Flow).

Live credentialed verification is still pending (no API keys in this environment).

### Live verification status
| Check | Status |
|---|---|
| Credential-free real-API checks (invalid keys, timeout) | ✅ passed against real Anthropic/Tavily endpoints |
| Offline dry run of the five agent tests, the e2e test and the benchmark (fakes, sockets blocked) | ✅ passed |
| Fake-key secret-exposure tests (API, errors, events, logs, tracebacks) | ✅ passed |
| Model listing + default-model validation | ⏳ pending (needs `ANTHROPIC_API_KEY`) |
| Planner / Research / Analysis / Verification / Report agents, each run live (≤ $0.40 each) | ⏳ pending (Research also needs `TAVILY_API_KEY`) |
| Real Tavily search | ⏳ pending (needs `TAVILY_API_KEY`) |
| Five-agent live end-to-end run (≤ $2.00 hard cap) | ⏳ pending (needs both keys) |
| Live verification benchmark, 26 items (≤ $1.50 hard cap) | ⏳ pending (needs `ANTHROPIC_API_KEY`) |

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
- Live (credentialed) verification still to run; see the status table above.
- LangGraph checkpoints use a SQLite file even when the main DB is Postgres (fine for single-node).
- SSRF guard resolves DNS before connecting; a DNS-rebinding race is theoretically possible (documented).

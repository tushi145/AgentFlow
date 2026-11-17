## Progress
- [x] Phase 1 – Architecture and scaffold
- [x] Phase 2 – Backend (agents, LangGraph orchestration, REST + SSE API, reports, evaluation)
- [x] Phase 3A – Frontend foundation: design system, app shell, auth, system status
- [x] Phase 3B – Overview, New Task, Active Executions, Execution History (all data from backend APIs)
- [ ] Phase 3C – next (not started)

### Phase 3B notes
- Live execution is gated on `ANTHROPIC_API_KEY` + `TAVILY_API_KEY`; browser tests so far cover demo mode only.
- Production bundle: main chunk ~504 kB and Overview (Recharts) ~384 kB minified; further splitting is a follow-up.
- Mobile: the Agent performance table scrolls horizontally rather than reflowing.


## Known limitations / follow-ups (tracked)
- Alembic migrations not yet generated (dev uses `create_all`); required before Postgres deploy (Phase 6).
- Embedding-based evidence retrieval (Gemini) not wired yet; BM25 is used and reported (Phase 4).
- LangGraph checkpoints use a SQLite file even when the main DB is Postgres (fine for single-node).
- SSRF guard resolves DNS before connecting; a DNS-rebinding race is theoretically possible (documented).


## Known limitations / follow-ups (tracked)
- Alembic migrations not yet generated (dev uses `create_all`); required before Postgres deploy (Phase 6).
- Embedding-based evidence retrieval (Gemini) not wired yet; BM25 is used and reported (Phase 4).
- LangGraph checkpoints use a SQLite file even when the main DB is Postgres (fine for single-node).
- SSRF guard resolves DNS before connecting; a DNS-rebinding race is theoretically possible (documented).

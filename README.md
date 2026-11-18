# AgentFlow

**An autonomous multi-agent research platform.** AgentFlow takes a natural-language objective, has
a Planner decompose it into a dependency graph, runs Research agents in parallel against the real
web, analyses and verifies every claim against retrieved evidence, and produces a cited
Markdown/PDF report. The whole execution streams live to the UI.

> 🚧 **Status: Phase 2 (backend), Phase 3A (frontend shell), Phase 3B (Overview, New Task, Active
> Executions, History) and Phase 3C (Live Execution) complete.** Phase 3D is next. See [docs/roadmap.md](docs/roadmap.md).

## Highlights

- **LangGraph orchestration:** typed shared state, dependency-aware parallel fan-out (`Send`),
  verification-driven research loops with hard iteration limits, and human-approval checkpoints
  through `interrupt()` with a persistent checkpointer.
- **Five specialised agents** with Pydantic contracts: Planner, Research, Analysis,
  Verification, Report.
- **Grounded output:** citations are enforced in code, references are generated from stored
  sources, and every claim gets a verdict, a confidence score and reasoning.
- **Cost governance:** per-task token, call, time and cost limits, per-agent usage, cost
  estimates before execution, and a reserve that keeps a final report possible.
- **Prompt-injection defence:** web content is always untrusted data, sanitised, flagged and
  isolated from instructions. See [architecture §7](docs/architecture.md).
- **Honest demo mode:** without API keys you can exercise the full pipeline with clearly
  labelled templates and `demo://` sources. It's never presented as AI output.
- **Evaluation module:** per-run citation integrity, source coverage, verification stats and
  failure rates, plus a labelled verification-accuracy benchmark.

## Quick start (backend)

```bash
cp .env.example .env            # add ANTHROPIC_API_KEY and TAVILY_API_KEY for live runs
cd backend
python -m venv .venv
.venv/Scripts/activate           # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

Open interactive API docs at <http://localhost:8000/api/docs>. Log in with `ADMIN_USERNAME` /
`ADMIN_PASSWORD` from `.env` (defaults: `admin` / `admin` in development).

```bash
# Run the test suite (no network, no keys needed)
cd backend && python -m pytest
# Real end-to-end run against Anthropic + Tavily (costs a few cents; needs keys)
python -m pytest -m live -s
```

## Documentation

- [Architecture and design decisions](docs/architecture.md)
- [Roadmap](docs/roadmap.md)

# Phase 4 – Advanced AI Capabilities and Real-World Integration

Status: **implemented and tested offline; live runs with credentials are still pending.** This
environment had no `ANTHROPIC_API_KEY` or `TAVILY_API_KEY`, so the credentialed live tests were
skipped, not passed (see [Live verification](#live-verification)).

Branch `feature/phase-4-advanced-ai`, on top of Phase 3D (`ca50c58`). Backend only; no frontend
pages were added. All existing APIs keep their shape. New data is additive (new metric blocks,
new event types, a new `guards` benchmark suite).

## 1. Gap analysis (before Phase 4)

| Area | Already present | Genuinely missing / broken |
|---|---|---|
| Providers | Anthropic structured outputs (`beta.messages.parse`), refusal/truncation handling, typed error mapping, live model validation, live mode refuses to start without keys | Truncation misclassified as invalid JSON (SDK parses before `stop_reason` is checked); tokens of invalid outputs not charged; `retry-after` ignored; fallbacks sent to models without them (Haiku); Tavily 429 treated as permanent quota, 403/432/433 retried |
| Agents | Five agents with Pydantic contracts, tool allowlists in code, schema-repair round | No truncation retry; nothing checked that a fact's quote really occurs in its source |
| Orchestration | `Send` fan-out, dependency layers, verification-driven gap loops, `max_iterations`, `interrupt()` approvals, checkpointer, cancellation | **Approval race** (decision overwritten / 409 after commit / stuck task); `approval_requested` could be persisted after `approval_decided`; one analysis/verification error failed the whole task; gap loops re-ran executed queries; searches within a subtask were sequential and unbounded across branches |
| Quality | Citation filtering to known ids, verdict guards, injection-flag confidence cap | URL dedup only stripped `/`; no content dedup, relevance threshold or domain diversity; no tag-character or verifier-directed injection rules; links/images/HTML in report prose; conflicts disclosed only if the model chose to |
| Cost | Token/call/time limits, report reserve, unknown pricing → `null` | Cost cap checked only *before* a call (a single call could overshoot); cap silently unenforceable for unpriced models; unknown cache-read price counted as $0; no Haiku long-context tier |
| Evaluation | Run metrics (citation integrity, coverage, verdicts), verification benchmark | No grounding, citation-support, cost or latency metrics; benchmark silently truncated beyond 24 claims; no calibration; no offline benchmark of the safety gates |

## 2. What changed

### Batch 1: orchestration reliability (`0f7b7c3`)
- **Approval race fixed.** `resume()` no longer waits 15 s and then raises 409 after the approval
  was committed. A resumed run is chained behind the paused run while it unwinds. `paused` is
  only published while an approval is really pending (`mark_awaiting_if_pending`). Terminal
  states are write-once (`finish_task_if_active`): exactly one `task_finished` per task.
- **Failure recovery.** Analysis failure: raw facts become claims and are still verified.
  Verification failure: claims are reported unverified. Both emit `agent_degraded`, appear in the
  report's limitations, and are counted in `reliability.degraded_agents`.
- Gap research skips queries already executed (`gap_queries_deduplicated`, `iteration_skipped`).
- Queries of a subtask run concurrently, bounded per task by `MAX_PARALLEL_SEARCHES` (1 in sequential mode).

### Batch 2: provider hardening and cost governance (`2780fc4`)
- Anthropic: the raw response is read first, so stop reason and usage are known before parsing.
  `retry-after` is honoured (capped at 60 s), and 529 is reported as `overloaded`. Fallbacks
  (`server-side-fallback-2026-07-01`, `fallbacks: "default"`) are sent only to Fable/Mythos 5.x,
  Opus 5.x and Sonnet 5.5. One truncation retry with a doubled allowance (≤ 32K), unless the
  budget was the limit.
- Tavily: 429 is retryable, plan/credit errors (403/432/433) are not.
- Cost cap is enforced before each call: output is clamped so the worst-case cost fits the
  remaining cap minus in-flight reservations, with a priced report reserve (based on the report
  model's real price). A cost cap with any unpriced model or search provider now fails up front
  (`cost_limit_unenforceable`).
- Pricing: Haiku 5.5 bills prompts over 100K tokens at the long-context rate ($0.50/$2.50).
  Unknown cache-read pricing makes cost `null`. The bundled prices match the published table
  (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50, Fable 5.1 $10/$50).

### Batch 3: research and verification quality (`d176c8d`)
- Sources: canonical-URL dedup, near-duplicate content merging (8-gram shingle Jaccard ≥ 0.85)
  and a relevance threshold of 0.30 (the best hit is kept if all are weak). Selection takes one
  page per domain first, then at most 2 per domain.
- **Anti-fabrication gate.** Every `evidence_quote` is matched against the cited source's text.
  The fact is either verified, re-attributed to the provided source that really contains it
  (`citation_corrected`), or dropped (`citation_violation`, reason `ungrounded_quote`). Fuzzy
  matching tolerates elisions and dropped filler words, never a changed name or number. Facts
  that read like injected instructions are dropped.
- Firewall: Unicode tag-character smuggling is decoded, flagged and stripped. New rules catch
  `verdict_manipulation` and `citation_manipulation` (text aimed at our verifier).
- Report prose: images, links or URLs that don't point at stored sources, and raw HTML, are
  removed and disclosed. Conflicting claims and failed high-importance claims are always listed
  in Limitations by code.
- Prompts tell each agent about the checks it is subject to.
- `approval_requested` is persisted before the task becomes decidable, so a decision can never
  precede it in the timeline.

### Batch 4: evaluation
- New metric blocks per run:
  - `grounding`: verified / corrected / dropped, grounded rate, citation accuracy.
  - `report_quality`: citation-support rate (prose citations that point at sources supporting a
    verified claim), uncited numeric sentences, removed markup, disclosed conflicts, merged duplicates.
  - `cost`: tokens, calls, cost only when fully priced, per-agent tokens/cost/latency.
- Verification benchmark: 26 items (10 new, including number/location/date substitution, sources
  that disagree, and injection aimed at the verifier). It runs in batches of 12, so no item is
  silently skipped, and reports a **Brier score**.
- New offline **`guards`** suite (no model, no credentials, free) scores the safety gates.
  Run it from `POST /api/v1/evaluations/benchmarks/guards/run` or
  `python -m app.evaluation.benchmarks guards`.

## 3. Results (this environment)

**Test suite:** run `cd backend && python -m pytest`. It was 141 tests before Phase 4; every
existing test still passes. Three fixtures were adjusted because identical content at two URLs
is now correctly merged, and one demo template quote was made verbatim. The suite was
stress-run repeatedly to rule out flakiness.

**Offline guard benchmark** (`python -m app.evaluation.benchmarks guards`, 44 labelled cases,
dataset written before the run and not tuned afterwards):

| Gate | n | Accuracy | Precision | Recall | Misses |
|---|---|---|---|---|---|
| Quote grounding | 18 | 0.944 | **1.000** | 0.900 | q18: "per cent" vs "percent" → valid fact dropped |
| Injection detection | 20 | 0.900 | 0.857 | **1.000** | i14, i20: benign text flagged (false positives) |
| URL dedup | 6 | 1.000 | 1.000 | 1.000 | – |
| Overall | 44 | 0.932 | 0.923 | 0.960 | |

Errors only fail safe: no fabricated or altered quote was accepted and no injection was missed.
The cost is a dropped valid fact, or a benign source flagged as low-reliability.

### Live verification
Credential-free real-API checks were run against the actual endpoints (`pytest -m live`):

| Check | Result |
|---|---|
| Anthropic, invalid key | `auth_failed`, not retryable, key not echoed ✅ |
| Anthropic, 1 ms client timeout | `timeout`, retryable ✅ |
| Tavily, invalid key | `search_auth_failed`, not retryable ✅ |
| Model listing / default-model validation | skipped: no `ANTHROPIC_API_KEY` |
| Each of the five agents, run for real | skipped: no `ANTHROPIC_API_KEY` |
| Real Tavily search | skipped: no `TAVILY_API_KEY` |
| Live end-to-end run (all five agents) | skipped: no keys |
| Live verification benchmark | not run: needs keys |

To complete live verification, add the keys to `.env` and run:

```bash
cd backend
python -m pytest -m live -s                      # provider checks + full five-agent live run
python -m app.evaluation.benchmarks guards       # offline gates
# Verification benchmark (live, costs a few cents): POST /api/v1/evaluations/benchmarks/verification/run
```

The end-to-end live test asserts that all five agents completed in live mode, and prints each
agent's model, tokens, cost and duration plus the run's grounding, quality and cost metrics.

## 4. Security notes and remaining limitations
- **Injection detection is heuristic** (regex rules plus structural isolation). The structural
  defences are the real control: untrusted text only appears in the user channel inside escaped
  wrappers, a tool allowlist is enforced in code, and the fetcher only reads URLs returned by
  search. Measured false positives: "ignore … previous instructions" in ordinary prose, and
  `User:` labels.
- **Quote grounding** proves a quote occurs in the source, not that the *statement* follows from
  it. That is the Verification Agent's job. A fact can still pair a genuine quote with an
  overreaching statement, and the verifier is the backstop for that.
- **Cost enforcement** reserves using an input estimate of about 3.5 characters per token. Actual
  spend can exceed the cap by the estimation error of the in-flight calls. Billing for a refusal
  that is rescued by a server-side fallback isn't modelled exactly: cost uses the serving
  model's rate.
- Tavily is priced at $0.008 per credit with `basic` search = 1 credit. Check this against your plan.
- Claude Opus 5.5 defaults to `effort: medium` when an agent's effort is unset. Set effort per
  agent in Agent Configuration if you want deeper reasoning.
- The UI doesn't yet visualise the new metric blocks or the `guards` suite (the API returns
  them). That's intentionally out of scope for this phase.
- Carried over: DNS-rebinding window in the SSRF guard; LangGraph checkpoints in SQLite; BM25
  evidence retrieval (no embeddings).

## Live verification runbook

Paid tests are opt-in (`-m live`); a normal `pytest` run never selects them. Every live test builds
real providers explicitly (`mode="live"`, `AnthropicProvider`, `TavilySearch`). No code path
substitutes demo providers: a missing key is a **skip**, a bad key is a **failure**. Every paid
test has a hard spending ceiling that is enforced before each call.

### Provider configuration (`.env` in the repo root or `backend/`)

| Variable | Required for live? | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **yes** | – | Claude API key (secret; never logged or returned) |
| `ANTHROPIC_DEFAULT_MODEL` | **yes** | `claude-opus-5-5` in `.env.example`; none in code | Model for every agent unless overridden; validated against the live model list and must be in the pricing file when a cost cap is set |
| `TAVILY_API_KEY` | **yes** | – | Tavily search key (secret) |
| `DEFAULT_LLM_PROVIDER` | no | `anthropic` | Provider used by agents without an override |
| `ANTHROPIC_ENABLE_FALLBACKS` | no | `true` | Server-side refusal fallback (sent only to models that support it) |
| `AGENT_MODEL_PLANNER` / `_RESEARCH` / `_ANALYSIS` / `_VERIFICATION` / `_REPORT` | no | unset | Per-agent `provider:model`; Agent Configuration in the UI takes precedence |
| `LLM_REQUEST_TIMEOUT_SECONDS` | no | `180` | Client default timeout; each agent call uses its own `timeout_s` (default 240, editable per agent in the UI) |
| `SEARCH_CREDITS_PER_QUERY` | no | `1` | Tavily credits per `basic` search (used for cost) |
| `MAX_PARALLEL_SEARCHES` | no | `4` | Concurrent searches per task |
| `PRICING_FILE` | no | bundled `app/config/pricing.json` | USD prices; unpriced models report cost `null` |
| `DEFAULT_MAX_COST_USD` | no | unset | Per-task hard cost cap |
| `ADMIN_PASSWORD` | for the live e2e test | `admin` (dev) | The e2e test logs in through the API |

**Retries (not env-configurable):**
- LLM calls: per-agent `max_retries` (default 2, set in the UI) with full-jitter exponential
  backoff (≤ 30 s) that never waits less than a server `retry-after` (capped at 60 s). Only
  rate-limit, overload, 5xx, timeout and connection errors are retried.
- Bounded repair rounds: at most one schema repair and one truncation retry per call.
- Tavily searches: 3 attempts. Page fetches: 2 attempts.

Free readiness check (makes no API calls): `GET /api/v1/system/status` reports `live_mode_available: true`.

### Execution order (from `backend/`; stop at the first failure)

| # | What | Command | Paid calls | Hard cap |
|---|---|---|---|---|
| 0 | Real-endpoint error handling | `python -m pytest -m live -s -k "invalid_key or timeout"` | none (rejected requests) | free |
| 1 | Anthropic model listing | `python -m pytest -m live -s -k model_listing` | `models.list` only | free |
| 2 | Planner | `python -m pytest -m live -s -k agent_planner` | 1 LLM call | $0.40 |
| 3 | Tavily search | `python -m pytest -m live -s -k tavily_search` | 1 search | 1 credit |
| 4 | Research (Tavily + LLM) | `python -m pytest -m live -s -k agent_research` | 1 search, 1–2 LLM calls | $0.40 |
| 5 | Analysis | `python -m pytest -m live -s -k agent_analysis` | 1 LLM call | $0.40 |
| 6 | Verification | `python -m pytest -m live -s -k agent_verification` | 1 LLM call | $0.40 |
| 7 | Report generation | `python -m pytest -m live -s -k agent_report` | 1 LLM call | $0.40 |
| 8 | Five-agent end-to-end | `python -m pytest -m live -s -k end_to_end` | ≤ 15 LLM calls, ≤ 6 searches | $2.00 |
| 9 | Verification benchmark | `python -m pytest -m live -s -k verification_benchmark` | 3 batched calls (+ retries) | $1.50 |

Each step prints its model, tokens, cost, retries and latency. Agent steps print their parsed
output (plan, grounded facts, claims, verdicts, report). Step 8 prints every agent execution and
the run metrics. Step 9 prints accuracy, binary accuracy, Brier score and each mismatch.

### Estimated cost (bundled list prices; thinking tokens bill as output)

| Step | Opus 5.5 ($4/$20) | Sonnet 5.5 ($2/$10) | Notes |
|---|---|---|---|
| 0–1 | $0 | $0 | |
| 2, 5–7 (each) | typically $0.02–0.10; worst case $0.17–0.39 | about half | worst case = the agent's full output limit (8K planner; 12K research, analysis, verification; 16K report) |
| 3–4 | ≤ $0.40 + 1–2 credits | | Tavily $0.008/credit (check against your plan) |
| 8 | estimator upper bound ≈ $1.01, typically $0.60–1.20 | ≈ $0.53 | capped at $2.00 including search |
| 9 | typically $0.20–0.50 | about half | capped at $1.50 |
| **All** | **about $1.5–3 typical**; ≤ $5.50 + about 8 credits worst case | about half | |

Haiku 5.5 is priced ($0.10/$0.50, long-context tier above 100K prompt tokens) but has no
server-side refusal fallback. Its pricing does not include a cache-read rate, which is not used.

### Offline dry runs (done; no network)
Steps 2 and 4–9 were executed with fake providers and all sockets blocked, to make sure the
tests themselves are correct before any paid run. That dry run found and fixed a console-encoding
crash that would have failed the Report test *after* its paid call on Windows
(`tests/live/conftest.py`).

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
| Structured output for all 5 agent schemas | skipped: no `ANTHROPIC_API_KEY` |
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

"""Benchmarks.

* `verification` - claim-verification accuracy and calibration of the real (live) Verification
  Agent on a hand-labelled dataset of claims with synthetic evidence. Never runs in demo mode.
* `guards` - deterministic, offline, free: precision/recall of the code-level safety gates
  (evidence-quote grounding, prompt-injection detection, URL de-duplication) on a labelled set of
  adversarial and benign cases. Needs no credentials and involves no model.

    python -m app.evaluation.benchmarks guards
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.agents.runtime import SourceRegistry, TaskRuntime
from app.agents.verification import VerificationAgent
from app.config.settings import BACKEND_DIR, Settings
from app.llm.factory import ProviderSet
from app.schemas.agent_io import Claim
from app.security.content_firewall import sanitize
from app.services.agent_config_service import EffectiveAgentConfig
from app.services.budget import BudgetGuard, PricingTable, TaskLimits

DATASETS = {"verification": BACKEND_DIR / "evals" / "datasets" / "verification.jsonl",
            "guards": BACKEND_DIR / "evals" / "datasets" / "guards.jsonl"}
OFFLINE_SUITES = frozenset({"guards"})
VERIFY_BATCH = 12  # claims per Verification Agent call (the agent caps one call at 24)
LABELS = ["supported", "partially_supported", "unsupported", "conflicting", "unverifiable"]
SUPPORTISH = {"supported", "partially_supported"}


def load_dataset(suite: str) -> list[dict]:
    path: Path = DATASETS[suite]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score(items: list[dict], predictions: dict[str, dict]) -> dict[str, Any]:
    """Accuracy, binary (support vs not) accuracy, per-class precision/recall, confusion matrix."""
    confusion: dict[str, Counter] = {g: Counter() for g in LABELS}
    exact = binary = 0
    for it in items:
        gold = it["gold"]
        pred = predictions.get(it["id"], {}).get("verdict", "unverifiable")
        confusion[gold][pred] += 1
        exact += pred == gold
        binary += (pred in SUPPORTISH) == (gold in SUPPORTISH)
    per_class = {}
    for label in LABELS:
        tp = confusion[label][label]
        predicted = sum(confusion[g][label] for g in LABELS)
        actual = sum(confusion[label].values())
        if actual or predicted:
            per_class[label] = {"precision": round(tp / predicted, 3) if predicted else None,
                                "recall": round(tp / actual, 3) if actual else None, "support": actual}
    n = len(items)
    # Calibration: Brier score of confidence as P(claim supported) on the binary task
    # (0 = perfect, 0.25 = always saying 50%). Lower is better.
    brier_terms = []
    for it in items:
        pred = predictions.get(it["id"])
        if pred is None or pred.get("confidence") is None:
            continue
        p_true = float(pred["confidence"]) if pred.get("verdict") in SUPPORTISH else \
            1.0 - float(pred["confidence"])
        brier_terms.append((p_true - (1.0 if it["gold"] in SUPPORTISH else 0.0)) ** 2)
    return {"items": n, "accuracy": round(exact / n, 4) if n else None,
            "binary_accuracy": round(binary / n, 4) if n else None, "per_class": per_class,
            "brier_score": round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None,
            "confusion": {g: dict(c) for g, c in confusion.items() if sum(c.values())}}


async def run_verification_benchmark(settings: Settings, providers: ProviderSet,
                                     config: EffectiveAgentConfig, pricing: PricingTable) -> dict[str, Any]:
    if providers.mode != "live":
        raise ValueError("Benchmarks require live providers.")
    items = load_dataset("verification")
    limits = TaskLimits(max_total_tokens=400_000, max_llm_calls=12, max_tool_calls=10,
                        timeout_seconds=600, max_iterations=1)
    budget = BudgetGuard(limits, pricing, final_reserve_tokens=0, final_reserve_calls=0)
    sources = SourceRegistry()
    claims: list[Claim] = []
    id_map: dict[str, str] = {}
    for i, it in enumerate(items, start=1):
        refs = []
        for j, ev in enumerate(it["evidence"]):
            rec, _ = await sources.add(url=f"https://benchmark.invalid/{it['id']}/{j}",
                                       title=f"Benchmark evidence {it['id']}.{j}", snippet="",
                                       sanitized=sanitize(ev), search_query="", provider="benchmark",
                                       score=None, is_demo=False)
            refs.append(rec.ref)
        cid = f"c{i}"
        id_map[cid] = it["id"]
        claims.append(Claim(id=cid, statement=it["claim"], source_refs=refs, importance="high"))

    async def _noop(*_: Any) -> None:
        return None

    rt = TaskRuntime(task_id="benchmark", mode="live", objective="Benchmark claim verification.",
                     settings=settings, providers=providers, budget=budget,
                     agent_configs={"verification": config}, sources=sources, emit=_noop)
    # Batched so every item gets a verdict (one agent call verifies at most 24 claims).
    preds: dict[str, dict] = {}
    stats = None
    for i in range(0, len(claims), VERIFY_BATCH):
        agent = VerificationAgent(rt)
        out = await agent.run(claims=claims[i:i + VERIFY_BATCH], success_criteria=["Judge each claim."])
        preds.update({id_map[v.claim_id]: v.model_dump() for v in out.result.verdicts
                      if v.claim_id in id_map})
        if stats is None:
            stats = out.stats
        else:
            stats.usage = stats.usage + out.stats.usage
            stats.add_cost(out.stats.cost_usd)
    assert stats is not None
    metrics = score(items, preds)
    item_results = [{"id": it["id"], "gold": it["gold"],
                     "predicted": preds.get(it["id"], {}).get("verdict"),
                     "confidence": preds.get(it["id"], {}).get("confidence")} for it in items]
    return {"metrics": metrics, "item_results": item_results, "model": stats.model,
            "provider": stats.provider, "cost_usd": stats.cost_usd, "tokens": stats.usage.total}


# ----------------------------------------------------------------------------- guards (offline)
def _binary(rows: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for g, p in rows if g and p)
    fp = sum(1 for g, p in rows if not g and p)
    fn = sum(1 for g, p in rows if g and not p)
    tn = sum(1 for g, p in rows if not g and not p)
    n = len(rows)
    return {"items": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "accuracy": round((tp + tn) / n, 4) if n else None,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None}


def run_guard_benchmark() -> dict[str, Any]:
    """Score the deterministic gates against labelled cases. Positive class per check:
    quote -> "quote really occurs in the source"; injection -> "text is an injection attempt";
    url -> "the two URLs are the same resource"."""
    from app.security.content_firewall import sanitize
    from app.tools.quality import canonical_url, quote_found

    items = load_dataset("guards")
    by_kind: dict[str, list[tuple[bool, bool]]] = {}
    results = []
    for it in items:
        kind = it["kind"]
        if kind == "quote":
            pred = quote_found(it["quote"], it["source"])
            detail: Any = None
        elif kind == "injection":
            res = sanitize(it["text"])
            pred, detail = res.injection_suspected, list(res.matched_rules)
        elif kind == "url":
            pred, detail = canonical_url(it["a"]) == canonical_url(it["b"]), None
        else:  # pragma: no cover - dataset error
            raise ValueError(f"unknown guard kind {kind!r}")
        by_kind.setdefault(kind, []).append((bool(it["gold"]), bool(pred)))
        results.append({"id": it["id"], "kind": kind, "gold": it["gold"], "predicted": pred,
                        "correct": bool(it["gold"]) == bool(pred), "rules": detail})
    metrics = {k: _binary(v) for k, v in by_kind.items()}
    allrows = [r for v in by_kind.values() for r in v]
    metrics["overall"] = _binary(allrows)
    return {"metrics": metrics, "item_results": results, "model": None, "provider": "deterministic",
            "cost_usd": 0.0, "tokens": 0}


if __name__ == "__main__":  # pragma: no cover
    import sys

    suite = sys.argv[1] if len(sys.argv) > 1 else "guards"
    if suite != "guards":
        raise SystemExit("Only the offline 'guards' suite runs from the CLI; run 'verification' "
                         "from the API with live credentials.")
    out = run_guard_benchmark()
    for kind, m in out["metrics"].items():
        print(f"{kind:<10} n={m['items']:<3} acc={m['accuracy']} precision={m['precision']} "
              f"recall={m['recall']} (tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']})")
    misses = [r for r in out["item_results"] if not r["correct"]]
    for r in misses:
        print(f"  MISS {r['id']} ({r['kind']}): gold={r['gold']} predicted={r['predicted']} {r['rules'] or ''}")

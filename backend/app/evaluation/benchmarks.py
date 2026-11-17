"""Offline benchmark: claim-verification accuracy of the real Verification Agent.

Runs the configured (live) Verification Agent against a hand-labelled dataset of claims with
synthetic evidence passages and compares verdicts with gold labels. Never runs in demo mode.
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

DATASETS = {"verification": BACKEND_DIR / "evals" / "datasets" / "verification.jsonl"}
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
    return {"items": n, "accuracy": round(exact / n, 4) if n else None,
            "binary_accuracy": round(binary / n, 4) if n else None, "per_class": per_class,
            "confusion": {g: dict(c) for g, c in confusion.items() if sum(c.values())}}


async def run_verification_benchmark(settings: Settings, providers: ProviderSet,
                                     config: EffectiveAgentConfig, pricing: PricingTable) -> dict[str, Any]:
    if providers.mode != "live":
        raise ValueError("Benchmarks require live providers.")
    items = load_dataset("verification")
    limits = TaskLimits(max_total_tokens=400_000, max_llm_calls=6, max_tool_calls=10,
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
    out = await VerificationAgent(rt).run(claims=claims, success_criteria=["Judge each claim."])
    preds = {id_map[v.claim_id]: v.model_dump() for v in out.result.verdicts if v.claim_id in id_map}
    metrics = score(items, preds)
    item_results = [{"id": it["id"], "gold": it["gold"],
                     "predicted": preds.get(it["id"], {}).get("verdict"),
                     "confidence": preds.get(it["id"], {}).get("confidence")} for it in items]
    return {"metrics": metrics, "item_results": item_results, "model": out.stats.model,
            "provider": out.stats.provider, "cost_usd": out.stats.cost_usd,
            "tokens": out.stats.usage.total}

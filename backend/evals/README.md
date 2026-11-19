# Evaluation datasets

`datasets/verification.jsonl` is a hand-labelled benchmark for the **Verification Agent**.
Each item has a claim, one or more evidence passages and a gold verdict
(`supported`, `partially_supported`, `unsupported`, `conflicting`, `unverifiable`).

* All companies and evidence are **fictional and synthetic**. They test the agent's judgement
  against the evidence it is shown, not real-world knowledge.
* Item `v15` contains an embedded prompt-injection attempt; the correct verdict is `unsupported`.
* Run it from the Evaluation page or `POST /api/v1/evaluations/benchmarks/verification/run`.
  It needs live credentials and is never run in demo mode.

`datasets/guards.jsonl` is a labelled set for the **deterministic safety gates** (no model involved):
evidence-quote grounding (`quote`), prompt-injection detection (`injection`, attacks and benign
look-alikes) and URL de-duplication (`url`). It is free and needs no credentials:

    python -m app.evaluation.benchmarks guards      # or POST /api/v1/evaluations/benchmarks/guards/run

The verification suite runs in batches of 12 claims (so every item is judged) and reports a Brier
score for calibration in addition to accuracy, binary accuracy and per-class precision/recall.
Items v17-v26 add number/location/date substitution, disagreeing sources and verifier-directed
injection.

# Evaluation datasets

`datasets/verification.jsonl` is a hand-labelled benchmark for the **Verification Agent**.
Each item has a claim, one or more evidence passages and a gold verdict
(`supported`, `partially_supported`, `unsupported`, `conflicting`, `unverifiable`).

* All companies and evidence are **fictional and synthetic**. They test the agent's judgement
  against the evidence it is shown, not real-world knowledge.
* Item `v15` contains an embedded prompt-injection attempt; the correct verdict is `unsupported`.
* Run it from the Evaluation page or `POST /api/v1/evaluations/benchmarks/verification/run`.
  It needs live credentials and is never run in demo mode.

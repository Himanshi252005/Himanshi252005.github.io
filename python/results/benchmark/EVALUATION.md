# Evaluation results

Provider: **offline**. 18 synthetic incidents, 3 repetitions per mode. Python 3.12.14.

| Mode | Root-cause accuracy* | Accuracy incl. abstentions | Citation validity | Rule grounding | Mean calls | Median ms | Mean tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| multi | 100.0% | 100.0% | 100.0% | 100.0% | 6.56 | 16.73 | 0.0 |
| single | 73.3% | 77.8% | 100.0% | 100.0% | 5.33 | 79.02 | 0.0 |
| sequential-adaptive | 100.0% | 100.0% | 100.0% | 100.0% | 6.56 | 80.36 | 0.0 |

*Root-cause accuracy excludes intentionally inconclusive oracle cases. Overall accuracy includes correct abstentions.

## Method and limits

- All modes receive the same incident, source data, tools, retry policy, budgets and inference engine. Fault counters reset for every run.
- `single` is a sequential one-pass baseline with no critic or follow-up retrieval. It is intentionally a simple baseline.
- `sequential-adaptive` is the same agent team run sequentially, with the same feedback policy as `multi`. This ablation separates retrieval-policy gains from parallel execution gains; it is not a second single-agent baseline.
- `multi` runs independent collectors and their tool calls concurrently; reasoning waits for both collectors and critic requests drive subsequent rounds.
- Citation validity checks existence of referenced evidence. Rule grounding checks whether each citation matches the signed domain rule for that claim. Neither substitutes for independent human causal review.
- Citation metrics exclude runs with no cited claims (N/A). They are macro-averaged over nonempty runs.
- Offline tokens are exactly zero: no model is called. These results evaluate the deterministic workflow, not LLM quality or cost.
- Live mode records provider-reported input/output tokens. Failed or cancelled requests with unavailable usage are counted separately, not treated as measured zero cost.
- Fixture generation and domain rules were co-designed; this is a functional benchmark, not a held-out generalization study.
- Source latencies are simulated. Wall-clock latency includes retries and orchestration, varies by machine, and does not predict production performance.
- Full per-run results are in evaluation.csv and evaluation.json; first-repetition investigation traces are in reports/.

# Verification record

Verified locally on 23 October 2026 using the bundled Python runtime on Windows. No third-party packages were installed for this project.

## Automated tests

Command:

```sh
python -m unittest discover -s tests -v
```

Result: **38 tests passed**, including the parameterized sweep of all 18 incidents. The final recorded test run completed in 2.908 seconds.

Coverage includes parallel collection and dependency barriers, two-hop feedback, incomplete results, bounded timeout recovery, permanent failures, shared budgets, round/deadline limits, failed agent recovery, critic and reasoning failure, ambiguity, counter-evidence, incident-ID independence, genuine single-agent ownership, sequential ablation equivalence, citation grounding, offline token counts, untrusted log text, service/time filtering, duplicate evidence rejection, input validation, duplicate-signal scoring and history handling.

Mocked model tests cover strict structured payloads, measured usage, unknown citations, refusals, incomplete responses, invalid schemas, transport failure usage, call limits, no silent provider fallback, a successful end-to-end live-mode workflow, identical baseline reasoning prompts, undiscovered service rejection and missing credentials. **No actual model API call was made.**

## Evaluation

```sh
python -m orange_investigator evaluate --output results/benchmark
```

Eighteen incidents × three modes × three repetitions = **162 measured runs**. The full summary, per-run rows and first-repetition reports are included under `results/benchmark/`.

| Configuration | Accuracy including abstentions | Root-cause accuracy on resolvable cases | Mean tool attempts | Median latency |
|---|---:|---:|---:|---:|
| Multi-agent | 100.0% | 100.0% | 6.56 | 16.73 ms |
| Single-agent one-pass | 77.8% | 73.3% | 5.33 | 79.02 ms |
| Sequential adaptive team | 100.0% | 100.0% | 6.56 | 80.36 ms |

All modes used the offline diagnostic engine and consumed **zero model tokens**. Citation validity and signed-rule grounding were 100% among runs with cited claims. The figures are fixture-specific; the rules and synthetic data were co-designed. These are not live-LLM or held-out production results.

## Documentation and delivery

- The technical note is 397 whitespace-separated words, including its heading, below the 500-word limit.
- Architecture documents all six roles, shared state, task dependencies, feedback, limits and failure behavior.
- Evaluation labels remain separate from the data available to the engine.
- The transcript contains the user-visible messages available at packaging time, exported from this chat's local session record. It omits internal reasoning, system/developer instructions, environment metadata and raw tool payloads.
- The ZIP excludes Python caches and stale preliminary benchmark output. A SHA-256 manifest inside the project records the packaged source and deliverable files.
- Setup, examples and tests require only Python 3.11 or newer. Live mode additionally requires an API credential, compatible model access and internet access.

## Fresh archive smoke check

The delivery ZIP was extracted into a new directory. Running the documented investigation command from that copy successfully diagnosed INC-004 as a dependency outage in two rounds and wrote both JSON and Markdown reports. ZIP integrity and every packaged file's SHA-256 digest were verified.

# Orange Investigator

A runnable, evidence-first implementation of the **Multi-Agent Production Incident Investigation System** assignment. It investigates synthetic incidents using six collaborating agents, returns ranked hypotheses with supporting and contradicting evidence, and abstains when the evidence is insufficient.

**Python 3.11 or newer. No third-party runtime dependencies. No API key required for the offline demonstration.**

## Start here

Extract the ZIP and open a terminal in the `orange-investigator` folder. On Windows, use `py -3` in place of `python` if needed.

```sh
python -m orange_investigator list
python -m orange_investigator investigate --incident INC-004
python -m unittest discover -s tests -v
python -m orange_investigator evaluate --output results/my-benchmark
```

The investigation command prints its report and saves both Markdown and complete JSON under `results/latest/`. The default example reveals an inventory dependency failure despite a coincidental checkout deployment. No services, containers, database, frontend, or package installation are needed.

Try these other cases:

```sh
python -m orange_investigator investigate --incident INC-017
python -m orange_investigator investigate --incident INC-010
python -m orange_investigator investigate --incident INC-012
python -m orange_investigator investigate --incident INC-013 --mode single
```

These show a two-hop investigation, conflicting explanations, permanent telemetry failures, and the limitations of one-pass investigation, respectively.

## What is implemented

| Assignment requirement | Implementation |
|---|---|
| Six distinct agents | Orchestrator, Observability, Dependency, Reasoning, Critic, Reporting |
| Structured collaboration | Typed `AgentResult`, `Critique`, `Query`, `ToolResult`, shared evidence state, explicit task graph and trace |
| Genuine feedback | Critic requests missing sources, full result sets, and newly discovered dependencies; orchestration routes these to the responsible collectors |
| Five tools | Logs, metrics, deployment/config changes, dependencies, historical incidents |
| Reliability | Per-tool and per-agent retries, timeouts, global call and round budgets, deadline, no-progress termination, partial reports |
| Evidence-based reports | Ranked hypotheses, positive/negative citations, heuristic confidence, source observations, assumptions, proposed mitigation and verification |
| Inconclusive outcomes | Missing evidence, close competing causes, exhausted limits or failed reasoning/critic prevent a conclusive diagnosis |
| Evaluation | 18 incidents, 3 repetitions, single-agent baseline plus sequential-team ablation; accuracy, grounding, calls, latency, token usage |
| Submission materials | Source, dataset, tests, architecture, technical note, measured results and assistant transcript |

## Offline and live modes

The default **offline** engine is a transparent symbolic diagnostic system using general domain rules, not an LLM. It is suitable for reproducible workflow testing. It does not pretend to consume tokens or understand arbitrary logs. Rules act on normalized observation codes; unknown signals can lead to an inconclusive outcome. No rule branches on an incident ID, title or expected answer.

In **live** mode, the Reasoning and Critic agents use the OpenAI Responses API with strict structured outputs. Collection, orchestration, citation validation and report assembly remain deterministic. The same reasoning implementation is used by the single-agent baseline. Reports record the requested provider and model; a model failure produces an explicitly inconclusive result rather than silently switching to offline rules.

Choose an API model available to your account that supports Responses and Structured Outputs. Model selection is explicit; the program does not assume a particular account's model access.

PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
python -m orange_investigator investigate --incident INC-004 --provider openai --model YOUR_MODEL_ID --output results/live-demo
```

macOS/Linux:

```sh
export OPENAI_API_KEY="your-api-key"
python -m orange_investigator investigate --incident INC-004 --provider openai --model YOUR_MODEL_ID --output results/live-demo
```

To measure live model performance and actual provider-reported tokens:

```sh
python -m orange_investigator evaluate --provider openai --model YOUR_MODEL_ID --repeats 1 --output results/live-benchmark
```

This sends the selected synthetic incident and collected evidence to OpenAI and incurs API usage. The adapter sets `store: false`, keeps the key in the environment, and never includes it in reports. Offline commands make no network requests. Live response parsing, refusals, malformed results, citation validation, usage accounting and transport failures are tested with mocks; **live execution has not been verified in this delivery because no API key was available**. See the [official Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) for the API contract used by the adapter.

## Limits you can control

```sh
python -m orange_investigator investigate --incident INC-017 --max-rounds 2 --max-tool-calls 12 --deadline 10
```

Defaults: 3 investigation rounds, 40 total tool attempts, 2 attempts per tool, 2 attempts per agent, and a 120-second CLI investigation deadline. Live mode additionally permits at most 8 model calls and 2,400 generated tokens per call; change these with `--max-llm-calls` and `--max-output-tokens`. Retries count toward global budgets. There is no API input-token cap beyond the bounded synthetic fixture; inspect your input size before running live mode on larger custom datasets.

Tool sources are read-only simulations. Remediation is always a recommendation, never an executed change.

## Use your own incident

Create a JSON file containing one case or a list of cases with `incident`, `records`, optional `faults`, and optional `agent_faults`. Follow the examples in [data/incidents.json](data/incidents.json) and the [data contract](docs/DATA_CONTRACT.md). Each observation needs a stable ID, source, service, timezone-aware timestamp, code, value, text, and reliability in `[0, 1]`.

```sh
python -m orange_investigator investigate --dataset my-incident.json --incident MY-ID --output results/custom
```

Evaluation labels are separate in `data/labels.json`. Neither collectors nor reasoning agents receive them. Custom evaluation requires a matching `--labels` file. All fixtures can be regenerated with `python data/generate_dataset.py`.

## Deliverables

- [Architecture and coordination](docs/ARCHITECTURE.md), including a Mermaid diagram
- [Technical note](docs/TECHNICAL_NOTE.md), under 500 words
- [Evaluation results](results/benchmark/EVALUATION.md), with raw CSV/JSON; rerun evaluation to generate per-incident traces
- [Example investigation](results/demo/INC-017.md)
- [Data contract and fault simulation](docs/DATA_CONTRACT.md)
- [Verification record](docs/VERIFICATION.md)

## Limitations

This is an assignment implementation using synthetic data, not a production incident-response service. It has no live telemetry integration, persistent database, distributed queue, authentication layer or UI. The supported cause catalog is finite; confidence is heuristic and uncalibrated. Historical incidents are retrieved as context, but never treated as direct proof of the current incident. Empty successful queries mean “no matching records in this fixture,” not proven absence of a production condition.

The supplied benchmark and the diagnostic rules were co-designed. Its scores demonstrate the implemented workflow on these fixtures, not generalization to unseen production incidents. The sequential-team ablation controls for parallelism and makes the simple baseline's retrieval disadvantage explicit. The live adapter uses a standard-library network worker thread: cancellation ends the investigation task, but an in-flight request may continue until its socket timeout (at most 25 seconds), and process shutdown may wait for that worker. Such requests are marked as having unknown token usage when usage cannot be collected.

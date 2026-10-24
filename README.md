# Orange Incident Intelligence

An evidence-first investigation workspace by **Himanshi Rathore**.

**Live website: [Open Orange Incident Intelligence](https://himanshi252005.github.io/Multi-Agent-Production-Incident-Investigation-System/)**

Explore 18 synthetic production incidents through six specialist roles: Orchestrator, Observability, Dependency, Reasoning, Critic, and Reporting. The interface uses an ivory, forest green, and orange palette and adapts to phones and desktops.

## Features

- Parallel evidence collection, shared state, bounded retries, and critic-driven follow-ups.
- Live agent activity and ranked hypotheses linked to supporting and contradicting evidence.
- Searchable evidence, source filtering, and expandable provenance.
- Three approaches: multi-agent, single-agent baseline, and sequential adaptive.
- Custom incident JSON imports; Markdown and JSON report downloads.
- Recent investigations saved in the visitor's browser.

The public demo runs the **deterministic TypeScript evidence engine entirely in your browser**. Imported evidence is not uploaded. No live LLM or production telemetry is connected; confidence is heuristic, and actions are recommendations. Google Fonts is loaded for typography.

## Run locally

Use Node.js 22.13 or newer:

```sh
npm ci
npm run dev
```

## Verify and publish

```sh
npm test
npm run build
```

The build produces `docs/`, which is committed for GitHub Pages. Configure Pages to deploy the `main` branch's `/docs` folder. After changing source, rebuild and commit both source and `docs/`.

The engine suite compares all 18 incidents in three modes against the Python implementation (54 comparisons) and includes eight extra validation, retry, budget, and identity checks. These are synthetic benchmark results, not claims of real-world diagnostic accuracy.

## Original Python system

`python/` contains the original standard-library implementation, fixtures, benchmark summary, tests, architecture, data contract, and technical note. Its optional OpenAI adapter is separate from the browser demo and requires a server-side API key. Never put an API key in this frontend.

```sh
cd python
python -m unittest discover -s tests -v
```

## Layout

- `app/`: React interface and responsive styles.
- `lib/`: evidence engine, browser transport, domain rules, and fixtures.
- `tests/` and `scripts/`: engine parity checks.
- `public/`: favicon and example incident.
- `docs/`: deployable static website.
- `python/`: original multi-agent system.

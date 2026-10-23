"""Evaluator is the ONLY runtime module allowed to read oracle labels."""
from __future__ import annotations

import csv
import json
import platform
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .engine import Orchestrator
from .llm import ResponsesClient
from .models import Config, Incident
from .reasoning import RULES
from .report import save_report


def grounding(report: dict) -> tuple[float | None, float | None]:
    evidence = {e["id"]: e for e in report["facts"]}
    refs, valid, entailed = 0, 0, 0
    for h in report["hypotheses"]:
        for field, positive in (("supporting", True), ("contradicting", False)):
            for ref in h[field]:
                refs += 1
                if ref in evidence:
                    valid += 1
                    from .models import Evidence
                    e = Evidence(**evidence[ref])
                    entailed += any(r.cause == h["cause"] and (r.weight > 0) == positive and r.matches(e)
                                    for r in RULES)
    # No cited claim is N/A, never artificially counted as perfect grounding.
    return (valid / refs if refs else None, entailed / refs if refs else None)


async def evaluate(cases: list[dict], labels: dict, output: Path, config: Config,
                   provider="offline", model="", repeats=3) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    modes = ["multi", "single", "sequential-adaptive"]
    for repeat in range(repeats):
        # Rotate order to reduce consistently favoring one mode in timing runs.
        order = modes[repeat % len(modes):] + modes[:repeat % len(modes)]
        for case in cases:
            incident = Incident(**case["incident"])
            expected = labels[incident.id]["root_cause"]
            for mode in order:
                client = ResponsesClient(model, config) if provider == "openai" else None
                report = await Orchestrator(config, client).investigate(
                    incident, case["records"], case.get("faults"), mode, case.get("agent_faults"))
                validity, entailment = grounding(report)
                rows.append({"incident": incident.id, "repeat": repeat + 1, "mode": mode,
                             "expected": expected, "predicted": report["root_cause"],
                             "correct": report["root_cause"] == expected,
                             "status": report["status"], "termination": report["termination"],
                             "citation_validity": validity, "rule_grounding": entailment,
                             "tool_calls": len(report["tool_calls"]), "latency_ms": report["latency_ms"],
                             "tokens": report["tokens"]["total_tokens"],
                             "unknown_usage_calls": report["tokens"]["unknown_usage_calls"]})
                if repeat == 0:
                    save_report(report, output / "reports" / mode)
    summaries = {}
    for mode in modes:
        selected = [r for r in rows if r["mode"] == mode]
        causal = [r for r in selected if r["expected"] is not None]
        predicted = [r for r in selected if r["predicted"] is not None]
        citations = [r["citation_validity"] for r in selected if r["citation_validity"] is not None]
        grounded = [r["rule_grounding"] for r in selected if r["rule_grounding"] is not None]
        summaries[mode] = {"runs": len(selected),
                           "accuracy_including_abstentions": statistics.mean(r["correct"] for r in selected),
                           "root_cause_accuracy": statistics.mean(r["correct"] for r in causal) if causal else None,
                           "answer_coverage": len(predicted) / len(selected),
                           "precision_when_answered": statistics.mean(r["correct"] for r in predicted) if predicted else None,
                           "citation_validity": statistics.mean(citations) if citations else None,
                           "rule_grounding": statistics.mean(grounded) if grounded else None,
                           "mean_tool_calls": statistics.mean(r["tool_calls"] for r in selected),
                           "median_latency_ms": statistics.median(r["latency_ms"] for r in selected),
                           "mean_tokens": statistics.mean(r["tokens"] for r in selected),
                           "unknown_usage_calls": sum(r["unknown_usage_calls"] for r in selected)}
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "provider": provider,
              "model": model or None, "python": platform.python_version(), "platform": platform.platform(),
              "config": asdict(config), "incidents": len(cases), "repeats": repeats,
              "summary": summaries, "runs": rows}
    (output / "evaluation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (output / "evaluation.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    table = ["# Evaluation results", "", f"Provider: **{provider}**. {len(cases)} synthetic incidents, "
             f"{repeats} repetitions per mode. Python {platform.python_version()}.", "",
             "| Mode | Root-cause accuracy* | Accuracy incl. abstentions | Citation validity | Rule grounding | Mean calls | Median ms | Mean tokens |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for mode, s in summaries.items():
        percent = lambda value: f"{value:.1%}" if value is not None else "N/A"
        table.append(f"| {mode} | {percent(s['root_cause_accuracy'])} | {percent(s['accuracy_including_abstentions'])} | "
                     f"{percent(s['citation_validity'])} | {percent(s['rule_grounding'])} | "
                     f"{s['mean_tool_calls']:.2f} | {s['median_latency_ms']:.2f} | {s['mean_tokens']:.1f} |")
    table.extend(["", "*Root-cause accuracy excludes intentionally inconclusive oracle cases. Overall accuracy includes correct abstentions.", "",
                  "## Method and limits", "",
                  "- All modes receive the same incident, source data, tools, retry policy, budgets and inference engine. Fault counters reset for every run.",
                  "- `single` is a sequential one-pass baseline with no critic or follow-up retrieval. It is intentionally a simple baseline.",
                  "- `sequential-adaptive` is the same agent team run sequentially, with the same feedback policy as `multi`. This ablation separates retrieval-policy gains from parallel execution gains; it is not a second single-agent baseline.",
                  "- `multi` runs independent collectors and their tool calls concurrently; reasoning waits for both collectors and critic requests drive subsequent rounds.",
                  "- Citation validity checks existence of referenced evidence. Rule grounding checks whether each citation matches the signed domain rule for that claim. Neither substitutes for independent human causal review.",
                  "- Citation metrics exclude runs with no cited claims (N/A). They are macro-averaged over nonempty runs.",
                  "- Offline tokens are exactly zero: no model is called. These results evaluate the deterministic workflow, not LLM quality or cost.",
                  "- Live mode records provider-reported input/output tokens. Failed or cancelled requests with unavailable usage are counted separately, not treated as measured zero cost.",
                  "- Fixture generation and domain rules were co-designed; this is a functional benchmark, not a held-out generalization study.",
                  "- Source latencies are simulated. Wall-clock latency includes retries and orchestration, varies by machine, and does not predict production performance.",
                  "- Full per-run results are in evaluation.csv and evaluation.json; first-repetition investigation traces are in reports/.", ""])
    (output / "EVALUATION.md").write_text("\n".join(table), encoding="utf-8")
    return result

"""Portable Markdown output; raw JSON retains all structured messages and traces."""
from __future__ import annotations

import html
import json
from pathlib import Path


def clean(value) -> str:
    return html.escape(str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def markdown(report: dict) -> str:
    incident = report["incident"]
    lines = [f"# {clean(incident['id'])}: {clean(incident['title'])}", "",
             f"**Outcome:** {report['status']}  ",
             f"**Probable cause:** {report['root_cause'] or 'Not established'}  ",
             f"**Engine:** {report['mode']} / {report['provider']}  ",
             f"**Termination:** {report['termination']}  ",
             f"**Rounds:** {report['rounds']} | **Tool calls:** {len(report['tool_calls'])} | "
             f"**Latency:** {report['latency_ms']:.2f} ms | **Tokens:** {report['tokens']['total_tokens']}", "",
             report["confidence_note"], "", "## Ranked hypotheses", "",
             "| Hypothesis | Score | Confidence | Supports | Contradicts |",
             "|---|---:|---:|---|---|"]
    for h in report["hypotheses"]:
        lines.append(f"| {clean(h['cause'])} | {h['score']:.2f} | {h['confidence']:.0%} | "
                     f"{', '.join(h['supporting'])} | {', '.join(h['contradicting']) or '-'} |")
    if not report["hypotheses"]:
        lines.append("| No supported hypothesis | - | - | - | - |")
    for h in report["hypotheses"]:
        lines.extend(["", f"**{clean(h['cause'])}:** {clean(h['explanation'])}"])
    lines.extend(["", "## Source observations", "", report["fact_note"], "",
                  "| ID | Source / service | Timestamp | Observation | Reliability |",
                  "|---|---|---|---|---:|"])
    for e in report["facts"]:
        lines.append(f"| {clean(e['id'])} | {clean(e['source'])} / {clean(e['service'])} | {clean(e['at'])} | "
                     f"{clean(e['text'])} (`{clean(e['code'])}` = {clean(e['value'])}) | {e['reliability']:.0%} |")
    lines.extend(["", "## Assumptions", ""])
    lines.extend(f"- {clean(a)}" for a in report["assumptions"])
    if not report["assumptions"]:
        lines.append("No causal assumptions are promoted to facts.")
    lines.extend(["", "## Recommended actions", ""])
    for action in report["actions"]:
        lines.extend([f"- **{action['priority']}**: {clean(action['action'])}",
                      f"  Evidence: {', '.join(action['evidence_ids']) or 'Additional collection required'}. "
                      f"Verification: {clean(action['verification'])}"])
    lines.extend(["", "Actions are recommendations only; this program never changes production systems.",
                  "", "## Gaps and failures", ""])
    lines.extend(f"- {clean(item)}" for item in report["issues"])
    lines.extend(f"- Unresolved source: {clean(item)}" for item in report["unresolved_sources"])
    if not report["issues"] and not report["unresolved_sources"]:
        lines.append("No unresolved source errors at termination.")
    if report.get("critic_review"):
        lines.extend(["", "## Critic review", ""])
        lines.append("Accepted evidence: " + ("yes" if report["critic_review"]["accepted"] else "no"))
        lines.extend(f"- {clean(c)}" for c in report["critic_review"]["concerns"])
    lines.extend(["", "## Coordination trace", "",
                  "| Time (ms) | Agent | Event | Round |", "|---:|---|---|---:|"])
    for event in report["trace"]:
        lines.append(f"| {event['elapsed_ms']:.2f} | {event['agent']} | {event['event']} | {event['round']} |")
    lines.extend(["", "Full query inputs, retry attempts, messages, and task dependencies are in the paired JSON report.", ""])
    return "\n".join(lines)


def save_report(report: dict, directory: Path, name: str | None = None):
    directory.mkdir(parents=True, exist_ok=True)
    # Never use an arbitrary incident id as a path component.
    stem = name or "".join(c if c.isalnum() or c in "-_" else "_" for c in report["incident"]["id"])
    (directory / f"{stem}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (directory / f"{stem}.md").write_text(markdown(report), encoding="utf-8")

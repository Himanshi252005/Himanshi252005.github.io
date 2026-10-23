from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .engine import Orchestrator
from .evaluation import evaluate
from .llm import ResponsesClient
from .models import Config, Incident
from .report import markdown, save_report

ROOT = Path(__file__).resolve().parent.parent


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data if isinstance(data, list) else [data]
    if not cases:
        raise ValueError("Dataset must contain at least one incident")
    seen = set()
    for case in cases:
        incident = Incident(**case["incident"])
        if incident.id in seen:
            raise ValueError("Duplicate incident id")
        seen.add(incident.id)
        if not isinstance(case["records"], list):
            raise ValueError("records must be a list")
    return cases


def parser():
    cli = argparse.ArgumentParser(description="Evidence-first production incident investigator")
    cli.add_argument("command", choices=["list", "investigate", "evaluate"])
    cli.add_argument("--dataset", type=Path, default=ROOT / "data" / "incidents.json")
    cli.add_argument("--labels", type=Path, default=ROOT / "data" / "labels.json")
    cli.add_argument("--incident", default="INC-004")
    cli.add_argument("--mode", choices=["multi", "single", "sequential-adaptive"], default="multi")
    cli.add_argument("--provider", choices=["offline", "openai"], default="offline")
    cli.add_argument("--model", default="")
    cli.add_argument("--output", type=Path, default=ROOT / "results" / "latest")
    cli.add_argument("--repeats", type=int, default=3)
    cli.add_argument("--max-rounds", type=int, default=3)
    cli.add_argument("--max-tool-calls", type=int, default=40)
    cli.add_argument("--max-llm-calls", type=int, default=8)
    cli.add_argument("--max-output-tokens", type=int, default=2400)
    cli.add_argument("--deadline", type=float, default=120)
    return cli


async def main(args):
    cases = load_cases(args.dataset)
    if args.command == "list":
        for case in cases:
            print(f"{case['incident']['id']}  {case['incident']['title']}")
        return
    config = Config(max_rounds=args.max_rounds, max_tool_calls=args.max_tool_calls,
                    deadline_seconds=args.deadline, agent_timeout=30 if args.provider == "openai" else 15,
                    max_llm_calls=args.max_llm_calls, max_output_tokens=args.max_output_tokens)
    if args.command == "evaluate":
        if args.repeats < 1:
            raise ValueError("--repeats must be positive")
        labels = json.loads(args.labels.read_text(encoding="utf-8"))
        if any(case["incident"]["id"] not in labels for case in cases):
            raise ValueError("Missing evaluation labels")
        result = await evaluate(cases, labels, args.output, config, args.provider, args.model, args.repeats)
        print(json.dumps(result["summary"], indent=2))
        print(f"\nEvaluation saved to {args.output.resolve()}")
        return
    case = next((c for c in cases if c["incident"]["id"] == args.incident), None)
    if case is None:
        raise ValueError(f"Incident {args.incident!r} not found; use the list command")
    client = ResponsesClient(args.model, config) if args.provider == "openai" else None
    report = await Orchestrator(config, client).investigate(
        Incident(**case["incident"]), case["records"], case.get("faults"), args.mode, case.get("agent_faults"))
    save_report(report, args.output)
    print(markdown(report))
    print(f"Saved Markdown and JSON to {args.output.resolve()}")


if __name__ == "__main__":
    try:
        asyncio.run(main(parser().parse_args()))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

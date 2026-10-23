from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

TOOLS = ("logs", "metrics", "deployments", "dependencies", "history")


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return result


@dataclass(frozen=True)
class Incident:
    id: str
    title: str
    service: str
    start: str
    end: str
    description: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in asdict(self).values()):
            raise ValueError("Incident fields must be nonempty strings")
        if timestamp(self.start) > timestamp(self.end):
            raise ValueError("Incident start must not follow end")


@dataclass(frozen=True)
class Query:
    tool: str
    service: str
    start: str
    end: str
    limit: int = 3

    def __post_init__(self):
        if self.tool not in TOOLS or not self.service:
            raise ValueError("Unknown tool or empty service")
        if not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("Query limit must be between 1 and 100")
        if timestamp(self.start) > timestamp(self.end):
            raise ValueError("Invalid query window")

    @property
    def key(self) -> str:
        return f"{self.tool}:{self.service}"


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    service: str
    at: str
    code: str
    value: Any
    text: str
    reliability: float = 1.0

    def __post_init__(self):
        if self.source not in TOOLS or not self.id or not self.service or not self.code:
            raise ValueError("Invalid evidence identity or source")
        timestamp(self.at)
        if not math.isfinite(self.reliability) or not 0 <= self.reliability <= 1:
            raise ValueError("Evidence reliability must be in [0, 1]")


@dataclass
class ToolResult:
    query: Query
    evidence: list[Evidence] = field(default_factory=list)
    complete: bool = False
    error: str | None = None
    attempts: int = 0
    warning: str | None = None


@dataclass
class Hypothesis:
    cause: str
    score: float
    confidence: float
    supporting: list[str]
    contradicting: list[str]
    explanation: str
    assumptions: list[str] = field(default_factory=list)


@dataclass
class Critique:
    accepted: bool
    concerns: list[str]
    requests: list[Query]


@dataclass
class AgentResult:
    agent: str
    round: int
    evidence_ids: list[str] = field(default_factory=list)
    queries: list[Query] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    critique: Critique | None = None
    error: str | None = None


@dataclass
class Config:
    max_rounds: int = 3
    max_tool_calls: int = 40
    tool_attempts: int = 2
    tool_timeout: float = 0.2
    deadline_seconds: float = 30.0
    agent_attempts: int = 2
    agent_timeout: float = 15.0
    simulated_latency: float = 0.01
    max_llm_calls: int = 8
    max_output_tokens: int = 2400

    def __post_init__(self):
        for name, value in asdict(self).items():
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        for name in ("max_rounds", "max_tool_calls", "tool_attempts", "agent_attempts",
                     "max_llm_calls", "max_output_tokens"):
            if not isinstance(getattr(self, name), int):
                raise ValueError(f"{name} must be an integer")


@dataclass
class InvestigationState:
    incident: Incident
    mode: str
    evidence: dict[str, Evidence] = field(default_factory=dict)
    queries: dict[str, ToolResult] = field(default_factory=dict)
    messages: list[AgentResult] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    tasks: dict[str, dict] = field(default_factory=dict)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    round: int = 0
    termination: str = "not_started"
    started: float = field(default_factory=time.perf_counter)

    def emit(self, agent: str, event: str, **details):
        self.events.append({"sequence": len(self.events) + 1,
                            "elapsed_ms": round((time.perf_counter() - self.started) * 1000, 3),
                            "agent": agent, "event": event, "round": self.round, **details})

    def merge(self, result: ToolResult):
        # Merges happen synchronously on the event loop, never in worker threads.
        for item in result.evidence:
            if item.id in self.evidence and self.evidence[item.id] != item:
                raise ValueError(f"Conflicting immutable evidence id: {item.id}")
            self.evidence[item.id] = item
        self.queries[result.query.key] = result

    def snapshot(self) -> dict:
        return {"incident": asdict(self.incident),
                "evidence": [asdict(e) for e in self.evidence.values()],
                "hypotheses": [asdict(h) for h in self.hypotheses],
                "issues": list(self.issues)}

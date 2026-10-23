from __future__ import annotations

import asyncio
from dataclasses import asdict

from .llm import CRITIC_SCHEMA, REASON_SCHEMA, ResponsesClient
from .models import AgentResult, Critique, Hypothesis, InvestigationState, Query
from .reasoning import ACTIONS, rank, sufficient
from .tools import SimulatedToolbox


def services(state: InvestigationState) -> set[str]:
    known = {state.incident.service}
    # Expand transitively through observed edges only, never through hidden fixture data.
    changed = True
    while changed:
        changed = False
        for e in state.evidence.values():
            if e.source == "dependencies" and e.code == "depends_on" and e.service in known:
                if isinstance(e.value, str) and e.value not in known:
                    known.add(e.value)
                    changed = True
    return known


def query_for(state: InvestigationState, tool: str, service: str, limit: int = 3) -> Query:
    return Query(tool, service, state.incident.start, state.incident.end, limit)


class CollectingAgent:
    name = "collector"
    tools: tuple[str, ...] = ()

    async def run(self, state: InvestigationState, toolbox: SimulatedToolbox,
                  requests: list[Query] | None = None, parallel: bool = True) -> AgentResult:
        queries = ([q for q in requests if q.tool in self.tools] if requests is not None else
                   [query_for(state, t, state.incident.service) for t in self.tools])
        if parallel:
            results = await asyncio.gather(*(toolbox.query(q) for q in queries))
        else:
            results = [await toolbox.query(q) for q in queries]
        ids = []
        for result in results:
            state.merge(result)
            ids.extend(e.id for e in result.evidence)
            state.emit(self.name, "tool_result", query=asdict(result.query),
                       evidence_ids=[e.id for e in result.evidence],
                       complete=result.complete, error=result.error, attempts=result.attempts)
        return AgentResult(self.name, state.round, sorted(set(ids)), queries)


class ObservabilityAgent(CollectingAgent):
    name = "observability"
    tools = ("logs", "metrics")


class DependencyAgent(CollectingAgent):
    name = "dependency"
    tools = ("deployments", "dependencies", "history")


class ReasoningAgent:
    name = "reasoning"

    def __init__(self, client: ResponsesClient | None = None):
        self.client = client

    async def run(self, state: InvestigationState) -> AgentResult:
        if self.client is None:
            hypotheses = rank(list(state.evidence.values()))
        else:
            result = await self.client.structured("reasoning", state.snapshot(), REASON_SCHEMA)
            hypotheses, seen = [], set()
            for h in result["hypotheses"]:
                refs = set(h["supporting"] + h["contradicting"])
                if not refs <= state.evidence.keys() or not h["supporting"]:
                    raise ValueError("Model supplied unknown citations or unsupported hypothesis")
                if set(h["supporting"]) & set(h["contradicting"]) or h["cause"] in seen:
                    raise ValueError("Model supplied overlapping evidence or duplicate hypothesis")
                seen.add(h["cause"])
                h["supporting"] = sorted(set(h["supporting"]))
                h["contradicting"] = sorted(set(h["contradicting"]))
                families = {state.evidence[i].source for i in h["supporting"] if state.evidence[i].source != "history"}
                if len(families) < 2:
                    h["confidence"] = min(h["confidence"], 0.59)
                hypotheses.append(Hypothesis(score=round(h["confidence"] * 10, 3), **h))
            hypotheses.sort(key=lambda h: (-h.score, h.cause))
        state.hypotheses = hypotheses
        state.emit(self.name, "ranked_hypotheses", hypotheses=[asdict(h) for h in hypotheses])
        return AgentResult(self.name, state.round, hypotheses=hypotheses)


class SingleAgent:
    """One policy owns all five retrievals and one inference; no agent hand-offs."""
    name = "single"

    def __init__(self, client: ResponsesClient | None = None):
        self.client = client

    async def run(self, state: InvestigationState, toolbox: SimulatedToolbox) -> AgentResult:
        queries = [query_for(state, tool, state.incident.service)
                   for tool in ("logs", "metrics", "deployments", "dependencies", "history")]
        for query in queries:
            result = await toolbox.query(query)
            state.merge(result)
            state.emit(self.name, "tool_result", query=asdict(query), complete=result.complete,
                       evidence_ids=[e.id for e in result.evidence], error=result.error)
        # Reuse only the inference implementation so the baseline's model/rules,
        # prompt, validation and citation semantics are identical to the team.
        inference = ReasoningAgent(self.client)
        inference.name = self.name
        result = await inference.run(state)
        result.queries = queries
        result.evidence_ids = sorted(state.evidence)
        return result


class CriticAgent:
    name = "critic"

    def __init__(self, client: ResponsesClient | None = None):
        self.client = client

    async def run(self, state: InvestigationState) -> AgentResult:
        requests: dict[str, Query] = {}
        concerns = []
        known = services(state)
        for result in state.queries.values():
            if not result.complete:
                requests[result.query.key] = query_for(state, result.query.tool, result.query.service, 100)
                concerns.append(f"Incomplete evidence from {result.query.key}: {result.error or result.warning}")
        # A collector failure may leave no query entry. Reconstruct essential coverage.
        for tool in ("logs", "metrics", "deployments", "dependencies", "history"):
            q = query_for(state, tool, state.incident.service, 100)
            if q.key not in state.queries:
                requests[q.key] = q
                concerns.append(f"Missing primary source: {q.key}")
        for service in sorted(known - {state.incident.service}):
            for tool in ("logs", "metrics", "dependencies"):
                q = query_for(state, tool, service, 100)
                if q.key not in state.queries:
                    requests[q.key] = q
                    concerns.append(f"Dependency {service} needs {tool} verification")
        adequate = sufficient(state.hypotheses, state.evidence)
        if not adequate:
            concerns.append("Insufficient independent support or competing explanations remain too close")
        if state.hypotheses and state.hypotheses[0].contradicting:
            concerns.append("The leading hypothesis has counter-evidence; preserve it in the report")
        llm_accepted = True
        if self.client:
            snapshot = state.snapshot()
            snapshot["coverage"] = {key: {"complete": r.complete, "error": r.error}
                                    for key, r in state.queries.items()}
            result = await self.client.structured(self.name, snapshot, CRITIC_SCHEMA)
            llm_accepted = result["accepted"]
            concerns.extend(result["concerns"])
            for item in result["requests"]:
                if item["service"] not in known:
                    raise ValueError("Critic requested an undiscovered service")
                q = query_for(state, item["tool"], item["service"], 100)
                requests[q.key] = q
        critique = Critique(adequate and not requests and llm_accepted,
                            list(dict.fromkeys(concerns)), list(requests.values()))
        state.emit(self.name, "critique", **asdict(critique))
        return AgentResult(self.name, state.round, critique=critique)


class ReportingAgent:
    name = "reporting"

    def run(self, state: InvestigationState, toolbox: SimulatedToolbox,
            client: ResponsesClient | None) -> dict:
        eligible = sufficient(state.hypotheses, state.evidence)
        accepted = state.termination in ("evidence_sufficient", "single_pass_complete")
        confirmed = eligible and accepted
        best = state.hypotheses[0] if state.hypotheses else None
        unresolved = [key for key, r in state.queries.items() if not r.complete]
        missing = [f"{t}:{state.incident.service}" for t in ("logs", "metrics", "deployments", "dependencies", "history")
                   if f"{t}:{state.incident.service}" not in state.queries]
        actions = []
        if best:
            mitigation, verification = ACTIONS[best.cause]
            actions.append({"priority": "P1" if confirmed else "investigate",
                            "action": mitigation if confirmed else "Validate this tentative hypothesis first. " + mitigation,
                            "evidence_ids": best.supporting, "verification": verification,
                            "requires_human_approval": True})
        if not confirmed:
            actions.append({"priority": "investigate",
                            "action": "Collect missing observations and resolve contradictions before selecting a root cause.",
                            "evidence_ids": [], "verification": "Obtain independent, time-aligned evidence.",
                            "requires_human_approval": False})
        facts = [asdict(e) for e in sorted(state.evidence.values(), key=lambda e: (e.at, e.id))]
        state.emit(self.name, "report_created", status="probable" if confirmed else "inconclusive")
        return {"incident": asdict(state.incident), "mode": state.mode,
                "provider": "openai" if client else "offline_rules",
                "model": client.model if client else None,
                "status": "probable" if confirmed else "inconclusive",
                "root_cause": best.cause if confirmed else None,
                "confidence": best.confidence if confirmed else 0.0,
                "confidence_note": "Heuristic confidence, not a calibrated probability.",
                "termination": state.termination, "rounds": state.round,
                "hypotheses": [asdict(h) for h in state.hypotheses],
                "facts": facts, "fact_note": "Reported source observations; sources can be wrong or contradictory.",
                "assumptions": sorted({a for h in state.hypotheses for a in h.assumptions}),
                "actions": actions, "unresolved_sources": sorted(set(unresolved + missing)),
                "critic_review": next((asdict(m.critique) for m in reversed(state.messages) if m.critique), None),
                "issues": state.issues, "agent_messages": [asdict(m) for m in state.messages],
                "task_graph": state.tasks, "trace": state.events,
                "tool_calls": toolbox.calls,
                "tokens": client.usage() if client else {"calls": 0, "input_tokens": 0,
                    "output_tokens": 0, "total_tokens": 0, "unknown_usage_calls": 0}}

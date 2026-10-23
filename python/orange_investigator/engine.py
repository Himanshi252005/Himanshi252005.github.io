"""Shared-state task graph with parallel collection and critic-directed feedback."""
from __future__ import annotations

import asyncio
import copy
import time

from .agents import (CriticAgent, DependencyAgent, ObservabilityAgent,
                     ReasoningAgent, ReportingAgent, SingleAgent)
from .llm import ResponsesClient
from .models import AgentResult, Config, Incident, InvestigationState
from .tools import SimulatedToolbox


class Orchestrator:
    def __init__(self, config: Config | None = None, client: ResponsesClient | None = None):
        self.config = config or Config()
        self.client = client

    async def investigate(self, incident: Incident, records: list[dict],
                          faults: dict | None = None, mode: str = "multi",
                          agent_faults: dict | None = None) -> dict:
        if mode not in ("multi", "single", "sequential-adaptive"):
            raise ValueError("Mode must be multi, single, or sequential-adaptive")
        state = InvestigationState(incident, mode)
        toolbox = SimulatedToolbox(records, faults, self.config)
        scripted = copy.deepcopy(agent_faults or {})
        observability, dependency = ObservabilityAgent(), DependencyAgent()
        reasoning, critic = ReasoningAgent(self.client), CriticAgent(self.client)
        parallel = mode == "multi"

        async def run_agent(agent, factory, dependencies=()):
            key = f"r{state.round}:{agent.name}"
            for prerequisite in dependencies:
                if state.tasks.get(prerequisite, {}).get("status") not in ("done", "failed"):
                    raise RuntimeError(f"Unmet task dependency: {prerequisite}")
            state.tasks[key] = {"agent": agent.name, "depends_on": list(dependencies), "status": "running"}
            for attempt in range(1, self.config.agent_attempts + 1):
                state.emit(agent.name, "started", attempt=attempt)
                try:
                    fault_list = scripted.get(agent.name, [])
                    if fault_list and fault_list.pop(0) == "error":
                        raise RuntimeError("Simulated agent failure")
                    result = await asyncio.wait_for(factory(), self.config.agent_timeout)
                    state.messages.append(result)
                    state.tasks[key]["status"] = "done"
                    state.emit(agent.name, "completed", attempt=attempt)
                    return result
                except asyncio.CancelledError:
                    state.tasks[key]["status"] = "cancelled"
                    state.emit(agent.name, "cancelled")
                    raise
                except Exception as exc:
                    state.emit(agent.name, "failed", attempt=attempt, error=f"{type(exc).__name__}: {exc}")
                    if attempt < self.config.agent_attempts:
                        continue
                    error = f"{agent.name} failed after {attempt} attempts: {type(exc).__name__}: {exc}"
                    state.issues.append(error)
                    state.tasks[key]["status"] = "failed"
                    result = AgentResult(agent.name, state.round, error=error)
                    state.messages.append(result)
                    return result

        async def workflow():
            state.tasks["plan"] = {"agent": "orchestrator", "depends_on": [], "status": "done"}
            state.emit("orchestrator", "planned", mode=mode, parallel=parallel,
                       max_rounds=self.config.max_rounds, max_tool_calls=self.config.max_tool_calls)
            if mode == "single":
                state.round = 1
                baseline = SingleAgent(self.client)
                result = await run_agent(baseline, lambda: baseline.run(state, toolbox), ["plan"])
                state.termination = "reasoning_failed" if result.error else "single_pass_complete"
                return
            requests = None
            previous_fingerprint = None
            for round_number in range(1, self.config.max_rounds + 1):
                state.round = round_number
                deps = ["plan"] if round_number == 1 else [f"r{round_number - 1}:critic"]
                jobs = [(observability, lambda: observability.run(state, toolbox, requests, parallel)),
                        (dependency, lambda: dependency.run(state, toolbox, requests, parallel))]
                if parallel:
                    await asyncio.gather(*(run_agent(a, f, deps) for a, f in jobs))
                else:
                    for agent, factory in jobs:
                        await run_agent(agent, factory, deps)
                result = await run_agent(reasoning, lambda: reasoning.run(state),
                                         [f"r{round_number}:observability", f"r{round_number}:dependency"])
                if result.error:
                    # Do not silently replace failed live reasoning with offline results.
                    state.termination = "reasoning_failed"
                    return
                result = await run_agent(critic, lambda: critic.run(state), [f"r{round_number}:reasoning"])
                if result.error:
                    state.termination = "critic_failed"
                    return
                critique = result.critique
                if critique.accepted:
                    state.termination = "evidence_sufficient"
                    return
                if not critique.requests:
                    state.termination = "insufficient_evidence"
                    return
                if len(toolbox.calls) >= self.config.max_tool_calls:
                    state.termination = "tool_budget_exhausted"
                    return
                fingerprint = (tuple(sorted(state.evidence)), tuple(sorted(q.key for q in critique.requests)),
                               tuple(sorted((k, r.complete, r.error) for k, r in state.queries.items())))
                if fingerprint == previous_fingerprint:
                    state.termination = "no_progress"
                    return
                previous_fingerprint = fingerprint
                requests = critique.requests
                state.emit("orchestrator", "feedback_dispatched", requests=[q.key for q in requests],
                           concerns=critique.concerns)
            state.termination = "round_limit"

        try:
            async with asyncio.timeout(self.config.deadline_seconds):
                await workflow()
        except TimeoutError:
            state.termination = "deadline_exceeded"
            state.issues.append("Investigation deadline exceeded; unfinished work was cancelled")
        state.tasks["report"] = {"agent": "reporting", "depends_on": list(state.tasks), "status": "done"}
        report = ReportingAgent().run(state, toolbox, self.client)
        report["latency_ms"] = round((time.perf_counter() - state.started) * 1000, 3)
        return report

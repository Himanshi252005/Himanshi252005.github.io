"""Typed, read-only simulated tools with reproducible faults and a shared budget."""
from __future__ import annotations

import asyncio
import copy
import time
from collections import defaultdict

from .models import Config, Evidence, Query, ToolResult, timestamp


class SimulatedToolbox:
    def __init__(self, records: list[dict], faults: dict | None, config: Config):
        self.records = [Evidence(**r) for r in records]
        if len({e.id for e in self.records}) != len(self.records):
            raise ValueError("Duplicate evidence ids in fixture")
        self.faults = copy.deepcopy(faults or {})
        self.config = config
        self.calls: list[dict] = []
        self._counts: dict[str, int] = defaultdict(int)

    async def _execute(self, query: Query, outcome: str) -> ToolResult:
        if outcome == "timeout":
            await asyncio.sleep(self.config.tool_timeout * 2)
        await asyncio.sleep(self.config.simulated_latency)
        if outcome == "error":
            raise ConnectionError("Simulated source unavailable")
        if outcome not in ("ok", "partial", "timeout", "error"):
            raise ValueError("Invalid simulated fault type")
        records = [e for e in self.records if e.source == query.tool and e.service == query.service
                   and timestamp(query.start) <= timestamp(e.at) <= timestamp(query.end)]
        records.sort(key=lambda e: (timestamp(e.at), e.id))
        limit = min(query.limit, 1) if outcome == "partial" else query.limit
        selected = records[:limit]
        complete = outcome != "partial" and len(selected) == len(records)
        return ToolResult(query, selected, complete,
                          warning=None if complete else "Source returned incomplete or truncated results")

    async def query(self, query: Query) -> ToolResult:
        for attempt in range(1, self.config.tool_attempts + 1):
            # Reserve before any await: independent tasks cannot overspend this budget.
            if len(self.calls) >= self.config.max_tool_calls:
                return ToolResult(query, error="tool_budget_exhausted", attempts=attempt - 1)
            number = self._counts[query.key]
            self._counts[query.key] += 1
            script = self.faults.get(query.key, self.faults.get(query.tool, []))
            outcome = script[min(number, len(script) - 1)] if script else "ok"
            call = {"tool": query.tool, "service": query.service, "limit": query.limit,
                    "attempt": attempt, "status": "running", "started": time.perf_counter()}
            self.calls.append(call)
            try:
                result = await asyncio.wait_for(self._execute(query, outcome), self.config.tool_timeout)
                result.attempts = attempt
                call.update(status="ok" if result.complete else "partial", count=len(result.evidence))
                return result
            except (TimeoutError, ConnectionError, ValueError) as exc:
                error = "timeout" if isinstance(exc, TimeoutError) else str(exc)
                call.update(status="error", error=error)
                if attempt == self.config.tool_attempts:
                    return ToolResult(query, error=error, attempts=attempt)
                await asyncio.sleep(min(0.005 * 2 ** (attempt - 1), 0.1))
            except asyncio.CancelledError:
                call.update(status="cancelled")
                raise
            finally:
                call["elapsed_ms"] = round((time.perf_counter() - call.pop("started")) * 1000, 3)
        raise AssertionError("Unreachable")

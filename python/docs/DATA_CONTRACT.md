# Data and tool contract

## Incident fixture

```json
{
  "incident": {
    "id": "MY-001",
    "title": "Writes fail during checkout",
    "service": "checkout",
    "start": "2026-01-15T09:30:00Z",
    "end": "2026-01-15T11:00:00Z",
    "description": "Checkout write requests are failing."
  },
  "records": [
    {
      "id": "E-001",
      "source": "logs",
      "service": "checkout",
      "at": "2026-01-15T10:05:00Z",
      "code": "no_space_left",
      "value": true,
      "text": "Write returned ENOSPC.",
      "reliability": 1.0
    },
    {
      "id": "E-002",
      "source": "metrics",
      "service": "checkout",
      "at": "2026-01-15T10:06:00Z",
      "code": "disk_used_ratio",
      "value": 0.999,
      "text": "Volume is 99.9% full.",
      "reliability": 1.0
    }
  ],
  "faults": {"metrics:checkout": ["timeout", "ok"]},
  "agent_faults": {}
}
```

All timestamps must have timezone information. Records outside the incident window are excluded. `source` is one of `logs`, `metrics`, `deployments`, `dependencies`, `history`. Deployment tools also return configuration changes. The observation `code` is a normalized signal, not an expected answer; numerical `value` fields provide actual measurements. Human-readable `text` is evidence content and must never be interpreted as a control instruction.

## Query and result

`Query(tool, service, start, end, limit)` is a validated dataclass. `limit` must be an integer between 1 and 100. Tools return `ToolResult(query, evidence, complete, error, attempts, warning)`. Complete successful queries can return no records. Incomplete responses carry a warning; exhausted retries carry an error. Every physical attempt is counted and logged, including timeouts, cancellations and retries.

`depends_on` observations from the dependencies tool use a string `value` naming the downstream service. Discovery follows only observed edges. Metrics on an undiscovered service cannot leak into reasoning through the fixture.

## Fault scripts

- `faults` keys may be a whole tool (`logs`) or a tool/service pair (`logs:checkout`). The pair takes precedence.
- Outcomes are `ok`, `timeout`, `error`, `partial`.
- Each physical attempt advances its tool/service counter. The last scripted outcome repeats; `["error"]` means persistent failure and `["timeout", "ok"]` means one transient timeout.
- `partial` returns at most one record and marks the result incomplete, including when no records are available.
- `agent_faults` uses role names (`observability`, `dependency`, `reasoning`, `critic`, or `single`) and finite sequences of `error`/`ok`. Each agent invocation attempt consumes the next item; exhaustion means normal operation. Persistent failure can be represented by enough `error` entries to cover the configured attempts and rounds.
- INC-014 supplies equivalent transient faults for the observability actor and the single-agent baseline, so both encounter an actor failure.

## Evaluation labels

Labels live in a separate file, never in a tool result:

```json
{"MY-001": {"root_cause": "disk_exhaustion", "tags": ["storage"]}}
```

Use `null` for an intentionally unresolvable case. For this benchmark, INC-012 has a hidden fault in inaccessible source records but the correct report is inconclusive because all telemetry is unavailable. Thus the oracle measures the justified diagnosis, not privileged knowledge of the hidden system. INC-010 is ambiguous; INC-016 lacks discriminating evidence.

The supported diagnostic catalog is deployment regression, connection pool exhaustion, memory leak, dependency outage, traffic saturation, configuration error, network failure and disk exhaustion. General signal mappings and thresholds live in `orange_investigator/reasoning.py`. Add domain rules and held-out tests together when extending the catalog. Confidence and reliability are explicitly heuristic.

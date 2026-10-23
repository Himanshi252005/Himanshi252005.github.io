"""General domain rules, shared by the offline multi- and single-agent engines.

Rules never inspect incident ids, titles, fault scripts, or evaluation labels.
Scores are heuristic support scores, NOT calibrated probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Evidence, Hypothesis


@dataclass(frozen=True)
class Rule:
    cause: str
    code: str
    operator: str
    threshold: Any
    weight: float

    def matches(self, e: Evidence) -> bool:
        if e.code != self.code:
            return False
        if self.operator == "eq":
            return e.value == self.threshold
        if not isinstance(e.value, (int, float)) or isinstance(e.value, bool):
            return False
        return e.value >= self.threshold if self.operator == "ge" else e.value <= self.threshold


RULES = [
    Rule("deployment_regression", "exception_after_release", "eq", True, 3),
    Rule("deployment_regression", "new_version_error_ratio", "ge", 0.15, 4),
    Rule("deployment_regression", "rollback_recovered", "eq", True, 5),
    Rule("deployment_regression", "release_changed", "eq", True, 1),
    Rule("deployment_regression", "errors_precede_deploy", "eq", True, -5),
    Rule("deployment_regression", "old_version_same_errors", "eq", True, -4),
    Rule("connection_pool_exhaustion", "pool_timeout", "eq", True, 3),
    Rule("connection_pool_exhaustion", "db_connections_ratio", "ge", 0.9, 4),
    Rule("connection_pool_exhaustion", "pool_limit_reduced", "eq", True, 2),
    Rule("connection_pool_exhaustion", "pool_resize_recovered", "eq", True, 4),
    Rule("connection_pool_exhaustion", "db_connections_ratio", "le", 0.5, -4),
    Rule("memory_leak", "oom_killed", "eq", True, 3),
    Rule("memory_leak", "memory_growth_mb_min", "ge", 10, 4),
    Rule("memory_leak", "retained_heap_growth", "eq", True, 3),
    Rule("memory_leak", "memory_growth_mb_min", "le", 0, -3),
    Rule("dependency_outage", "upstream_5xx", "eq", True, 2),
    Rule("dependency_outage", "availability_ratio", "le", 0.9, 4),
    Rule("dependency_outage", "upstream_connection_refused", "eq", True, 3),
    Rule("dependency_outage", "availability_ratio", "ge", 0.999, -3),
    Rule("traffic_saturation", "request_rate_vs_baseline", "ge", 3, 3),
    Rule("traffic_saturation", "cpu_utilization", "ge", 0.9, 3),
    Rule("traffic_saturation", "queue_depth", "ge", 500, 2),
    Rule("traffic_saturation", "request_rate_vs_baseline", "le", 1.2, -3),
    Rule("config_error", "invalid_config", "eq", True, 4),
    Rule("config_error", "config_changed", "eq", True, 1),
    Rule("config_error", "config_revert_recovered", "eq", True, 5),
    Rule("config_error", "config_validated", "eq", True, -3),
    Rule("network_failure", "dns_failure", "eq", True, 4),
    Rule("network_failure", "packet_loss_ratio", "ge", 0.1, 3),
    Rule("network_failure", "dns_failure_ratio", "ge", 0.1, 3),
    Rule("network_failure", "network_probe_success", "eq", True, -3),
    Rule("disk_exhaustion", "no_space_left", "eq", True, 4),
    Rule("disk_exhaustion", "disk_used_ratio", "ge", 0.95, 4),
    Rule("disk_exhaustion", "disk_used_ratio", "le", 0.7, -4),
]
CAUSES = tuple(sorted({r.cause for r in RULES}))

ACTIONS = {
    "deployment_regression": ("Validate the failing version with a canary; prepare a rollback to the last healthy release.", "Compare error rate by version and verify recovery before restoring traffic."),
    "connection_pool_exhaustion": ("Inspect active and idle connections and pool limits; drain stuck consumers before a controlled pool adjustment.", "Verify database headroom and falling connection wait time; do not raise limits blindly."),
    "memory_leak": ("Capture a heap profile and compare retained allocations; recycle affected instances gradually if needed.", "Verify stable memory after warm-up and no further OOM events."),
    "dependency_outage": ("Confirm the downstream service health; consider a circuit breaker or tested fallback to reduce cascading failures.", "Verify dependency availability and end-to-end success before removing mitigations."),
    "traffic_saturation": ("Check capacity and demand; apply tested rate limits or scale consumers within downstream capacity.", "Verify queue depth, CPU and tail latency return to baseline."),
    "config_error": ("Validate the changed configuration against the last known working version; prepare a scoped revert.", "Verify validation passes and affected requests recover after the change."),
    "network_failure": ("Check DNS resolution and packet loss from affected hosts; engage the network owner with probe evidence.", "Verify resolution, connectivity and end-to-end latency from multiple affected hosts."),
    "disk_exhaustion": ("Inspect disk and inode usage; rotate expendable logs or expand the volume using an approved runbook.", "Verify free space and successful writes; preserve evidence before cleanup."),
}


def rank(evidence: list[Evidence]) -> list[Hypothesis]:
    hypotheses = []
    for cause in CAUSES:
        # Repeated log lines do not create artificial confidence. One strongest
        # observation per service/code/polarity contributes to the score.
        votes: dict[tuple, tuple[float, Evidence]] = {}
        for e in evidence:
            if e.source == "history":
                continue  # Prior incidents inform investigation, never prove this one.
            for rule in RULES:
                if rule.cause == cause and rule.matches(e):
                    weight = rule.weight * e.reliability
                    key = (e.service, e.code, weight > 0)
                    if key not in votes or abs(weight) > abs(votes[key][0]):
                        votes[key] = (weight, e)
        positives = [(w, e) for w, e in votes.values() if w > 0]
        negatives = [(w, e) for w, e in votes.values() if w < 0]
        if not positives:
            continue
        score = round(sum(w for w, _ in votes.values()), 4)
        families = {e.source for _, e in positives}
        confidence = max(0.0, min(0.97, 0.30 + 0.065 * score))
        if len(families) < 2:
            confidence = min(confidence, 0.59)
        hypotheses.append(Hypothesis(
            cause, score, round(confidence, 3),
            sorted(e.id for _, e in positives), sorted(e.id for _, e in negatives),
            f"{len(positives)} supporting signals across {len(families)} source families; "
            f"{len(negatives)} counter-signals. Net weighted support {score:g}.",
            ["Temporal correlation may not establish causation; validate the proposed mitigation."]))
    return sorted(hypotheses, key=lambda h: (-h.score, h.cause))


def sufficient(hypotheses: list[Hypothesis], evidence: dict[str, Evidence]) -> bool:
    if not hypotheses:
        return False
    first = hypotheses[0]
    families = {evidence[i].source for i in first.supporting if i in evidence}
    return (first.score >= 5 and first.confidence >= 0.65 and len(families) >= 2
            and (len(hypotheses) == 1 or first.score - hypotheses[1].score >= 2))

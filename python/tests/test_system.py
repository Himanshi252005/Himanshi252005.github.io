import asyncio
import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from orange_investigator.agents import ReasoningAgent
from orange_investigator.engine import Orchestrator
from orange_investigator.evaluation import grounding
from orange_investigator.llm import REASON_SCHEMA, ResponsesClient
from orange_investigator.models import Config, Evidence, Incident, InvestigationState, Query
from orange_investigator.reasoning import rank
from orange_investigator.report import markdown, save_report
from orange_investigator.tools import SimulatedToolbox

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "data/incidents.json").read_text(encoding="utf-8"))
LABELS = json.loads((ROOT / "data/labels.json").read_text(encoding="utf-8"))


def config(**kwargs):
    # Leave headroom for Windows timer granularity and asyncio debug overhead.
    return Config(**{"simulated_latency": .001, "tool_timeout": .5, **kwargs})


async def run_case(index, mode="multi", cfg=None, **overrides):
    case = copy.deepcopy(CASES[index - 1])
    case.update(overrides)
    return await Orchestrator(cfg or config()).investigate(
        Incident(**case["incident"]), case["records"], case["faults"], mode, case["agent_faults"])


class InvestigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_synthetic_cases_match_separate_oracle(self):
        for number, case in enumerate(CASES, 1):
            with self.subTest(incident=case["incident"]["id"]):
                report = await run_case(number)
                self.assertEqual(report["root_cause"], LABELS[case["incident"]["id"]]["root_cause"])
                self.assertLessEqual(len(report["tool_calls"]), 40)
                self.assertLessEqual(report["rounds"], 3)

    async def test_parallel_collectors_and_barrier(self):
        report = await run_case(1)
        events = report["trace"]
        position = lambda agent, event: next(i for i, e in enumerate(events) if e["agent"] == agent and e["event"] == event)
        self.assertLess(position("dependency", "started"), position("observability", "completed"))
        for agent in ("dependency", "observability"):
            self.assertLess(position(agent, "completed"), position("reasoning", "started"))
        self.assertEqual(report["task_graph"]["r1:reasoning"]["depends_on"], ["r1:observability", "r1:dependency"])

    async def test_critic_follows_two_dependency_hops(self):
        report = await run_case(17)
        self.assertEqual(report["rounds"], 3)
        self.assertEqual(report["root_cause"], "dependency_outage")
        self.assertIn("warehouse", {c["service"] for c in report["tool_calls"]})
        self.assertEqual(sum(e["event"] == "feedback_dispatched" for e in report["trace"]), 2)

    async def test_partial_and_truncated_sources_are_requeried(self):
        for number in (9, 13):
            report = await run_case(number)
            self.assertGreater(report["rounds"], 1)
            self.assertTrue(any(c["limit"] == 100 for c in report["tool_calls"]))
            self.assertFalse(report["unresolved_sources"])

    async def test_transient_timeout_recovery(self):
        report = await run_case(11)
        calls = [c for c in report["tool_calls"] if c["tool"] == "metrics"]
        self.assertEqual([c["status"] for c in calls], ["error", "ok"])
        self.assertEqual(calls[0]["error"], "timeout")
        self.assertEqual(report["root_cause"], "network_failure")

    async def test_permanent_source_failures_terminate(self):
        report = await run_case(12)
        self.assertEqual(report["termination"], "no_progress")
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["rounds"], 2)
        self.assertEqual(len(report["tool_calls"]), 20)

    async def test_tool_budget_cannot_be_overspent_by_parallel_tasks(self):
        report = await run_case(4, cfg=config(max_tool_calls=3))
        self.assertEqual(len(report["tool_calls"]), 3)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["termination"], "tool_budget_exhausted")

    async def test_round_budget_prevents_unbounded_dependency_walk(self):
        report = await run_case(17, cfg=config(max_rounds=1))
        self.assertEqual(report["termination"], "round_limit")
        self.assertEqual(report["status"], "inconclusive")

    async def test_deadline_cancels_outstanding_work(self):
        report = await run_case(1, cfg=config(deadline_seconds=.001, simulated_latency=.02))
        self.assertEqual(report["termination"], "deadline_exceeded")
        self.assertEqual(report["status"], "inconclusive")
        self.assertTrue(all(c["status"] == "cancelled" for c in report["tool_calls"]))

    async def test_agent_failure_retries_and_recovers(self):
        report = await run_case(14)
        self.assertTrue(any(e["agent"] == "observability" and e["event"] == "failed" for e in report["trace"]))
        self.assertEqual(report["status"], "probable")

    async def test_permanent_reasoning_failure_is_inconclusive(self):
        report = await run_case(1, agent_faults={"reasoning": ["error", "error"]})
        self.assertEqual(report["termination"], "reasoning_failed")
        self.assertIsNone(report["root_cause"])

    async def test_permanent_critic_failure_is_inconclusive(self):
        report = await run_case(1, agent_faults={"critic": ["error", "error"]})
        self.assertEqual(report["termination"], "critic_failed")
        self.assertIsNone(report["root_cause"])

    async def test_failed_collector_can_be_recovered_by_feedback(self):
        report = await run_case(1, agent_faults={"observability": ["error", "error", "ok"]})
        self.assertEqual(report["root_cause"], "deployment_regression")
        self.assertEqual(report["rounds"], 2)
        self.assertTrue(report["issues"])

    async def test_competing_hypotheses_allow_abstention(self):
        report = await run_case(10)
        self.assertEqual(report["status"], "inconclusive")
        self.assertGreaterEqual(len(report["hypotheses"]), 2)

    async def test_counter_evidence_is_preserved(self):
        report = await run_case(18)
        self.assertTrue(report["hypotheses"][0]["contradicting"])
        self.assertIn("Contradicts", markdown(report))

    async def test_identity_does_not_determine_answer(self):
        case = copy.deepcopy(CASES[0])
        case["incident"].update(id="RANDOM-UNSEEN-987", title="Unrelated title")
        for e in case["records"]:
            e["id"] = e["id"].replace("INC-001", "RANDOM")
        report = await run_case(1, incident=case["incident"], records=case["records"])
        self.assertEqual(report["root_cause"], "deployment_regression")

    async def test_single_agent_has_one_owner_and_no_feedback(self):
        report = await run_case(4, mode="single")
        self.assertEqual({m["agent"] for m in report["agent_messages"]}, {"single"})
        self.assertEqual(len(report["tool_calls"]), 5)
        self.assertEqual(report["rounds"], 1)
        self.assertNotIn("critic", {e["agent"] for e in report["trace"]})

    async def test_sequential_ablation_preserves_adaptive_answer(self):
        multi = await run_case(17)
        serial = await run_case(17, mode="sequential-adaptive")
        self.assertEqual(multi["root_cause"], serial["root_cause"])
        self.assertEqual(len(multi["tool_calls"]), len(serial["tool_calls"]))

    async def test_grounding_and_offline_usage(self):
        report = await run_case(1)
        self.assertEqual(grounding(report), (1, 1))
        self.assertEqual(report["tokens"]["total_tokens"], 0)
        empty = await run_case(16)
        self.assertEqual(grounding(empty), (None, None))

    async def test_unknown_and_injected_log_content_is_not_an_instruction(self):
        report = await run_case(15)
        self.assertEqual(report["root_cause"], "disk_exhaustion")
        unknown = await run_case(16)
        self.assertIsNone(unknown["root_cause"])


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_filtering_by_source_service_and_time(self):
        case = CASES[3]
        toolbox = SimulatedToolbox(case["records"], {}, config())
        query = Query("logs", "inventory", "2026-01-15T10:00:00Z", "2026-01-15T11:00:00Z")
        result = await toolbox.query(query)
        self.assertEqual(len(result.evidence), 1)
        self.assertTrue(all(e.service == "inventory" and e.source == "logs" for e in result.evidence))
        early = Query("logs", "inventory", "2026-01-15T08:00:00Z", "2026-01-15T09:00:00Z")
        self.assertEqual((await toolbox.query(early)).evidence, [])

    async def test_tool_retries_are_bounded(self):
        toolbox = SimulatedToolbox([], {"logs": ["error"]}, config(tool_attempts=2))
        result = await toolbox.query(Query("logs", "checkout", CASES[0]["incident"]["start"], CASES[0]["incident"]["end"]))
        self.assertEqual(len(toolbox.calls), 2)
        self.assertEqual(result.attempts, 2)
        self.assertIsNotNone(result.error)


class DomainTests(unittest.TestCase):
    def test_history_does_not_prove_current_cause(self):
        e = Evidence("prior", "history", "checkout", "2026-01-15T10:00:00Z", "oom_killed", True, "Old OOM")
        self.assertEqual(rank([e]), [])

    def test_duplicate_signals_do_not_inflate_confidence(self):
        evidence = [Evidence(**e) for e in CASES[0]["records"]]
        repeated = copy.deepcopy(CASES[0]["records"][1])
        repeated["id"] = "new-copy"
        before, after = rank(evidence), rank(evidence + [Evidence(**repeated)])
        self.assertEqual(before[0].score, after[0].score)
        self.assertEqual(before[0].confidence, after[0].confidence)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            Query("unknown", "x", "2026-01-15T10:00:00Z", "2026-01-15T11:00:00Z")
        with self.assertRaises(ValueError):
            Incident("x", "title", "service", "2026-01-15T11:00:00Z", "2026-01-15T10:00:00Z", "description")
        with self.assertRaises(ValueError):
            Config(max_rounds=0)
        with self.assertRaises(ValueError):
            Config(deadline_seconds=float("nan"))
        with self.assertRaises(ValueError):
            Evidence("x", "logs", "s", "2026-01-15T10:00:00Z", "c", 1, "t", 2)

    def test_duplicate_evidence_identity_rejected(self):
        with self.assertRaises(ValueError):
            SimulatedToolbox(CASES[0]["records"] * 2, {}, config())


class ModelTests(unittest.IsolatedAsyncioTestCase):
    def client(self, **kwargs):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder-not-a-real-key"}):
            return ResponsesClient("test-model", config(**kwargs))

    def response(self, data):
        return {"status": "completed", "usage": {"input_tokens": 123, "output_tokens": 45},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(data)}]}]}

    async def test_structured_payload_and_measured_tokens(self):
        client = self.client()
        with patch.object(client, "_post", return_value=self.response({"hypotheses": []})) as post:
            result = await client.structured("reasoning", {"evidence": []}, REASON_SCHEMA)
        self.assertEqual(result, {"hypotheses": []})
        self.assertEqual(client.usage()["total_tokens"], 168)
        self.assertFalse(post.call_args.args[0]["store"])
        self.assertTrue(post.call_args.args[0]["text"]["format"]["strict"])

    async def test_unknown_citation_rejected(self):
        client = self.client()
        data = {"hypotheses": [{"cause": "memory_leak", "confidence": .9,
                               "supporting": ["fabricated"], "contradicting": [],
                               "explanation": "unsupported", "assumptions": []}]}
        state = InvestigationState(Incident(**CASES[0]["incident"]), "multi")
        with patch.object(client, "_post", return_value=self.response(data)):
            with self.assertRaisesRegex(ValueError, "unknown citations"):
                await ReasoningAgent(client).run(state)

    async def test_model_refusal_is_not_a_valid_report(self):
        client = self.client()
        response = self.response({})
        response["output"][0]["content"] = [{"type": "refusal", "refusal": "No"}]
        with patch.object(client, "_post", return_value=response):
            with self.assertRaisesRegex(ValueError, "refused"):
                await client.structured("reasoning", {}, REASON_SCHEMA)
        self.assertEqual(client.usage()["total_tokens"], 168)

    async def test_incomplete_response_is_rejected(self):
        client = self.client()
        response = self.response({"hypotheses": []})
        response["status"] = "incomplete"
        with patch.object(client, "_post", return_value=response):
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                await client.structured("reasoning", {}, REASON_SCHEMA)

    async def test_bad_schema_is_rejected(self):
        client = self.client()
        with patch.object(client, "_post", return_value=self.response({"made_up": True})):
            with self.assertRaises(ValueError):
                await client.structured("reasoning", {}, REASON_SCHEMA)

    async def test_transport_failure_usage_is_explicitly_unknown(self):
        client = self.client()
        with patch.object(client, "_post", side_effect=RuntimeError("transport down")):
            with self.assertRaises(RuntimeError):
                await client.structured("reasoning", {}, REASON_SCHEMA)
        self.assertEqual(client.usage()["unknown_usage_calls"], 1)

    async def test_model_budget_is_bounded(self):
        client = self.client(max_llm_calls=1)
        with patch.object(client, "_post", return_value=self.response({"hypotheses": []})):
            await client.structured("reasoning", {}, REASON_SCHEMA)
            with self.assertRaisesRegex(RuntimeError, "budget"):
                await client.structured("reasoning", {}, REASON_SCHEMA)
        self.assertEqual(client.calls, 1)

    async def test_no_silent_offline_fallback_on_live_failure(self):
        client = self.client()
        case = CASES[0]
        with patch.object(client, "_post", side_effect=RuntimeError("transport down")):
            report = await Orchestrator(config(), client).investigate(Incident(**case["incident"]), case["records"])
        self.assertEqual(report["provider"], "openai")
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["termination"], "reasoning_failed")

    async def test_successful_live_pipeline_with_mocked_transport(self):
        client = self.client()
        case = CASES[7]
        hypothesis = {"hypotheses": [{"cause": "disk_exhaustion", "confidence": .9,
                                     "supporting": [e["id"] for e in case["records"]],
                                     "contradicting": [], "explanation": "ENOSPC and a full disk agree.",
                                     "assumptions": ["Confirm the affected volume before remediation."]}]}
        review = {"accepted": True, "concerns": [], "requests": []}
        with patch.object(client, "_post", side_effect=[self.response(hypothesis), self.response(review)]):
            report = await Orchestrator(config(), client).investigate(Incident(**case["incident"]), case["records"])
        self.assertEqual(report["root_cause"], "disk_exhaustion")
        self.assertEqual(report["tokens"]["total_tokens"], 336)
        self.assertTrue(report["critic_review"]["accepted"])

    async def test_live_critic_cannot_query_undiscovered_services(self):
        from orange_investigator.agents import CriticAgent
        client = self.client()
        state = InvestigationState(Incident(**CASES[0]["incident"]), "multi")
        review = {"accepted": False, "concerns": [], "requests": [{"tool": "logs", "service": "invented-service"}]}
        with patch.object(client, "_post", return_value=self.response(review)):
            with self.assertRaisesRegex(ValueError, "undiscovered"):
                await CriticAgent(client).run(state)

    async def test_single_live_baseline_uses_the_same_reasoning_prompt(self):
        client = self.client()
        case = CASES[7]
        with patch.object(client, "_post", return_value=self.response({"hypotheses": []})) as post:
            await Orchestrator(config(), client).investigate(Incident(**case["incident"]), case["records"], mode="single")
        payload = post.call_args.args[0]
        self.assertEqual(payload["text"]["format"]["name"], "reasoning")
        self.assertIn("Rank plausible causes", payload["instructions"])
        self.assertEqual(post.call_count, 1)

    def test_live_mode_requires_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                ResponsesClient("test-model", config())


if __name__ == "__main__":
    unittest.main()

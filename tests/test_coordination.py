import copy
import unittest

from igd.agents import PDAgent, PMAgent
from igd.backends.demo import DemoBackend, demo_plan
from igd.coordination import CoordinationResult, LocalCoordinator
from igd.engine import TPAgent
from igd.errors import BackendError, ValidationError
from igd.models import Plan, Task

from tests.test_core import FixedBackend


class RecordingCoordinator(LocalCoordinator):
    def __init__(self, callback=None):
        self.states = []
        self.callback = callback

    def coordinate(self, state):
        self.states.append(copy.deepcopy(state))
        if self.callback is not None:
            return self.callback(state)
        return super().coordinate(state)


class CoordinationTests(unittest.TestCase):
    def test_tp_receives_prior_results_and_updated_field(self):
        backend = DemoBackend()
        coordinator = RecordingCoordinator()
        report = TPAgent([PDAgent(backend), PMAgent(backend)], coordinator=coordinator).run(demo_plan())
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(len(coordinator.states), 3)
        first, second, final = coordinator.states
        self.assertEqual(first["results"], {})
        self.assertEqual(first["selection"]["task_id"], "design")
        self.assertEqual(second["selection"]["task_id"], "shaft")
        self.assertEqual(set(second["results"]), {"design"})
        self.assertEqual(second["last_execution"]["task_id"], "design")
        self.assertAlmostEqual(second["pheromones"]["design"], 0.8)
        self.assertIn("cadquery_code", final["results"]["shaft"])
        self.assertTrue(final["all_tasks_succeeded"])
        self.assertIsNone(final["selection"])
        self.assertIsNotNone(report["tp"]["summary"])
        self.assertEqual(report["metrics"]["tp_call_count"], 3)

    def test_premature_finish_is_rejected(self):
        backend = FixedBackend()
        coordinator = RecordingCoordinator(lambda state: CoordinationResult("finish", "Done.", total_tokens=9))
        report = TPAgent([PDAgent(backend)], coordinator=coordinator).run(Plan("x", (Task("a", "PD", "x"),)))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["tp"]["error_code"], "invalid_tp_decision")
        self.assertEqual(report["tasks"]["a"]["status"], "cancelled")
        self.assertEqual(report["metrics"]["known_tp_tokens"], 9)
        self.assertEqual(backend.received, {})

    def test_dispatch_cannot_bypass_dependencies_or_field_selection(self):
        coordinator = RecordingCoordinator(lambda state:
            CoordinationResult("dispatch", "Skip design.", "shaft", "pm-agent"))
        backend = DemoBackend()
        report = TPAgent([PDAgent(backend), PMAgent(backend)], coordinator=coordinator).run(demo_plan())
        self.assertEqual(report["tp"]["error_code"], "invalid_tp_decision")
        self.assertEqual(report["metrics"]["executed_tasks"], 0)

    def test_tp_can_abort_after_receiving_a_design_result(self):
        policy = LocalCoordinator()
        coordinator = RecordingCoordinator(lambda state:
            CoordinationResult("abort", "The requested design needs clarification.", total_tokens=7)
            if state["results"] else policy.coordinate(state))
        backend = DemoBackend()
        report = TPAgent([PDAgent(backend), PMAgent(backend)], coordinator=coordinator).run(demo_plan())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["tasks"]["design"]["status"], "succeeded")
        self.assertEqual(report["tasks"]["shaft"]["status"], "cancelled")
        self.assertEqual(report["metrics"]["executed_tasks"], 1)
        self.assertEqual(report["tp"]["error_code"], "tp_aborted")

    def test_tp_exception_is_sanitized_and_results_survive(self):
        policy = LocalCoordinator()
        def coordinate(state):
            if state["results"]:
                raise BackendError("timeout", "private transport details")
            return policy.coordinate(state)
        backend = DemoBackend()
        report = TPAgent([PDAgent(backend), PMAgent(backend)],
                         coordinator=RecordingCoordinator(coordinate)).run(demo_plan())
        self.assertEqual(report["tp"]["error_code"], "timeout")
        self.assertNotIn("private transport details", str(report))
        self.assertIsNotNone(report["tasks"]["design"]["result"])
        self.assertFalse(report["metrics"]["tp_token_usage_complete"])

    def test_round_limit_prevents_further_dispatch(self):
        backend = DemoBackend()
        report = TPAgent([PDAgent(backend), PMAgent(backend)], max_rounds=1).run(demo_plan())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["tp"]["error_code"], "round_limit")
        self.assertEqual(report["metrics"]["executed_tasks"], 1)
        self.assertEqual(report["tasks"]["shaft"]["status"], "cancelled")

    def test_coordination_state_is_isolated(self):
        policy = LocalCoordinator()
        def coordinate(state):
            decision = policy.coordinate(state)
            state["plan"]["tasks"][0]["inputs"].clear()
            state["results"].clear()
            state["pheromones"].clear()
            return decision
        backend = DemoBackend()
        report = TPAgent([PDAgent(backend), PMAgent(backend)],
                         coordinator=RecordingCoordinator(coordinate)).run(demo_plan())
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["plan"]["tasks"][0]["inputs"]["keyways"][1]["end_margin_mm"], 5)

    def test_decision_contract_rejects_invalid_fields(self):
        for value in ({"action": "skip", "summary": "x"},
                      {"action": "dispatch", "summary": "x"},
                      {"action": "finish", "summary": "x", "task_id": "a"},
                      {"action": "finish", "summary": "x", "raw_prompt": "private"}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                CoordinationResult.from_dict(value)

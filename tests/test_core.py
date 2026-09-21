import copy
import json
import random
import tempfile
import unittest
from pathlib import Path

from igd.acbac import ACBACConfig, PheromoneField, Proposal, weighted_choice
from igd.agents import Agent, PDAgent, PMAgent, PAAgent
from igd.artifacts import save_report
from igd.backends.demo import DEFAULT_PARAMETERS, DemoBackend, demo_plan, validate_parameters
from igd.engine import TPAgent
from igd.errors import BackendError, ValidationError
from igd.models import ExecutionResult, Plan, Role, Task, loads_json, validate_payload


def design_result(value=1):
    return ExecutionResult({"summary": "Design", "parameters": {"length": value},
                            "units": {"length": "mm"}}, quality=0.8, total_tokens=12)


class FixedBackend:
    name = "test"

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.received = {}

    def execute(self, task, dependency_results):
        self.received[task.id] = copy.deepcopy(dependency_results)
        value = self.responses.get(task.id, design_result())
        if isinstance(value, Exception):
            raise value
        return value


class ContractTests(unittest.TestCase):
    def test_unknown_dependency(self):
        with self.assertRaises(ValidationError):
            Plan("test", (Task("a", "PD", "test", ("missing",)),))

    def test_cycle(self):
        with self.assertRaises(ValidationError):
            Plan("test", (Task("a", "PD", "test", ("b",)), Task("b", "PD", "test", ("a",))))

    def test_duplicates(self):
        with self.assertRaises(ValidationError):
            Plan("test", (Task("a", "PD", "test"), Task("A", "PM", "test")))
        with self.assertRaises(ValidationError):
            Task("a", "PD", "test", ("b", "b"))

    def test_unsafe_ids(self):
        for value in ("../escape", "a/b", "a\\b", "CON", "NUL", "", "a.py"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Task(value, "PM", "test")

    def test_empty_unknown_or_mistyped_plan(self):
        for value in ({"requirement": "x", "tasks": []},
                      {"requirement": "x", "tasks": {}, "surprise": 1},
                      {"requirement": "x", "tasks": [{"id": "a", "role": "UNKNOWN", "description": "x"}]}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Plan.from_dict(value)

    def test_strict_json(self):
        for value in ('{"x": ' + '9' * 5000 + '}', '{"x": NaN}', '{"x": 1e999}', '{"x": 1, "x": 2}', '{broken'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                loads_json(value)

    def test_empty_result_is_rejected(self):
        for role, value in (
            (Role.PD, {"summary": "done", "parameters": {}, "units": {"length": "mm"}}),
            (Role.PM, {"summary": "done", "cadquery_code": ""}),
            (Role.PM, {"summary": "done", "cadquery_code": "cube([1,1,1]);"}),
            (Role.PM, {"summary": "done", "cadquery_code": "import cadquery as cq\npass"}),
            (Role.PA, {"summary": "done", "cadquery_code": "import cadquery as cq\nresult = cq.Assembly()", "components": []}),
        ):
            with self.subTest(role=role), self.assertRaises(ValidationError):
                validate_payload(role, value)

    def test_raw_response_field_is_rejected(self):
        value = design_result().payload | {"raw_dify_response": {"prompt": "private"}}
        with self.assertRaises(ValidationError):
            validate_payload(Role.PD, value)

    def test_quality_and_token_contract(self):
        for quality, tokens in ((float("nan"), 0), (1.1, 1), (-1, 0), (True, 1), (0.5, True), (0.5, -1)):
            with self.subTest(quality=quality, tokens=tokens), self.assertRaises(ValidationError):
                ExecutionResult(design_result().payload, quality, tokens).validated(Role.PD)

    def test_dimension_constraints(self):
        for patch in ({"segment_diameters_mm": [-1, 57, 40, 32]}, {"tooth_count": 2},
                      {"gear_face_width_mm": 60}, {"keyway_depth_mm": float("inf")}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                validate_parameters(DEFAULT_PARAMETERS | patch)


class ACBACTests(unittest.TestCase):
    def test_paper_feedback_equation(self):
        field = PheromoneField(["a"], ACBACConfig(evaporation=0.2, reinforcement=2.0))
        self.assertAlmostEqual(field.feedback("a", True, 0.75), 2.3)
        self.assertAlmostEqual(field.feedback("a", False, 1.0), 1.84)
        self.assertAlmostEqual(field.feedback("a", True, None), 1.472)

    def test_proposal_equation_and_inverse_time(self):
        field = PheromoneField(["a"])
        packets = [Proposal("first", "a", 0.8, 0.6, 1.0, 0.9, 10),
                   Proposal("second", "a", 0.8, 0.6, 1.0, 0.9, 20)]
        scored = field.score(packets)
        self.assertAlmostEqual(scored[0].strength, 0.86)
        self.assertAlmostEqual(scored[1].strength, 0.76)
        self.assertAlmostEqual(scored[1].time_score, 0.5)
        self.assertAlmostEqual(field.concentration("a", scored), 2.62)
        self.assertAlmostEqual(field.concentration("a", field.score(packets)), 2.62)
        self.assertEqual(field.values["a"], 1.0)

    def test_configuration_ranges(self):
        for patch in ({"evaporation": -0.1}, {"evaporation": float("nan")},
                      {"reinforcement": -1}, {"weights": {"confidence": 1.0}}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                ACBACConfig(**patch)

    def test_duplicate_proposal_and_invalid_duration(self):
        p = Proposal("agent", "a", 1, 1, 1, 1, 1)
        with self.assertRaises(ValidationError):
            PheromoneField(["a"]).score([p, p])
        with self.assertRaises(ValidationError):
            Proposal("agent", "a", 1, 1, 1, 1, 0)

    def test_weighted_selection(self):
        rng = random.Random(5)
        choices = [weighted_choice(["a", "b"], [1, 4], rng) for _ in range(4000)]
        self.assertGreater(choices.count("b"), 2900)
        self.assertLess(choices.count("b"), 3500)
        with self.assertRaises(ValidationError):
            weighted_choice(["a"], [0], rng)
        self.assertEqual(weighted_choice(["a", "b"], [0, 1], rng), "b")


class EngineTests(unittest.TestCase):
    def test_demo_dependency_order_and_results(self):
        backend = DemoBackend()
        report = TPAgent([PDAgent(backend), PMAgent(backend), PAAgent(backend)]).run(demo_plan())
        self.assertEqual(report["status"], "succeeded")
        starts = [event["task_id"] for event in report["events"] if event["event"] == "started"]
        self.assertEqual(starts[0], "design")
        self.assertEqual(starts[-1], "gear_shaft")
        self.assertEqual(starts, ["design", "gear_shaft"])
        self.assertIn("range(p[\"tooth_count\"])", report["tasks"]["gear_shaft"]["result"]["payload"]["cadquery_code"])
        self.assertEqual([call["decision"]["action"] for call in report["tp"]["calls"]],
                         ["dispatch", "dispatch", "finish"])
        self.assertEqual(report["metrics"]["known_execution_tokens"], 0)

    def test_failure_blocks_only_descendants(self):
        backend = FixedBackend({"bad": BackendError("test_failure", "private-secret")})
        plan = Plan("test", (Task("bad", "PD", "bad"), Task("child", "PD", "child", ("bad",)),
                            Task("grandchild", "PD", "child", ("child",)), Task("independent", "PD", "good")))
        report = TPAgent([PDAgent(backend)]).run(plan)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["tasks"]["bad"]["status"], "failed")
        self.assertEqual(report["tasks"]["child"]["status"], "blocked")
        self.assertEqual(report["tasks"]["grandchild"]["status"], "blocked")
        self.assertEqual(report["tasks"]["independent"]["status"], "succeeded")
        self.assertNotIn("private-secret", json.dumps(report))
        self.assertNotIn("child", backend.received)
        self.assertFalse(report["metrics"]["execution_token_usage_complete"])

    def test_multiple_predecessors_keep_distinct_results(self):
        backend = FixedBackend({"first": design_result(10), "second": design_result(20)})
        plan = Plan("test", (Task("first", "PD", "first"), Task("second", "PD", "second"),
                            Task("last", "PD", "last", ("first", "second"))))
        report = TPAgent([PDAgent(backend)]).run(plan)
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(backend.received["last"]["first"]["parameters"]["length"], 10)
        self.assertEqual(backend.received["last"]["second"]["parameters"]["length"], 20)

    def test_invalid_output_does_not_complete(self):
        backend = FixedBackend({"a": ExecutionResult({})})
        report = TPAgent([PDAgent(backend)]).run(Plan("x", (Task("a", "PD", "x"),)))
        self.assertEqual(report["tasks"]["a"]["error_code"], "invalid_result")
        self.assertEqual(report["status"], "failed")

    def test_missing_agent_is_terminal_failure(self):
        report = TPAgent([PDAgent(FixedBackend())]).run(Plan("x", (Task("a", "PM", "x"),)))
        self.assertEqual(report["tasks"]["a"]["error_code"], "no_eligible_agent")
        self.assertEqual(report["status"], "failed")

    def test_history_changes_next_proposal(self):
        backend = FixedBackend()
        plan = Plan("x", (Task("first", "PD", "x"), Task("second", "PD", "x", ("first",))))
        report = TPAgent([PDAgent(backend)]).run(plan)
        packets = [e for e in report["events"] if e["event"] == "proposal"]
        self.assertAlmostEqual(packets[0]["history"], 0.5)
        self.assertAlmostEqual(packets[1]["history"], 2 / 3)

    def test_two_capable_agents_bid(self):
        backend = FixedBackend()
        agents = [PDAgent(backend, id="pd-one"), Agent("flexible", {"PD": 0.5, "PM": 1}, backend)]
        report = TPAgent(agents).run(Plan("x", (Task("a", "PD", "x"),)))
        packets = [e for e in report["events"] if e["event"] == "proposal"]
        self.assertEqual(len(packets), 2)
        self.assertGreater(next(e["strength"] for e in packets if e["agent_id"] == "pd-one"),
                           next(e["strength"] for e in packets if e["agent_id"] == "flexible"))

    def test_run_isolation_and_seed(self):
        backend = DemoBackend()
        engine = TPAgent([PDAgent(backend), PMAgent(backend), PAAgent(backend)], seed=11)
        a, b = engine.run(demo_plan()), engine.run(demo_plan())
        self.assertEqual(a["events"], b["events"])
        self.assertEqual(a["pheromones"], b["pheromones"])
        self.assertEqual(a["metrics"]["executed_tasks"], 2)

    def test_safe_artifacts_and_unique_runs(self):
        backend = DemoBackend()
        report = TPAgent([PDAgent(backend), PMAgent(backend), PAAgent(backend)]).run(demo_plan())
        with tempfile.TemporaryDirectory() as directory:
            first = save_report(report, Path(directory))
            second = save_report(report, Path(directory))
            self.assertNotEqual(first, second)
            self.assertTrue((first / "artifacts" / "gear_shaft.py").is_file())
            saved = json.loads((first / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "succeeded")
            self.assertFalse((first / "run.json.tmp").exists())

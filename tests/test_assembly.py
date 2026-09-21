import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from igd.agents import PAAgent, PMAgent
from igd.backends.demo import DEFAULT_PARAMETERS, demo_plan, demo_sources
from igd.cli import main
from igd.engine import TPAgent
from igd.models import ExecutionResult, Plan, Task

from tests.test_core import FixedBackend
from tests.test_dify import LocalWorkflowServer


class AssemblyTests(unittest.TestCase):
    def test_pa_still_runs_after_models_with_tp_coordination(self):
        with LocalWorkflowServer() as server, tempfile.TemporaryDirectory() as directory:
            base = demo_plan()
            server.plan_value = Plan("A stepped shaft in an assembly.", base.tasks + (
                Task("assembly", "PA", "Place the stepped shaft at the origin.", ("shaft",)),))
            source = demo_sources(DEFAULT_PARAMETERS)["shaft"]
            source += "\npart = result\nresult = cq.Assembly().add(part, name='shaft')\n"
            server.task_results["assembly"] = ExecutionResult({
                "summary": "Assembly containing the modelled stepped shaft.",
                "cadquery_code": source, "components": ["shaft"],
            })
            env = {"IGD_DIFY_BASE_URL": server.url.removesuffix("/workflows/run")}
            env.update({f"IGD_DIFY_{role}_API_KEY": "test-only-token" for role in ("TP", "PD", "PM", "PA")})
            with patch.dict("os.environ", env, clear=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["run", "--requirement", "A stepped shaft in an assembly.", "--output", directory])
            self.assertEqual(code, 0)
            self.assertEqual(len(server.requests), 8)
            report = json.loads(next(Path(directory).glob("*/run.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["tasks"]["assembly"]["status"], "succeeded")
            self.assertEqual(report["metrics"]["known_total_tokens"], 136)
            self.assertEqual(report["metrics"]["tp_call_count"], 5)
            pa_request = next(json.loads(r[2]["inputs"]["request"]) for r in server.requests
                              if json.loads(r[2]["inputs"]["request"])["role"] == "PA")
            self.assertEqual(set(pa_request["dependency_results"]), {"shaft"})
            self.assertIn("cadquery_code", pa_request["dependency_results"]["shaft"])
            self.assertEqual(server.coordinate_states[-1]["results"]["assembly"]["components"], ["shaft"])

    def test_assembly_cannot_name_a_model_it_did_not_receive(self):
        backend = FixedBackend({
            "model": ExecutionResult({"summary": "Part", "cadquery_code":
                                     "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)"}),
            "assembly": ExecutionResult({"summary": "Assembly", "cadquery_code":
                                        "import cadquery as cq\nresult = cq.Assembly()", "components": ["other"]}),
        })
        plan = Plan("Assembly", (Task("model", "PM", "Build a part"),
                                Task("assembly", "PA", "Assemble the part", ("model",))))
        report = TPAgent([PMAgent(backend), PAAgent(backend)]).run(plan)
        self.assertEqual(report["tasks"]["assembly"]["error_code"], "invalid_result")
        self.assertIsNone(report["tasks"]["assembly"]["result"])
        self.assertEqual(report["status"], "failed")

    def test_missing_model_executor_blocks_assembly(self):
        plan = Plan("Assembly", (Task("model", "PM", "Build a part"),
                                Task("assembly", "PA", "Assemble the part", ("model",))))
        report = TPAgent([PAAgent(FixedBackend())]).run(plan)
        self.assertEqual(report["tasks"]["model"]["error_code"], "no_eligible_agent")
        self.assertEqual(report["tasks"]["assembly"]["status"], "blocked")

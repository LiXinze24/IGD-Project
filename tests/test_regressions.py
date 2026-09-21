import io
import json
import random
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.client import BadStatusLine, IncompleteRead
from pathlib import Path
from unittest.mock import MagicMock, patch

from igd.acbac import weighted_choice
from igd.backends.demo import DemoBackend, demo_plan
from igd.backends.dify import DifyClient
from igd.cli import main
from igd.config import Endpoint
from igd.errors import BackendError, ValidationError
from igd.models import Plan, Role, Task, loads_json, validate_payload
from tests.test_dify import LocalWorkflowServer


class RegressionTests(unittest.TestCase):
    def test_cad_source_rejects_compile_errors_or_missing_object(self):
        cases = [
            "result = cq.Workplane(); return",
            "def make(a, a):\n    pass\nresult = cq.Workplane()",
            "def make():\n    result = cq.Workplane()",
            "if True:\n    result = cq.Workplane()",
            "result = None", "result = 1", "result = []", "result: object",
            "result = cq.Workplane()\ndel result",
        ]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValidationError):
                validate_payload(Role.PM, {"summary": "model", "cadquery_code": "import cadquery as cq\n" + body})

    def test_cad_source_allows_helper_without_executing_it(self):
        source = ("import cadquery as cq\n"
                  "def make():\n    raise RuntimeError('must not execute')\n"
                  "result = make()\n")
        self.assertEqual(validate_payload(Role.PM, {"summary": "model", "cadquery_code": source})["cadquery_code"], source)

    def test_invalid_unicode_is_rejected_but_non_bmp_text_survives(self):
        for document in ('{"x":"\\ud800"}', '{"\\udfff":1}', '{"x":["\\ud800"]}'):
            with self.subTest(document=document), self.assertRaises(ValidationError):
                loads_json(document)
        with self.assertRaises(ValidationError):
            Task("a", "PD", "\ud800")
        self.assertEqual(loads_json('{"x":"\\ud83d\\ude80"}')["x"], "\U0001f680")

    def test_truncated_http_is_sanitized_at_open_and_read(self):
        for stage in ("open", "read"):
            for failure in (IncompleteRead(b"private-prompt"), BadStatusLine("private-prompt")):
                client = DifyClient(Endpoint("https://example.com/v1/workflows/run", "test-only-token"))
                with self.subTest(stage=stage, failure=type(failure).__name__):
                    manager = MagicMock()
                    manager.__enter__.return_value.read.side_effect = failure
                    kwargs = {"side_effect": failure} if stage == "open" else {"return_value": manager}
                    with patch.object(client._opener, "open", **kwargs), self.assertRaises(BackendError) as caught:
                        client.call({})
                    self.assertEqual(caught.exception.code, "invalid_http_response")
                    self.assertNotIn("private-prompt", str(caught.exception))

    def test_subnormal_weight_never_selects_zero_weight_item(self):
        for seed in range(30):
            self.assertEqual(weighted_choice(["eligible", "zero"], [5e-324, 0], random.Random(seed)), "eligible")

    def test_bad_output_storage_is_rejected_before_tp_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "already-a-file"
            path.write_text("existing content", encoding="utf-8")
            with patch("igd.backends.dify.DifyBackend.plan") as planner, redirect_stderr(io.StringIO()):
                self.assertEqual(main(["run", "--requirement", "stepped shaft", "--output", str(path)]), 2)
            planner.assert_not_called()
            self.assertEqual(path.read_text(), "existing content")
            with patch("igd.artifacts.tempfile.TemporaryFile", side_effect=PermissionError), \
                 patch("igd.backends.dify.DifyBackend.plan") as planner, redirect_stderr(io.StringIO()):
                self.assertEqual(main(["run", "--requirement", "stepped shaft", "--output", directory]), 2)
            planner.assert_not_called()

    def test_tp_http_failure_returns_report_without_private_details(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            plan_path.write_text(json.dumps(demo_plan().to_dict()), encoding="utf-8")
            env = {f"IGD_DIFY_{r}_API_KEY": "test-only-token" for r in ("TP", "PD", "PM")}
            stderr = io.StringIO()
            with patch.dict("os.environ", env, clear=True), \
                 redirect_stdout(io.StringIO()):
                with patch("urllib.request.OpenerDirector.open", side_effect=BadStatusLine("private-prompt")), redirect_stderr(stderr):
                    code = main(["run", "--plan", str(plan_path), "--output", str(Path(directory) / "out")])
            self.assertEqual(code, 1)
            self.assertNotIn("private-prompt", stderr.getvalue())
            report = json.loads(next((Path(directory) / "out").glob("*/run.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["tp"]["error_code"], "invalid_http_response")
            self.assertEqual(report["metrics"]["executed_tasks"], 0)

    def test_saved_plan_still_requires_tp(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            plan_path.write_text(json.dumps(demo_plan().to_dict()), encoding="utf-8")
            env = {f"IGD_DIFY_{r}_API_KEY": "test-only-token" for r in ("PD", "PM")}
            with patch.dict("os.environ", env, clear=True), patch.object(DifyClient, "call") as call, redirect_stderr(io.StringIO()):
                code = main(["run", "--plan", str(plan_path), "--output", str(Path(directory) / "out")])
            self.assertEqual(code, 2)
            call.assert_not_called()

    def test_tp_cannot_finish_before_functional_tasks_over_http(self):
        with LocalWorkflowServer() as server, tempfile.TemporaryDirectory() as directory:
            server.tp_decisions = [{"action": "finish", "summary": "Premature completion."}]
            env = {"IGD_DIFY_BASE_URL": server.url.removesuffix("/workflows/run")}
            env.update({f"IGD_DIFY_{r}_API_KEY": "test-only-token" for r in ("TP", "PD", "PM")})
            with patch.dict("os.environ", env, clear=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["run", "--requirement", "stepped shaft", "--output", directory])
            self.assertEqual(code, 1)
            self.assertEqual(len(server.requests), 2)

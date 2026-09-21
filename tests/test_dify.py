import io
import json
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from igd.backends.demo import DemoBackend, demo_plan
from igd.backends.dify import DifyBackend, DifyClient
from igd.cli import main
from igd.config import Endpoint, load_environment
from igd.errors import BackendError, ValidationError
from igd.models import ExecutionResult, Plan, Task


class LocalWorkflowServer:
    def __enter__(self):
        fixture = self
        self.requests = []
        self.status = 200
        self.raw = None
        self.workflow_status = "succeeded"
        self.result_as_string = False
        self.override_result = None
        self.demo = DemoBackend()
        self.failed_roles = set()
        self.plan_value = None
        self.task_results = {}
        self.coordinate_states = []
        self.tp_decisions = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.requests.append((self.path, dict(self.headers), data))
                request = json.loads(data["inputs"]["request"])
                if request["role"] == "TP":
                    if request.get("phase") == "coordinate":
                        fixture.coordinate_states.append(request["state"])
                        value = (fixture.tp_decisions.pop(0) if fixture.tp_decisions else
                                 fixture.demo.coordinate(request["state"]).to_dict())
                    else:
                        value = (fixture.plan_value or demo_plan()).to_dict()
                        value["requirement"] = request["requirement"]
                else:
                    task = Task.from_dict(request["task"])
                    result = fixture.task_results.get(task.id)
                    if result is None:
                        result = fixture.demo.execute(task, request["dependency_results"])
                    value = {"payload": result.payload, "quality": result.quality}
                if fixture.override_result is not None:
                    value = fixture.override_result
                if fixture.result_as_string:
                    value = json.dumps(value)
                body = fixture.raw if fixture.raw is not None else json.dumps({
                    "data": {"status": ("failed" if request["role"] in fixture.failed_roles else fixture.workflow_status), "outputs": {"result": value}, "total_tokens": 17},
                    "workflow_run_id": "fixture-run",
                }).encode()
                self.send_response(fixture.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if fixture.status == 302:
                    self.send_header("Location", fixture.url + "/redirect")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/workflows/run"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def client(self, **kwargs):
        return DifyClient(Endpoint(self.url, "test-only-token", **kwargs))

    def request(self):
        return {"schema_version": 1, "role": "PD", "task": demo_plan().tasks[0].to_dict(),
                "dependency_results": {}}


class ConfigurationTests(unittest.TestCase):
    def test_url_and_secret_validation(self):
        for url in ("http://example.com/v1/workflows/run", "https://user:pass@example.com/v1/workflows/run",
                    "https://example.com/v1/workflows/run?key=secret", "https://example.com/apps/123",
                    "https://example.com:bad/v1/workflows/run"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                Endpoint(url, "test-only-token")
        with self.assertRaises(ValidationError):
            Endpoint("https://example.com/v1/workflows/run", "test\r\nInjected: true")
        self.assertNotIn("test-only-token", repr(Endpoint("https://example.com/v1/workflows/run", "test-only-token")))

    def test_role_key_required(self):
        with self.assertRaisesRegex(ValidationError, "IGD_DIFY_PM_API_KEY"):
            Endpoint.from_environment("PM", {})

    def test_environment_file_is_literal_and_shell_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("VALUE='file value'\nLITERAL=\u0024{NO_EXPANSION}\n", encoding="utf-8")
            with patch.dict("os.environ", {"VALUE": "shell value"}, clear=True):
                result = load_environment(path)
            self.assertEqual(result["VALUE"], "shell value")
            self.assertEqual(result["LITERAL"], "\u0024{NO_EXPANSION}")
            path.write_text("VALUE=one\nVALUE=two\n", encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_environment(path)


class DifyTests(unittest.TestCase):
    def test_payload_authentication_and_string_result(self):
        with LocalWorkflowServer() as server:
            server.result_as_string = True
            result, tokens = server.client().call(server.request())
            self.assertIn("parameters", result["payload"])
            self.assertEqual(tokens, 17)
            path, headers, body = server.requests[0]
            self.assertEqual(headers["Authorization"], "Bearer test-only-token")
            self.assertEqual(body["response_mode"], "blocking")
            self.assertEqual(body["user"], "igd-client")
            self.assertIsInstance(body["inputs"]["request"], str)

    def test_failed_or_paused_http_200_is_failure(self):
        with LocalWorkflowServer() as server:
            for status in ("failed", "paused", "running", None):
                server.workflow_status = status
                with self.subTest(status=status), self.assertRaises(BackendError) as caught:
                    server.client().call(server.request())
                self.assertEqual(caught.exception.code, "workflow_not_succeeded")

    def test_errors_do_not_echo_remote_secrets(self):
        with LocalWorkflowServer() as server:
            for status in (401, 429, 500):
                server.status = status
                server.raw = b'private-system-prompt test-only-token'
                with self.subTest(status=status), self.assertRaises(BackendError) as caught:
                    server.client().call(server.request())
                self.assertEqual(caught.exception.code, "http_error")
                self.assertNotIn("private-system-prompt", str(caught.exception))
                self.assertNotIn("test-only-token", str(caught.exception))

    def test_redirect_is_not_followed(self):
        with LocalWorkflowServer() as server:
            server.status = 302
            with self.assertRaises(BackendError) as caught:
                server.client().call(server.request())
            self.assertEqual(caught.exception.code, "redirect_rejected")
            self.assertEqual(len(server.requests), 1)

    def test_malformed_json_or_missing_output(self):
        with LocalWorkflowServer() as server:
            for raw in (b"<html>not JSON</html>", b'{"data":{"status":"succeeded","outputs":{}}}',
                        b'{"data":{"status":"succeeded","outputs":{"result":42}}}',
                        b'{"data":{"status":"succeeded","outputs":{"result":"NaN"}}}'):
                server.raw = raw
                with self.subTest(raw=raw), self.assertRaises(BackendError) as caught:
                    server.client().call(server.request())
                self.assertEqual(caught.exception.code, "invalid_response")

    def test_size_limit(self):
        with LocalWorkflowServer() as server:
            with patch("igd.backends.dify.MAX_RESPONSE_BYTES", 20):
                with self.assertRaises(BackendError) as caught:
                    server.client().call(server.request())
            self.assertEqual(caught.exception.code, "response_too_large")

    def test_timeout_and_network_error(self):
        client = DifyClient(Endpoint("https://example.com/v1/workflows/run", "test-only-token"))
        for failure, code in ((socket.timeout("secret"), "timeout"), (URLError("secret"), "network_error")):
            with patch.object(client._opener, "open", side_effect=failure):
                with self.assertRaises(BackendError) as caught:
                    client.call({})
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("secret", str(caught.exception))

    def test_backend_rejects_empty_payload(self):
        with LocalWorkflowServer() as server:
            server.override_result = {"payload": {}}
            backend = DifyBackend({"PD": server.client()})
            with self.assertRaises(BackendError) as caught:
                backend.execute(demo_plan().tasks[0], {})
            self.assertEqual(caught.exception.code, "invalid_result")

    def test_alternative_result_path(self):
        with LocalWorkflowServer() as server:
            server.raw = json.dumps({"data": {"status": "succeeded", "outputs": {"payload": {"x": 1}}}}).encode()
            value, tokens = server.client(result_path="data.outputs").call(server.request())
            self.assertEqual(value, {"payload": {"x": 1}})
            self.assertIsNone(tokens)


class CLITests(unittest.TestCase):
    def test_dify_full_pipeline_over_http(self):
        with LocalWorkflowServer() as server, tempfile.TemporaryDirectory() as directory:
            env = {"IGD_DIFY_BASE_URL": server.url.removesuffix("/workflows/run")}
            for role in ("TP", "PD", "PM"):
                env[f"IGD_DIFY_{role}_API_KEY"] = "test-only-token"
            with patch.dict("os.environ", env, clear=True), redirect_stdout(io.StringIO()):
                code = main(["run", "--requirement", "A four-segment stepped shaft", "--output", directory])
            self.assertEqual(code, 0)
            self.assertEqual(len(server.requests), 6)
            report = json.loads(next(Path(directory).glob("*/run.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "succeeded")
            self.assertEqual(report["metrics"]["known_total_tokens"], 102)
            self.assertEqual(report["metrics"]["known_tp_tokens"], 68)
            self.assertEqual(report["metrics"]["tp_call_count"], 4)
            self.assertEqual([json.loads(r[2]["inputs"]["request"])["role"] for r in server.requests],
                             ["TP", "TP", "PD", "TP", "PM", "TP"])
            self.assertEqual(set(server.coordinate_states[1]["results"]), {"design"})
            self.assertIn("cadquery_code", server.coordinate_states[2]["results"]["shaft"])
            self.assertTrue(report["metrics"]["total_token_usage_complete"])
            modelling_call = json.loads(server.requests[-2][2]["inputs"]["request"])
            self.assertEqual(set(modelling_call["dependency_results"]), {"design"})
            self.assertNotIn("test-only-token", json.dumps(report))
            self.assertNotIn(server.url, json.dumps(report))

    def test_failure_returns_nonzero_and_saves_report(self):
        with LocalWorkflowServer() as server, tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(demo_plan().to_dict()), encoding="utf-8")
            server.failed_roles = {"PD"}
            env = {"IGD_DIFY_BASE_URL": server.url.removesuffix("/workflows/run")}
            for role in ("TP", "PD", "PM"):
                env[f"IGD_DIFY_{role}_API_KEY"] = "test-only-token"
            with patch.dict("os.environ", env, clear=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["run", "--plan", str(plan_path), "--output", str(root / "out")])
            self.assertEqual(code, 1)
            report = json.loads(next((root / "out").glob("*/run.json")).read_text(encoding="utf-8"))
            self.assertEqual(report["tasks"]["shaft"]["status"], "blocked")
            self.assertEqual(len(server.requests), 3)

    def test_missing_key_fails_before_any_request(self):
        with patch.dict("os.environ", {}, clear=True), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["run", "--requirement", "test"]), 2)

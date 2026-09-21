"""Dify Workflow API adapter. Prompts and workflow definitions stay server-side."""

from __future__ import annotations

import json
import socket
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..config import Endpoint
from ..coordination import CoordinationResult
from ..errors import BackendError, ValidationError
from ..models import ExecutionResult, Plan, Task, fields, json_copy, loads_json, require_object


MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DifyClient:
    def __init__(self, endpoint: Endpoint):
        self.endpoint = endpoint
        self._opener = build_opener(_NoRedirect())

    def call(self, request: dict) -> tuple[dict, int | None]:
        body = {
            "inputs": {self.endpoint.input_key: json.dumps(json_copy(request), ensure_ascii=False, allow_nan=False)},
            "response_mode": "blocking",
            "user": self.endpoint.user,
        }
        req = Request(self.endpoint.url, method="POST", data=json.dumps(body).encode("utf-8"),
                      headers={"Authorization": "Bearer " + self.endpoint.api_key,
                               "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.endpoint.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise BackendError("response_too_large", "Dify response exceeds the size limit")
        except HTTPError as error:
            # Closing without reading prevents upstream body text from entering logs.
            code = error.code
            error.close()
            if 300 <= code < 400:
                raise BackendError("redirect_rejected", "Dify endpoint redirects are not followed") from None
            raise BackendError("http_error", f"Dify returned HTTP {code}") from None
        except (socket.timeout, TimeoutError):
            raise BackendError("timeout", "Dify request timed out; remote execution may still be running") from None
        except HTTPException:
            raise BackendError("invalid_http_response", "Dify returned an incomplete or invalid HTTP response") from None
        except (URLError, OSError):
            raise BackendError("network_error", "Unable to reach the configured Dify endpoint") from None

        try:
            document = require_object(loads_json(raw.decode("utf-8-sig")), "Dify response")
            data = require_object(document.get("data"), "Dify data")
            if data.get("status") != "succeeded":
                raise BackendError("workflow_not_succeeded", "Dify workflow did not finish successfully")
            value = document
            for key in self.endpoint.result_path.split("."):
                value = require_object(value, "result path")[key]
            if isinstance(value, str):
                value = loads_json(value)
            value = require_object(value, "workflow result")
            tokens = data.get("total_tokens")
            if tokens is not None and (type(tokens) is not int or tokens < 0):
                raise ValidationError("Invalid token count")
            return value, tokens
        except (ValidationError, KeyError, UnicodeError):
            raise BackendError("invalid_response", "Dify response does not satisfy the configured result contract") from None


class DifyBackend:
    name = "dify"

    def __init__(self, clients: dict[str, DifyClient]):
        self.clients = dict(clients)

    @classmethod
    def from_environment(cls, environment: dict[str, str], roles) -> DifyBackend:
        return cls({role: DifyClient(Endpoint.from_environment(role, environment)) for role in sorted(set(roles))})

    def plan(self, requirement: str) -> tuple[Plan, int | None]:
        if "TP" not in self.clients:
            raise ValidationError("Configure a TP workflow for planning and coordination")
        value, tokens = self.clients["TP"].call({"schema_version": 2, "role": "TP", "phase": "plan", "requirement": requirement})
        plan = Plan.from_dict(value)
        if plan.requirement != requirement:
            raise ValidationError("TP plan must preserve the original requirement")
        return plan, tokens

    def coordinate(self, state: dict) -> CoordinationResult:
        if "TP" not in self.clients:
            raise BackendError("missing_backend", "Configure a TP workflow for coordination")
        value, tokens = self.clients["TP"].call({
            "schema_version": 2, "role": "TP", "phase": "coordinate", "state": json_copy(state),
        })
        try:
            return CoordinationResult.from_dict(value, tokens)
        except ValidationError:
            raise BackendError("invalid_tp_decision", "TP output does not satisfy the coordination contract") from None

    def execute(self, task: Task, dependency_results: dict[str, dict]) -> ExecutionResult:
        role = task.role.value
        if role not in self.clients:
            raise BackendError("missing_backend", "No workflow is configured for this role")
        value, tokens = self.clients[role].call({
            "schema_version": 1, "role": role, "task": task.to_dict(),
            "dependency_results": json_copy(dependency_results),
        })
        try:
            fields(value, {"payload"}, {"quality"}, "workflow result")
            return ExecutionResult(value["payload"], value.get("quality"), tokens).validated(task.role)
        except ValidationError:
            raise BackendError("invalid_result", "Workflow output does not satisfy the role contract") from None


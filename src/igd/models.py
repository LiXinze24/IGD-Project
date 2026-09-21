"""Validated task and result contracts shared by every backend."""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .errors import ValidationError


class Role(str, Enum):
    PD = "PD"
    PM = "PM"
    PA = "PA"


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


def require_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValidationError(f"{label} must be a JSON object with string keys")
    return value


def fields(value: dict, required: set[str], optional: set[str], label: str) -> None:
    if required - value.keys():
        raise ValidationError(f"{label} is missing required fields: {', '.join(sorted(required - value.keys()))}")
    if value.keys() - required - optional:
        raise ValidationError(f"{label} contains unsupported fields")


def nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValidationError(f"{label} must contain valid Unicode") from None
    return value


def identifier(value: Any, label: str = "id") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValidationError(f"{label} must start with a letter and contain 1-64 letters, digits, underscores or hyphens")
    if value.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValidationError(f"{label} uses a reserved filename")
    return value


def number(value: Any, label: str, minimum: float = 0, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be numeric")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValidationError(f"{label} must be finite") from None
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ValidationError(f"{label} is outside its finite numeric range")
    return result


def json_copy(value: Any) -> Any:
    try:
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
        encoded.encode("utf-8")  # Reject unpaired surrogates before artifact writing.
        return json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValidationError("Value must be finite JSON data") from None


def loads_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValidationError("JSON contains duplicate keys")
            result[key] = value
        return result

    def constant(_value):
        raise ValidationError("JSON contains a non-finite number")

    try:
        return json_copy(json.loads(text, object_pairs_hook=pairs, parse_constant=constant))
    except ValidationError:
        raise
    except (ValueError, RecursionError):
        raise ValidationError("Invalid JSON document") from None


@dataclass(frozen=True)
class Task:
    id: str
    role: Role
    description: str
    dependencies: tuple[str, ...] = ()
    inputs: dict = field(default_factory=dict)

    def __post_init__(self):
        identifier(self.id, "task.id")
        try:
            object.__setattr__(self, "role", Role(self.role))
        except (TypeError, ValueError):
            raise ValidationError("task.role must be PD, PM or PA") from None
        nonempty_text(self.description, "task.description")
        if not isinstance(self.dependencies, (tuple, list)):
            raise ValidationError("task.dependencies must be an array")
        deps = tuple(identifier(d, "dependency id") for d in self.dependencies)
        if len(deps) != len(set(deps)) or self.id in deps:
            raise ValidationError("Task dependencies must be unique and cannot include itself")
        object.__setattr__(self, "dependencies", deps)
        object.__setattr__(self, "inputs", json_copy(require_object(self.inputs, "task.inputs")))

    @classmethod
    def from_dict(cls, value: dict) -> Task:
        value = require_object(value, "task")
        fields(value, {"id", "role", "description"}, {"dependencies", "inputs"}, "task")
        return cls(**value)

    def to_dict(self) -> dict:
        return {"id": self.id, "role": self.role.value, "description": self.description,
                "dependencies": list(self.dependencies), "inputs": json_copy(self.inputs)}


@dataclass(frozen=True)
class Plan:
    requirement: str
    tasks: tuple[Task, ...]

    def __post_init__(self):
        nonempty_text(self.requirement, "plan.requirement")
        if not isinstance(self.tasks, (list, tuple)) or not self.tasks or len(self.tasks) > 1000:
            raise ValidationError("A plan must contain between 1 and 1000 tasks")
        if any(not isinstance(task, Task) for task in self.tasks):
            raise ValidationError("plan.tasks must contain Task objects")
        object.__setattr__(self, "tasks", tuple(self.tasks))
        lookup = {task.id: task for task in self.tasks}
        if len(lookup) != len(self.tasks):
            raise ValidationError("Task ids must be unique")
        if len({task.id.casefold() for task in self.tasks}) != len(self.tasks):
            raise ValidationError("Task ids must also be unique on case-insensitive filesystems")
        remaining = {task.id: set(task.dependencies) for task in self.tasks}
        if any(deps - lookup.keys() for deps in remaining.values()):
            raise ValidationError("Plan contains an unknown dependency")
        while remaining:
            ready = {key for key, deps in remaining.items() if not deps}
            if not ready:
                raise ValidationError("Plan contains a dependency cycle")
            remaining = {key: deps - ready for key, deps in remaining.items() if key not in ready}

    @classmethod
    def from_dict(cls, value: dict) -> Plan:
        value = require_object(value, "plan")
        fields(value, {"requirement", "tasks"}, set(), "plan")
        if not isinstance(value["tasks"], list):
            raise ValidationError("plan.tasks must be an array")
        return cls(value["requirement"], tuple(Task.from_dict(t) for t in value["tasks"]))

    def to_dict(self) -> dict:
        return {"requirement": self.requirement, "tasks": [task.to_dict() for task in self.tasks]}


def validate_cadquery_source(source: Any) -> None:
    source = nonempty_text(source, "cadquery_code")
    if len(source) > 2_000_000:
        raise ValidationError("CadQuery source exceeds the size limit")
    try:
        tree = ast.parse(source)
        # Parsing alone accepts constructs such as a return outside a function.
        # Compilation checks Python contexts without executing any source code.
        compile(tree, "<cadquery-result>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError, RecursionError):
        raise ValidationError("CadQuery source must be valid Python") from None
    if not any(
        (isinstance(node, ast.Import) and any(alias.name == "cadquery" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "cadquery")
        for node in tree.body
    ):
        raise ValidationError("Source must import CadQuery at module level")
    result_value = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "result" for t in node.targets):
            result_value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "result":
            if node.value is not None:
                result_value = node.value
        elif isinstance(node, ast.Delete) and any(isinstance(t, ast.Name) and t.id == "result" for t in node.targets):
            result_value = None
    if result_value is None:
        raise ValidationError("Source must assign 'result' explicitly at module level")
    if isinstance(result_value, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict,
                                 ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        raise ValidationError("The result must be a CadQuery object, not a literal or container")


def validate_payload(role: Role, payload: Any) -> dict:
    value = json_copy(require_object(payload, "result"))
    if role == Role.PD:
        fields(value, {"summary", "parameters", "units"}, {"constraints", "assumptions"}, "PD result")
        if not require_object(value["parameters"], "parameters"):
            raise ValidationError("PD parameters cannot be empty")
        if not require_object(value["units"], "units"):
            raise ValidationError("PD units cannot be empty")
        for unit in value["units"].values():
            nonempty_text(unit, "unit")
        for name in ("constraints", "assumptions"):
            if name in value and (not isinstance(value[name], list) or any(not isinstance(s, str) for s in value[name])):
                raise ValidationError(f"{name} must be an array of strings")
    else:
        required = {"summary", "cadquery_code"}
        if role == Role.PA:
            required.add("components")
        fields(value, required, set(), f"{role.value} result")
        validate_cadquery_source(value["cadquery_code"])
        if role == Role.PA:
            components = value["components"]
            if not isinstance(components, list) or not components:
                raise ValidationError("PA components must be a nonempty array")
            for component in components:
                identifier(component, "component")
            if len(components) != len(set(components)):
                raise ValidationError("PA components must be unique")
    nonempty_text(value["summary"], "summary")
    return value


@dataclass(frozen=True)
class ExecutionResult:
    payload: dict
    quality: float | None = None
    total_tokens: int | None = None

    def validated(self, role: Role) -> ExecutionResult:
        quality = None if self.quality is None else number(self.quality, "quality", 0, 1)
        if self.total_tokens is not None and (type(self.total_tokens) is not int or self.total_tokens < 0):
            raise ValidationError("total_tokens must be a nonnegative integer or null")
        return ExecutionResult(validate_payload(role, self.payload), quality, self.total_tokens)

    def to_dict(self) -> dict:
        return {"payload": json_copy(self.payload), "quality": self.quality, "total_tokens": self.total_tokens}


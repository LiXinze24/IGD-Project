"""Local TP, PD and PM example for a parameterized gear shaft."""

from ..coordination import LocalCoordinator
from ..errors import BackendError
from ..geometry import DEFAULT_PARAMETERS, create_demo_geometry, gear_shaft_source, validate_parameters
from ..models import ExecutionResult, Plan, Role, Task

DEMO_REQUIREMENT = "Design a four-segment gear shaft with a rounded-profile tooth array and a keyway, using the supplied dimensions in millimetres."


def demo_plan(parameters: dict | None = None, requirement: str = DEMO_REQUIREMENT) -> Plan:
    p = validate_parameters(DEFAULT_PARAMETERS if parameters is None else parameters)
    return Plan(requirement, (
        Task("design", Role.PD, "Establish and validate the gear-shaft dimensions.", inputs=p),
        Task("gear_shaft", Role.PM, "Model the four shaft segments, tooth array and keyway in CadQuery.",
             ("design",), {"part": "gear_shaft"}),
    ))


def demo_sources(parameters: dict) -> dict[str, str]:
    return {"gear_shaft": gear_shaft_source(parameters)}


class DemoBackend(LocalCoordinator):
    name = "demo"

    def __init__(self, parameters: dict | None = None):
        self.parameters = validate_parameters(DEFAULT_PARAMETERS if parameters is None else parameters)

    def plan(self, requirement: str) -> tuple[Plan, int]:
        return demo_plan(self.parameters, requirement), 0

    def execute(self, task: Task, dependency_results: dict[str, dict]) -> ExecutionResult:
        if task.id == "design" and task.role == Role.PD:
            p = validate_parameters(task.inputs)
            return ExecutionResult({
                "summary": "Validated dimensions for a four-segment gear shaft with teeth and a keyway.",
                "parameters": p,
                "units": {"length": "mm"},
                "constraints": ["Contiguous coaxial shaft segments", "Gear face within segment 2",
                                "Keyway within segment 4", "Distinct teeth in a circular array"],
                "assumptions": ["Rounded-slot tooth profile", "Nominal geometry with no loading or tolerances"],
            }, total_tokens=0)
        if task.id == "gear_shaft" and task.role == Role.PM and task.inputs == {"part": "gear_shaft"}:
            if "design" not in dependency_results:
                raise BackendError("invalid_demo_task", "The example requires its design predecessor")
            p = validate_parameters(dependency_results["design"]["parameters"])
            return ExecutionResult({"summary": "CadQuery gear shaft with four steps, teeth and a keyway.",
                                    "cadquery_code": gear_shaft_source(p)}, total_tokens=0)
        raise BackendError("invalid_demo_task", "Task is outside the gear-shaft example profile")

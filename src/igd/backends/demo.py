"""Local TP, PD and PM example for a stepped shaft with two keyways."""

from ..coordination import LocalCoordinator
from ..errors import BackendError
from ..geometry import DEFAULT_PARAMETERS, create_demo_geometry, shaft_source, validate_parameters
from ..models import ExecutionResult, Plan, Role, Task

DEMO_REQUIREMENT = "Design a four-segment stepped shaft with two keyways, using the supplied dimensions in millimetres."


def demo_plan(parameters: dict | None = None, requirement: str = DEMO_REQUIREMENT) -> Plan:
    p = validate_parameters(DEFAULT_PARAMETERS if parameters is None else parameters)
    return Plan(requirement, (
        Task("design", Role.PD, "Establish and validate the shaft dimensions and keyway positions.", inputs=p),
        Task("shaft", Role.PM, "Model the four shaft segments and two keyways in CadQuery.",
             ("design",), {"part": "shaft"}),
    ))


def demo_sources(parameters: dict) -> dict[str, str]:
    return {"shaft": shaft_source(parameters)}


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
                "summary": "Validated dimensions for a four-segment stepped shaft with two keyways.",
                "parameters": p,
                "units": {"length": "mm"},
                "constraints": ["Contiguous coaxial shaft segments",
                                "Each keyway stays within its designated segment",
                                "Material retained at both ends of each keyway"],
                "assumptions": ["Nominal geometry with no loading or tolerances"],
            }, total_tokens=0)
        if task.id == "shaft" and task.role == Role.PM and task.inputs == {"part": "shaft"}:
            if "design" not in dependency_results:
                raise BackendError("invalid_demo_task", "The example requires its design predecessor")
            p = validate_parameters(dependency_results["design"]["parameters"])
            return ExecutionResult({"summary": "CadQuery stepped shaft with four segments and two keyways.",
                                    "cadquery_code": shaft_source(p)}, total_tokens=0)
        raise BackendError("invalid_demo_task", "Task is outside the shaft example profile")

"""Public TP coordination contract and deterministic local policy."""

from dataclasses import dataclass

from .errors import ValidationError
from .models import fields, identifier, json_copy, nonempty_text, require_object


@dataclass(frozen=True)
class CoordinationResult:
    action: str
    summary: str
    task_id: str | None = None
    agent_id: str | None = None
    total_tokens: int | None = None

    def __post_init__(self):
        if self.action not in {"dispatch", "finish", "abort"}:
            raise ValidationError("TP action must be dispatch, finish or abort")
        nonempty_text(self.summary, "TP summary")
        if len(self.summary) > 4000:
            raise ValidationError("TP summary exceeds the size limit")
        if self.action == "dispatch":
            identifier(self.task_id, "TP task_id")
            identifier(self.agent_id, "TP agent_id")
        elif self.task_id is not None or self.agent_id is not None:
            raise ValidationError("Only dispatch decisions may identify a task and agent")
        if self.total_tokens is not None and (type(self.total_tokens) is not int or self.total_tokens < 0):
            raise ValidationError("TP total_tokens must be nonnegative or null")

    @classmethod
    def from_dict(cls, value: dict, total_tokens: int | None = None):
        value = json_copy(require_object(value, "TP decision"))
        fields(value, {"action", "summary"}, {"task_id", "agent_id"}, "TP decision")
        return cls(**value, total_tokens=total_tokens)

    def to_dict(self):
        value = {"action": self.action, "summary": self.summary}
        if self.action == "dispatch":
            value.update(task_id=self.task_id, agent_id=self.agent_id)
        return value


class LocalCoordinator:
    """Deterministic TP policy for local runs and custom Python backends."""

    name = "local"

    def coordinate(self, state: dict) -> CoordinationResult:
        if state["all_tasks_succeeded"]:
            return CoordinationResult("finish", "All planned tasks completed successfully.", total_tokens=0)
        selection = state["selection"]
        if selection is None:
            return CoordinationResult("abort", "No executable task remains.", total_tokens=0)
        return CoordinationResult("dispatch", "Dispatch the ACBAC-selected task and executor.",
                                  **selection, total_tokens=0)

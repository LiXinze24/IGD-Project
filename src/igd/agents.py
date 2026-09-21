"""Functional agents and their public capability declarations."""

from dataclasses import dataclass, field
from typing import Protocol

from .acbac import Proposal
from .models import ExecutionResult, Role, Task, identifier, number
from .errors import ValidationError


class Backend(Protocol):
    name: str

    def execute(self, task: Task, dependency_results: dict[str, dict]) -> ExecutionResult: ...


@dataclass(frozen=True)
class Agent:
    id: str
    capabilities: dict[Role, float]
    backend: Backend = field(repr=False)
    confidence: float = 1.0
    plan_score: float = 1.0
    estimated_seconds: float = 60.0

    def __post_init__(self):
        identifier(self.id, "agent id")
        if not isinstance(self.capabilities, dict) or not self.capabilities:
            raise ValidationError("An agent must declare at least one capability")
        try:
            caps = {Role(role): number(score, "capability", 0, 1) for role, score in self.capabilities.items()}
        except (TypeError, ValueError):
            raise ValidationError("Invalid agent capabilities") from None
        object.__setattr__(self, "capabilities", caps)
        number(self.confidence, "confidence", 0, 1)
        number(self.plan_score, "plan_score", 0, 1)
        if number(self.estimated_seconds, "estimated_seconds") <= 0:
            raise ValidationError("estimated_seconds must be positive")

    def propose(self, task: Task, history: float) -> Proposal | None:
        match = self.capabilities.get(task.role, 0.0)
        if match <= 0:
            return None
        return Proposal(self.id, task.id, self.confidence, history, self.plan_score,
                        match, self.estimated_seconds)


class PDAgent(Agent):
    def __init__(self, backend: Backend, id: str = "pd-agent", **kwargs):
        super().__init__(id, {Role.PD: 1.0}, backend, **kwargs)


class PMAgent(Agent):
    def __init__(self, backend: Backend, id: str = "pm-agent", **kwargs):
        super().__init__(id, {Role.PM: 1.0}, backend, **kwargs)


class PAAgent(Agent):
    def __init__(self, backend: Backend, id: str = "pa-agent", **kwargs):
        super().__init__(id, {Role.PA: 1.0}, backend, **kwargs)


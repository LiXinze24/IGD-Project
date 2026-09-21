"""ACBAC proposal aggregation and feedback equations.

Policy defaults (weights, time normalisation and sampling) are explicit runtime
choices. They are configurable and are not fitted experimental parameters.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .errors import ValidationError
from .models import identifier, number


INDICATORS = ("confidence", "history", "plan", "capability", "time")


@dataclass(frozen=True)
class ACBACConfig:
    evaporation: float = 0.2
    reinforcement: float = 1.0
    initial_pheromone: float = 1.0
    weights: dict[str, float] = field(default_factory=lambda: {key: 0.2 for key in INDICATORS})

    def __post_init__(self):
        number(self.evaporation, "evaporation", 0, 1)
        number(self.reinforcement, "reinforcement", 0, 1e6)
        number(self.initial_pheromone, "initial_pheromone", 0, 1e6)
        if not isinstance(self.weights, dict) or set(self.weights) != set(INDICATORS):
            raise ValidationError("weights must specify confidence, history, plan, capability and time")
        weights = {key: number(self.weights[key], f"weight.{key}", 0, 1) for key in INDICATORS}
        if not math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9):
            raise ValidationError("Proposal weights must sum to one")
        object.__setattr__(self, "weights", weights)


@dataclass(frozen=True)
class Proposal:
    agent_id: str
    task_id: str
    confidence: float
    history: float
    plan: float
    capability: float
    estimated_seconds: float

    def __post_init__(self):
        identifier(self.agent_id, "agent id")
        identifier(self.task_id, "task id")
        for name in INDICATORS[:-1]:
            number(getattr(self, name), name, 0, 1)
        if number(self.estimated_seconds, "estimated_seconds") <= 0:
            raise ValidationError("estimated_seconds must be positive")


@dataclass(frozen=True)
class ScoredProposal:
    proposal: Proposal
    time_score: float
    strength: float


class PheromoneField:
    def __init__(self, task_ids, config: ACBACConfig | None = None):
        self.config = config or ACBACConfig()
        ids = list(task_ids)
        if len(ids) != len(set(ids)):
            raise ValidationError("Duplicate pheromone node")
        self.values = {identifier(task_id): self.config.initial_pheromone for task_id in ids}

    def score(self, proposals: list[Proposal]) -> list[ScoredProposal]:
        if not proposals:
            return []
        if len({p.task_id for p in proposals}) != 1:
            raise ValidationError("Score proposals for one task at a time")
        if proposals[0].task_id not in self.values:
            raise ValidationError("Unknown pheromone node")
        if len({p.agent_id for p in proposals}) != len(proposals):
            raise ValidationError("An agent may submit only one proposal per task")
        fastest = min(p.estimated_seconds for p in proposals)
        scored = []
        for proposal in sorted(proposals, key=lambda p: p.agent_id):
            time_score = fastest / proposal.estimated_seconds
            indicators = {key: getattr(proposal, key) for key in INDICATORS[:-1]}
            indicators["time"] = time_score
            strength = sum(self.config.weights[key] * indicators[key] for key in INDICATORS)
            scored.append(ScoredProposal(proposal, time_score, strength))
        return scored

    def concentration(self, task_id: str, proposals: list[ScoredProposal]) -> float:
        if task_id not in self.values or any(p.proposal.task_id != task_id for p in proposals):
            raise ValidationError("Proposal task does not match pheromone node")
        # Re-evaluation replaces the proposal contribution; it never accumulates duplicates.
        return self.values[task_id] + sum(p.strength for p in proposals)

    def feedback(self, task_id: str, succeeded: bool, quality: float | None) -> float:
        if task_id not in self.values:
            raise ValidationError("Unknown pheromone node")
        if quality is not None:
            number(quality, "quality", 0, 1)
        score = quality if succeeded and quality is not None else 0.0
        self.values[task_id] = ((1 - self.config.evaporation) * self.values[task_id]
                                + self.config.reinforcement * score)
        return self.values[task_id]


def weighted_choice(items: list, weights: list[float], rng: random.Random):
    if not items or len(items) != len(weights):
        raise ValidationError("Invalid weighted selection")
    for weight in weights:
        number(weight, "selection weight")
    total = sum(weights)
    if not math.isfinite(total):
        raise ValidationError("Selection weights overflow")
    if total <= 0:
        raise ValidationError("Selection requires positive proposal strength")
    threshold = rng.random() * total
    cumulative = 0.0
    for item, weight in zip(items, weights):
        cumulative += weight
        if threshold < cumulative:
            return item
    # A subnormal total can round the threshold up to total. Never choose
    # a trailing zero-weight item when handling that floating-point boundary.
    return next(item for item, weight in reversed(list(zip(items, weights))) if weight > 0)


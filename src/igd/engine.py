"""TP orchestration: task proposals, dispatch, returned results and feedback."""

from __future__ import annotations

import random
import time
from dataclasses import asdict

from .acbac import ACBACConfig, PheromoneField, weighted_choice
from .agents import Agent
from .coordination import CoordinationResult, LocalCoordinator
from .errors import BackendError, ValidationError
from .models import ExecutionResult, Plan, Role, Status, json_copy


class TPAgent:
    """Coordinate one task list through ACBAC and repeated TP decisions.

    The shared field calculates proposal strengths and samples a dispatch.
    The TP backend receives this selection, prior results and the current field
    on every round. It authorizes dispatch, terminates with a summary, or aborts.
    """

    def __init__(self, agents: list[Agent], config: ACBACConfig | None = None,
                 seed: int = 0, coordinator=None, max_rounds: int = 1001):
        if not agents or len({a.id for a in agents}) != len(agents):
            raise ValidationError("Register agents with unique ids")
        if type(max_rounds) is not int or not 1 <= max_rounds <= 10001:
            raise ValidationError("max_rounds must be an integer from 1 to 10001")
        self.agents = {agent.id: agent for agent in agents}
        self.config = config or ACBACConfig()
        self.seed = seed
        self.coordinator = coordinator if coordinator is not None else LocalCoordinator()
        self.max_rounds = max_rounds

    def run(self, plan: Plan) -> dict:
        rng = random.Random(self.seed)
        field = PheromoneField((task.id for task in plan.tasks), self.config)
        tasks = {task.id: task for task in plan.tasks}
        records = {task.id: {"role": task.role.value, "status": Status.PENDING.value,
                            "agent_id": None, "result": None, "error_code": None,
                            "elapsed_seconds": None} for task in plan.tasks}
        history: dict[tuple[str, Role], tuple[int, int]] = {}
        events, tp_calls = [], []
        last_execution = None
        final_summary = None
        run_error = None
        finished = False
        started = time.perf_counter()

        def event(kind: str, task_id: str | None = None, **data):
            events.append({"sequence": len(events), "event": kind, "task_id": task_id, **data})

        def propagate_failures():
            changed = True
            while changed:
                changed = False
                for task in plan.tasks:
                    if records[task.id]["status"] == Status.PENDING.value and any(
                        records[dep]["status"] in {Status.FAILED.value, Status.BLOCKED.value}
                        for dep in task.dependencies
                    ):
                        records[task.id].update(status=Status.BLOCKED.value, error_code="dependency_failed")
                        event("blocked", task.id, error_code="dependency_failed")
                        changed = True


        for round_number in range(1, self.max_rounds + 1):
            propagate_failures()

            ready = sorted(task.id for task in plan.tasks
                           if records[task.id]["status"] == Status.PENDING.value
                           and all(records[d]["status"] == Status.SUCCEEDED.value for d in task.dependencies))
            candidates = {}
            proposal_packets = []
            for task_id in ready:
                task = tasks[task_id]
                proposals = []
                for agent in self.agents.values():
                    successes, attempts = history.get((agent.id, task.role), (0, 0))
                    proposal = agent.propose(task, (successes + 1) / (attempts + 2))
                    if proposal is not None:
                        proposals.append(proposal)
                scored = [p for p in field.score(proposals) if p.strength > 0]
                if not scored:
                    records[task_id].update(status=Status.FAILED.value, error_code="no_eligible_agent")
                    tau = field.feedback(task_id, False, None)
                    event("failed", task_id, error_code="no_eligible_agent", pheromone=tau)
                    continue
                candidates[task_id] = scored
                for proposal in scored:
                    packet = asdict(proposal.proposal)
                    packet.update(time_score=proposal.time_score, strength=proposal.strength,
                                  concentration=field.concentration(task_id, scored))
                    proposal_packets.append(packet)

            propagate_failures()
            selection = None
            if candidates:
                ids = sorted(candidates)
                task_id = weighted_choice(ids, [field.concentration(t, candidates[t]) for t in ids], rng)
                selected = weighted_choice(candidates[task_id], [p.strength for p in candidates[task_id]], rng)
                selection = {"task_id": task_id, "agent_id": selected.proposal.agent_id}

            all_succeeded = all(r["status"] == Status.SUCCEEDED.value for r in records.values())
            state = {
                "round": round_number, "plan": plan.to_dict(),
                "task_states": {key: {"status": r["status"], "role": r["role"], "error_code": r["error_code"]}
                                for key, r in records.items()},
                "results": {key: json_copy(r["result"]["payload"]) for key, r in records.items()
                            if r["result"] is not None},
                "last_execution": json_copy(last_execution),
                "pheromones": dict(field.values), "proposals": proposal_packets,
                "selection": selection, "all_tasks_succeeded": all_succeeded,
            }
            call = {"phase": "coordinate", "round": round_number, "decision": None,
                    "error_code": None, "total_tokens": None,
                    "task_statuses": {key: r["status"] for key, r in records.items()},
                    "result_task_ids": sorted(state["results"]), "last_execution": json_copy(last_execution),
                    "selection": json_copy(selection), "pheromones": dict(field.values)}
            call_started = time.perf_counter()
            decision = None
            try:
                decision = self.coordinator.coordinate(json_copy(state))
                if not isinstance(decision, CoordinationResult):
                    raise ValidationError("TP must return CoordinationResult")
                call["total_tokens"] = decision.total_tokens
                if decision.action == "finish" and not all_succeeded:
                    raise ValidationError("TP cannot finish while tasks remain incomplete")
                if decision.action == "dispatch":
                    if selection is None or {"task_id": decision.task_id, "agent_id": decision.agent_id} != selection:
                        raise ValidationError("TP dispatch must match the current ACBAC selection")
                call["decision"] = decision.to_dict()
            except BackendError as error:
                run_error = error.code
            except ValidationError:
                run_error = "invalid_tp_decision"
            except Exception:
                run_error = "tp_exception"
            finally:
                call["elapsed_seconds"] = time.perf_counter() - call_started
                call["error_code"] = run_error
                tp_calls.append(call)
            if run_error:
                event("tp_failed", error_code=run_error, round=round_number)
                break
            event("tp_decision", decision.task_id, round=round_number, action=decision.action,
                  agent_id=decision.agent_id)
            if decision.action == "finish":
                final_summary = decision.summary
                finished = True
                break
            if decision.action == "abort":
                run_error = "tp_aborted"
                final_summary = decision.summary
                break

            task_id = decision.task_id
            task = tasks[task_id]
            agent = self.agents[decision.agent_id]
            scored = candidates[task_id]
            for proposal in scored:
                packet = asdict(proposal.proposal)
                packet.pop("task_id")
                event("proposal", task_id, **packet, time_score=proposal.time_score, strength=proposal.strength)
            record = records[task_id]
            record.update(status=Status.RUNNING.value, agent_id=agent.id)
            event("started", task_id, agent_id=agent.id, concentration=field.concentration(task_id, scored))
            dependencies = {dep: json_copy(records[dep]["result"]["payload"]) for dep in task.dependencies}
            task_started = time.perf_counter()
            result = None
            try:
                result = agent.backend.execute(type(task).from_dict(task.to_dict()), dependencies)
                if not isinstance(result, ExecutionResult):
                    raise ValidationError("Backend must return ExecutionResult")
                result = result.validated(task.role)
                if task.role == Role.PA:
                    model_ids = {dep for dep in task.dependencies if tasks[dep].role == Role.PM}
                    if set(result.payload["components"]) != model_ids:
                        raise ValidationError("Assembly components must match its direct PM dependencies")
                record.update(status=Status.SUCCEEDED.value, result=result.to_dict())
            except BackendError as error:
                record.update(status=Status.FAILED.value, error_code=error.code)
            except ValidationError:
                record.update(status=Status.FAILED.value, error_code="invalid_result")
            except Exception:
                record.update(status=Status.FAILED.value, error_code="backend_exception")
            finally:
                record["elapsed_seconds"] = time.perf_counter() - task_started
            succeeded = record["status"] == Status.SUCCEEDED.value
            successes, attempts = history.get((agent.id, task.role), (0, 0))
            history[(agent.id, task.role)] = (successes + int(succeeded), attempts + 1)
            quality = result.quality if succeeded else None
            tau = field.feedback(task_id, succeeded, quality)
            last_execution = {"task_id": task_id, "status": record["status"],
                              "error_code": record["error_code"], "quality": quality}
            event(record["status"], task_id, agent_id=agent.id,
                  error_code=record["error_code"], quality=quality, pheromone=tau)
        else:
            run_error = "round_limit"

        propagate_failures()
        if not finished:
            for task_id, record in records.items():
                if record["status"] == Status.PENDING.value:
                    record.update(status=Status.CANCELLED.value, error_code="tp_stopped")
                    event("cancelled", task_id, error_code="tp_stopped")

        tokens = [r["result"]["total_tokens"] for r in records.values() if r["result"] is not None]
        known_tokens = [value for value in tokens if value is not None]
        executed = [r for r in records.values() if r["elapsed_seconds"] is not None]
        tp_tokens = [c["total_tokens"] for c in tp_calls if c["total_tokens"] is not None]
        return {
            "schema_version": 2, "status": "succeeded" if finished else "failed",
            "seed": self.seed, "scheduler": asdict(self.config), "plan": plan.to_dict(),
            "tasks": records, "events": events, "pheromones": dict(field.values),
            "tp": {"backend": self.coordinator.name, "calls": tp_calls, "summary": final_summary,
                   "error_code": run_error, "max_rounds": self.max_rounds},
            "metrics": {
                "elapsed_seconds": time.perf_counter() - started,
                "known_execution_tokens": sum(known_tokens),
                "execution_token_usage_complete": len(known_tokens) == len(executed),
                "executed_tasks": len(executed),
                "known_tp_tokens": sum(tp_tokens),
                "tp_token_usage_complete": len(tp_tokens) == len(tp_calls),
                "tp_call_count": len(tp_calls),
            },
        }

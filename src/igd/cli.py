"""Command-line entry points for local examples and Dify-backed runs."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .acbac import ACBACConfig
from .agents import PAAgent, PDAgent, PMAgent
from .artifacts import prepare_output_root, save_report
from .backends.demo import DEMO_REQUIREMENT, DemoBackend
from .backends.dify import DifyBackend
from .config import load_environment
from .engine import TPAgent
from .errors import BackendError, ValidationError
from .models import Plan, fields, loads_json, require_object


def read_json(path: Path):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValidationError("Input document exceeds the size limit")
    return loads_json(path.read_text(encoding="utf-8-sig"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="igd", description="Intelligent Generative Design coordination runtime")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run the local shaft example with TP coordination")
    demo.add_argument("--parameters", type=Path, help="JSON dimensions for the stepped shaft")
    run = commands.add_parser("run", help="Plan and coordinate tasks through private Dify workflows")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", type=Path, help="A saved initial task plan; TP still coordinates execution")
    source.add_argument("--requirement", help="Design requirement for TP to decompose")
    run.add_argument("--env-file", type=Path, help="Explicit environment file; shell variables take precedence")
    validate = commands.add_parser("validate-plan", help="Validate roles, ids and task dependencies")
    validate.add_argument("path", type=Path)
    for command in (demo, run):
        command.add_argument("--output", type=Path, default=Path("outputs"), help="Parent directory for a fresh run")
        command.add_argument("--seed", type=int, default=0, help="Seed for ACBAC sampling")
        command.add_argument("--scheduler-config", type=Path, help="JSON override of ACBAC settings")
        command.add_argument("--max-rounds", type=int, default=1001, help="Maximum TP coordination rounds")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate-plan":
            plan = Plan.from_dict(read_json(args.path))
            print(f"Valid plan: {len(plan.tasks)} tasks.")
            return 0
        if not 1 <= args.max_rounds <= 10001:
            raise ValidationError("max-rounds must be between 1 and 10001")
        config = ACBACConfig()
        if args.scheduler_config is not None:
            data = require_object(read_json(args.scheduler_config), "scheduler config")
            fields(data, set(), {"evaporation", "reinforcement", "initial_pheromone", "weights"}, "scheduler config")
            config = ACBACConfig(**data)
        args.output = prepare_output_root(args.output)
        planning = {"source": "file", "elapsed_seconds": 0.0, "total_tokens": 0, "called": False}
        if args.command == "demo":
            parameters = read_json(args.parameters) if args.parameters else None
            backend = DemoBackend(parameters)
            started = time.perf_counter()
            plan, tokens = backend.plan(DEMO_REQUIREMENT)
            planning = {"source": "demo", "elapsed_seconds": time.perf_counter() - started,
                        "total_tokens": tokens, "called": True}
        else:
            environment = load_environment(args.env_file)
            if args.plan is not None:
                plan = Plan.from_dict(read_json(args.plan))
            else:
                if not args.requirement.strip():
                    raise ValidationError("Requirement cannot be blank")
                planner = DifyBackend.from_environment(environment, {"TP"})
                started = time.perf_counter()
                plan, tokens = planner.plan(args.requirement)
                planning = {"source": "dify", "elapsed_seconds": time.perf_counter() - started,
                            "total_tokens": tokens, "called": True}
            roles = {task.role.value for task in plan.tasks} | {"TP"}
            backend = DifyBackend.from_environment(environment, roles)
        agents = [PDAgent(backend), PMAgent(backend), PAAgent(backend)]
        report = TPAgent(agents, config, args.seed, coordinator=backend, max_rounds=args.max_rounds).run(plan)
        report.update(version=__version__, backend=backend.name, planning=planning)
        report["metrics"]["known_tp_tokens"] += planning["total_tokens"] or 0
        report["metrics"]["tp_token_usage_complete"] &= planning["total_tokens"] is not None
        report["metrics"]["tp_call_count"] += int(planning["called"])
        report["metrics"]["known_total_tokens"] = (report["metrics"]["known_execution_tokens"]
                                                    + report["metrics"]["known_tp_tokens"])
        report["metrics"]["total_token_usage_complete"] = (report["metrics"]["execution_token_usage_complete"]
                                                           and report["metrics"]["tp_token_usage_complete"])
        report["tp"]["calls"].insert(0, {"phase": "plan" if planning["called"] else "load_plan",
                                        "round": 0, **planning, "task_ids": [t.id for t in plan.tasks]})
        directory = save_report(report, args.output)
        completed = sum(record["status"] == "succeeded" for record in report["tasks"].values())
        print(f"{report['status'].capitalize()}: {completed}/{len(plan.tasks)} tasks. Results: {directory}")
        if report["status"] != "succeeded":
            if report["tp"]["error_code"]:
                print(f"  TP: {report['tp']['error_code']}", file=sys.stderr)
            for task_id, record in report["tasks"].items():
                if record["error_code"]:
                    print(f"  {task_id}: {record['error_code']}", file=sys.stderr)
            return 1
        return 0
    except (ValidationError, BackendError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("Error: unable to read an input file or write the output directory.", file=sys.stderr)
        return 2

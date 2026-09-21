# Workflow contracts

Requests and outputs use finite JSON values and UTF-8-encodable Unicode strings. Duplicate keys, NaN and infinity are rejected. Schemas describe the external shape; runtime validation additionally enforces dependencies, role contracts and source compilation.

## Initial task plan

See [plan.schema.json](../schemas/plan.schema.json) and [gear_shaft_plan.json](../examples/gear_shaft_plan.json).

```json
{
  "requirement": "Design a gear shaft.",
  "tasks": [
    {
      "id": "design",
      "role": "PD",
      "description": "Determine the gear-shaft dimensions.",
      "dependencies": [],
      "inputs": {"length_unit": "mm"}
    },
    {
      "id": "gear_shaft",
      "role": "PM",
      "description": "Model the gear shaft from the design result.",
      "dependencies": ["design"],
      "inputs": {"part": "gear_shaft"}
    }
  ]
}
```

A plan contains 1-1000 tasks. IDs start with a letter and contain at most 64 letters, digits, underscores or hyphens. IDs must be unique even on case-insensitive filesystems, and Windows reserved device names are rejected.

A JSON plan is the serialization of the initial task list. Structural validation produces the executable task graph; it does not establish design or geometric correctness.

## TP initialization

The TP input uses schema version 2 and a phase:

```json
{
  "schema_version": 2,
  "role": "TP",
  "phase": "plan",
  "requirement": "Design a gear shaft."
}
```

The workflow returns the plan object above directly as its `result`, preserving the requirement exactly. A saved `--plan` file supplies this initial object without the initial planning call.

## TP coordination

Before every functional dispatch and after the final functional result, TP receives:

```json
{
  "schema_version": 2,
  "role": "TP",
  "phase": "coordinate",
  "state": {
    "round": 1,
    "plan": {
      "requirement": "Determine gear-shaft dimensions.",
      "tasks": [
        {
          "id": "design",
          "role": "PD",
          "description": "Determine dimensions.",
          "dependencies": [],
          "inputs": {}
        }
      ]
    },
    "task_states": {
      "design": {"status": "pending", "role": "PD", "error_code": null}
    },
    "results": {},
    "last_execution": null,
    "pheromones": {"design": 1.0},
    "proposals": [
      {
        "agent_id": "pd-agent",
        "task_id": "design",
        "confidence": 1.0,
        "history": 0.5,
        "plan": 1.0,
        "capability": 1.0,
        "estimated_seconds": 60.0,
        "time_score": 1.0,
        "strength": 0.9,
        "concentration": 1.9
      }
    ],
    "selection": {"task_id": "design", "agent_id": "pd-agent"},
    "all_tasks_succeeded": false
  }
}
```

`results` contains all available validated task payloads keyed by task ID. Subsequent rounds include the prior execution's status, error category and optional quality in `last_execution`. Scores and field values reflect the current round. An empty candidate set produces `selection: null`.

The TP response follows [tp-decision.schema.json](../schemas/tp-decision.schema.json). To dispatch:

```json
{
  "action": "dispatch",
  "task_id": "design",
  "agent_id": "pd-agent",
  "summary": "Proceed with design analysis using the current ACBAC selection."
}
```

The task and agent must exactly match `state.selection`. The runtime computes the ACBAC selection; TP coordinates it using the current results and state.

After all tasks succeed:

```json
{
  "action": "finish",
  "summary": "The gear-shaft design parameters and CadQuery model are complete."
}
```

TP may return `{"action": "abort", "summary": "..."}` to stop with a failed run. A summary must contain 1-4000 characters and non-whitespace text. A premature finish or invalid dispatch fails the run. Only an accepted finish completes a run. Each TP response is a coordination decision, not a replacement task graph.

No internal prompts are part of this contract. TP can consume the state within its private workflow and expose only the decision object.

## Functional request

PD, PM and PA retain schema version 1. Each receives the task and full validated results of its direct dependencies:

```json
{
  "schema_version": 1,
  "role": "PM",
  "task": {
    "id": "gear_shaft",
    "role": "PM",
    "description": "Model the gear shaft from its dimensions.",
    "dependencies": ["design"],
    "inputs": {"part": "gear_shaft"}
  },
  "dependency_results": {
    "design": {
      "summary": "Nominal gear-shaft dimensions.",
      "parameters": {
        "segment_diameters_mm": [40, 57, 40, 32],
        "segment_lengths_mm": [25, 48, 55, 60],
        "tooth_count": 25
      },
      "units": {"length": "mm"}
    }
  }
}
```

## Functional response

The workflow's `result` contains required `payload` and optional `quality`. The adapter reads token usage from Dify's `data.total_tokens`. See [result.schema.json](../schemas/result.schema.json).

| Role | Required payload fields | Optional fields |
| --- | --- | --- |
| PD | Nonempty `summary`, `parameters`, `units` | String arrays `constraints`, `assumptions` |
| PM | Nonempty `summary`, `cadquery_code` | None |
| PA | Nonempty `summary`, `cadquery_code`, `components` | None |

PD units are nonempty strings. PM and PA source imports CadQuery at module level and explicitly assigns `result` at module level. Helper functions are supported, followed by `result = build_part()`. Literal/container results and a result defined only inside a helper are rejected.

PM should return a CadQuery shape or workplane; PA should return an assembly. PA component IDs must exactly match its direct PM dependencies. The runtime parses and compiles source without executing it, so geometric validity requires a separate CAD check.

A quality value is a finite normalized backend assessment in `[0,1]`. Leave it `null` when no such assessment exists. Successful HTTP transport or Python compilation does not imply engineering quality. Unknown result fields are rejected.

## Failures and reports

Non-successful Dify status, malformed output, missing fields, incompatible payloads and transport failures are failures. Stable error categories are saved; raw HTTP bodies are not.

A failed functional task blocks its descendants. Independent work can continue under TP coordination. A failed TP call, rejected decision, abort or round limit stops dispatch and cancels pending tasks. Completed outputs remain in the report.

Output storage is checked before workflow calls. Configuration and initial planning failures return exit code 2. Once graph execution begins, failures return code 1 and save a report when storage remains available.

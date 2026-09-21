# Dify integration

IGD connects to published Dify **Workflow** applications through their Service API. TP, PD, PM and PA use separate application keys. Internal prompts, knowledge retrieval, model choices and refinement logic remain in the private deployment.

## Workflow boundary

1. Expose one text input variable named `request`.
2. Decode the JSON string and map it into the private workflow.
3. Return a variable named `result` with the appropriate object from [Contracts](contracts.md). A JSON-encoded string is also accepted.
4. Publish the workflow and configure its application key.

TP accepts two phases using the same application: `plan` for initial requirement decomposition, and `coordinate` for ongoing task coordination. Functional workflows accept their task and dependency results as before.

Existing deployments can map their own internal fields at this boundary. The public package requires no workflow export or internal prompt.

## Environment settings

| Variable | Meaning |
| --- | --- |
| `IGD_DIFY_BASE_URL` | API base, default `https://api.dify.ai/v1` |
| `IGD_DIFY_TP_API_KEY` | TP Workflow key, used for planning and coordination |
| `IGD_DIFY_PD_API_KEY` | PD Workflow key |
| `IGD_DIFY_PM_API_KEY` | PM Workflow key |
| `IGD_DIFY_PA_API_KEY` | PA Workflow key |
| `IGD_DIFY_<ROLE>_URL` | Optional full workflow-run URL override |
| `IGD_DIFY_<ROLE>_INPUT_KEY` | Text variable, default `request` |
| `IGD_DIFY_<ROLE>_RESULT_PATH` | Output path, default `data.outputs.result` |
| `IGD_DIFY_USER` | End-user identifier, default `igd-client` |
| `IGD_DIFY_TIMEOUT_SECONDS` | Socket timeout, 1-600 seconds; default 180 |

`<ROLE>` is TP, PD, PM or PA. TP is required for both requirement and saved-plan runs. Functional keys are required for roles present in the plan. The gear-shaft example needs TP, PD and PM; an assembly task also needs PA.

A requirement run calls TP first to discover the task roles, then validates the corresponding functional configuration. A saved-plan run validates all required keys before dispatch.

`.env` is read only with `--env-file .env`. Values are literal `NAME=value` entries, optionally quoted, without interpolation. Shell variables take precedence. For multi-user deployments, assign an appropriate stable end-user identifier.

## Requests and TP rounds

The adapter POSTs to `/workflows/run` or a configured version-specific `/workflows/<id>/run` endpoint. Initial planning uses:

```json
{
  "inputs": {
    "request": "{\"schema_version\":2,\"role\":\"TP\",\"phase\":\"plan\",\"requirement\":\"Design a gear shaft.\"}"
  },
  "response_mode": "blocking",
  "user": "igd-client"
}
```

The initial TP result is a task plan. Later calls have `phase: "coordinate"` and a `state` containing the plan, task states, validated results, scored proposals, current field and ACBAC-selected dispatch. Their result is a `dispatch`, `finish` or `abort` decision.

For the two-task gear-shaft plan, a normal requirement run has this sequence:

```text
TP plan
TP coordinate -> dispatch PD
PD -> design result
TP coordinate -> dispatch PM, informed by the PD result
PM -> modelling result
TP coordinate -> final summary
```

A saved-plan run omits only the first planning call. TP receives the final modelling result before finishing. Every actual TP call contributes to the reported time and available token totals.

The TP contract uses schema version 2. Functional requests use schema version 1. See [Contracts](contracts.md) for exact request and response objects.

## Transport behavior

Authentication uses `Authorization: Bearer ...`; the key identifies the application. The adapter requires `data.status == "succeeded"`, extracts the configured output, and validates its contract. A paused or running workflow is not complete.

Responses are bounded in size and use blocking mode. Configure proxy and socket timeouts for the deployment; a longer client timeout does not override an upstream limit. There are no automatic request retries. A timeout, truncated response or connection loss can leave a remote workflow running, so the runtime reports failure without silently submitting another call.

Redirects are rejected. HTTPS is required except for local loopback HTTP fixtures. API keys, request headers, raw HTTP errors and workflow definitions are excluded from reports. Generated design results are project outputs; the output directory is excluded from source control.

## References

- [Dify: Run Workflow](https://docs.dify.ai/en/api-reference/workflow-runs/run-workflow)
- [Dify: Workflow App API](https://docs.dify.ai/en/api-reference/guides/workflow)

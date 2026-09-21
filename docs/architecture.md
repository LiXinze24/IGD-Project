# Architecture

## Roles and shared environment

| Term | Meaning | Public entry point |
| --- | --- | --- |
| IGD | Intelligent Generative Design | `igd` |
| TP | Task planning and ongoing coordination | `TPAgent`, planning and coordination contracts |
| PD | Part design | `PDAgent` |
| PM | Part modelling | `PMAgent` |
| PA | Part assembly | `PAAgent` |
| ACBAC | Ant colony-inspired bionic agent collaboration | `PheromoneField` |
| PP | Proposed pheromone | `Proposal` |
| CRP / CGP | Content-retrieval / content-generation prompting | Private PM workflow |
| MKG | Multimodal design knowledge graph | Private workflows |
| GART | Graph-based assembly relation transfer | Private PA workflow |

The paper's Figure 1(b) begins with requirements, TP and task decomposition. Figures 4(b) and 5(b) show TP receiving functional-agent responses and coordinating subsequent execution. The implementation separates TP initialization from its repeated coordination rounds while retaining one TP responsibility throughout.

## TP lifecycle

1. TP receives the design requirement and produces the initial task list. A saved JSON plan can supply this initial list instead.
2. The runtime validates task roles, IDs and dependencies, and initializes the shared field.
3. Eligible agents propose for ready tasks. The shared field evaluates proposals and samples a task and executor.
4. TP receives the selected dispatch, scored proposals, task states, previous results, the latest execution feedback and the current pheromone field.
5. TP dispatches that selection or aborts. A functional result returns to the TP environment, which validates the result and updates task state, history and pheromones.
6. The next TP coordination round consumes the updated state. After all tasks succeed, TP returns `finish` with a final summary.

The runtime computes ACBAC scores; the TP workflow does not introduce an additional scoring formula. Its `dispatch` must identify the field-selected task and executor. A premature `finish`, stale dispatch or unrecognized decision fails the run. A returned result can cause TP to abort rather than authorize the next operation.

The reference runtime uses a serial event loop over one validated task list. It does not regenerate that list on every response. Each task executes at most once per run. The TP limit defaults to 1001 coordination rounds, accommodating 1000 tasks and a final completion round. An exhausted limit is a failed run.

The deterministic local TP policy follows the same contract. The Dify backend calls the private TP workflow for both initial planning and every coordination round. Internal modelling/Checker refinement remains inside the functional workflow.

## Task graph and state

A JSON task plan is a serialized task list with dependencies. A validated graph is its executable representation after structural checks; it is not an additional architectural agent.

Unknown dependencies, duplicate IDs, self-dependencies and cycles are rejected. Input order need not be topological. Eligibility requires every predecessor to have succeeded.

```text
pending -> running -> succeeded
                   -> failed
pending -> blocked    (a predecessor failed or was blocked)
pending -> failed     (no eligible proposal)
pending -> cancelled  (TP aborted, failed or reached the round limit)
```

Failure propagates to descendants. TP can continue independent branches. A run succeeds only after all tasks succeed and TP confirms completion.

Results are addressed by task ID. Functional workflows receive complete validated payloads from their direct predecessors. TP receives all available validated results for coordination. Copies isolate workflow inputs from runtime state. PA component IDs must exactly match its direct PM dependencies; PD dependencies may provide design context.

## Proposal strength

For a ready node, agents with a positive role capability submit:

- Confidence: declared score in `[0,1]`.
- History: successes for that agent and role within the run, using `(successes + 1) / (attempts + 2)`.
- Plan: declared normalized plan-quality score.
- Capability: declared normalized role match.
- Time: `minimum_estimated_seconds / agent_estimated_seconds` for that node's proposals.

Equation (2):

```text
phi_j = alpha * Confidence + beta * History + gamma * Plan
      + delta * Capability + epsilon * Time
```

Weights are nonnegative and sum to one. Supplied functional agents use declared capability, confidence and plan scores of one, and a 60-second estimate. Applications can construct `Agent` instances with their own assessed declarations. These defaults are not measured model judgments.

## Aggregation and selection

```text
c_i = tau_i + sum(phi_j for eligible proposals at node i)
```

The reference policy samples a ready task proportionally to `c_i`, then samples its executor proportionally to `phi_j`. Zero-strength proposals are ineligible. A fixed seed reproduces decisions for the same proposals, results and TP responses. Re-evaluation replaces the proposal contribution rather than accumulating duplicate submissions.

Sampling, equal default weights, inverse-time normalization and the Beta history prior are explicit runtime choices. They are not fitted experimental parameters.

## Quality feedback

Equation (1) updates a node after execution:

```text
tau_i(next) = (1 - rho) * tau_i + Q * f(q_i)
```

The backend's optional `quality` supplies a normalized `f(q_i)` in `[0,1]`. Failed execution and omitted quality receive zero reinforcement. Valid transport or source syntax does not imply a quality score of one.

Defaults are `rho=0.2`, `Q=1.0` and `tau_0=1.0`. The local example supplies no engineering quality score. It exercises evaporation and passes the updated field to TP. Field values, history and random state are scoped to the run.

## Reports and measurements

`run.json` schema version 2 stores the initial plan, task results, field values and `tp.calls`. Each coordination record includes its round, state summary, selected dispatch, accepted decision, elapsed time and reported token usage. The final TP summary and terminal error category appear in `tp`.

`known_execution_tokens` counts available functional-task tokens. `known_tp_tokens` includes initial planning and every coordination call; `known_total_tokens` adds both. Completeness flags distinguish known subtotals from complete usage. Calls with invalid output or failed transport can incur unreported cost. `tp_call_count` counts actual backend calls, excluding loading a saved plan.

Loop elapsed time includes coordination and functional execution. Initial planning time is recorded separately. Source artifacts are saved as text and are not executed by the coordinator. Provider endpoints, keys, raw HTTP bodies and workflow definitions are excluded from run records.

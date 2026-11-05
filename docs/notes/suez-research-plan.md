# Suez: explaining avoidable prefill in coding agents

Plan date: 2026-11-03. Status: direction accepted; execution plan prepared;
engine experiments and performance findings remain pending.

Execution update 2026-11-05: the active pinned agent loop now uses a small direct
HTTP text adapter. Both agent requests pass capture and exact local reconstruction;
the active environment contains 13 packages with no provider SDK. See
[the direct observations](direct-harness-observations.md): unchanged initial
messages yield 45/91 prompt tokens and LCP41 because the template generation cue
differs from realized assistant history. This is a controlled custom backend,
not a finding about stock harness inefficiency or actual cache loss. Next is
M4 calibration preparation and engine input/cache validation on CUDA.

Execution update 2026-11-03: M1 measurement/source contract and the first M2 CPU
explanation slice are complete. See [the contract](prefill-measurement-contract.md),
[pinned source paths](vllm-source-paths.md) and
[saved CPU history observation](prompt-history-observations.md). The new
`analysis/reuse.py` report compares adjacent and historical observed prompt IDs
with source/hash verification. Request 4 in the existing fixture has adjacent
LCP 3 and historical prompt LCP 13. This is structural evidence, not engine reuse.
All 60 Python tests pass on 3.14; base 3.9 skips one optional zstd test. M3 preparation
is next; no new model calls, installation or GPU work occurred.

Further update 2026-11-03: M3's read-only harness inspection and frozen task
specification are prepared. See [the harness note](mini-swe-agent-source-paths.md)
and [task/evaluator package](../../experiments/agent-task/README.md). Actual
baseline/reference validation remains pending approval for the external task
checkout and evaluator execution. No upstream repository code was imported.

Task-preflight update 2026-11-03: the user approved that checkout and evaluation.
Baseline fails the six expected cases (15/21 pass), reference passes 21/21, and
both pass the four sealed original test methods. Exported source trees remained
unchanged. See the task README for raw results and preserved execution inputs.
The task/evaluator gate is satisfied; harness installation, transport validation
and agent execution remain pending. No dependencies or model calls were used.

Admission update 2026-11-03: explicit OpenAI admission now bounds request count,
model, input bytes, output-token aliases and one output choice. Its synthetic
JSON/SSE run made two fixture dispatches and locally rejected a third request;
all captures verified. The two-step real SDK check is specified in
`experiments/agent-task/wire-check.json`, with installation/execution pending.
No engine or GPU claim follows from this synthetic transport result.

## 1. Question and intended outcome

**When a coding agent resends its context, how much recomputation comes from
changes to the prompt versus eviction of reusable KV, and when does fixing the
prompt actually improve latency?**

Build a profiler and controlled experiment that connect a harness operation to
rendered tokens, eligible cache blocks, actual engine reuse, prefill processing
and latency. The primary outcome is a reproducible project and a detailed blog.
A paper becomes a separate decision only if results establish a differentiated
finding. A null performance result remains a valid outcome.

The first completed slice needs one real task trace, one explained cache-miss
mechanism and one evaluated change. A broad dataset, multiple engines, a new
cache policy and a custom agent are not prerequisites.

### Questions the experiment must answer

- RQ1: Which source-confirmed harness operations change the exact rendered
  prefix, rather than only changing request JSON or provider metadata?
- RQ2: Under controlled engine state, how do those changes and cache pressure
  affect actual reuse and processed prompt tokens? Where do they interact?
- RQ3: Does one change intended to preserve reuse improve prefill or latency,
  and does its benefit survive pressure and fresh task execution?

Working hypotheses, not findings: an early rendered change can remove eligible
reuse; pressure can remove reuse from an unchanged prefix; saved prefill may
have little effect on overall latency or task completion time.

## 2. Existing evidence and scope

Already implemented: Go JSON/SSE forwarding, private integrity-checked capture,
offline usage reports and SQLite, strict Qwen reconstruction, controlled token
mutations, request-structure audits, and bounded hosted/Claude compatibility
runners. See `analysis/`, `internal/`, and the experiment READMEs.

Existing evidence:

- Four supported fixture requests reconstructed with exact local Qwen IDs.
- Fourteen supported controlled prefix cases plus one intentional rejection.
- Two successful DeepSeek smoke calls.
- Two Claude Code Messages calls on a synthetic read-only fixture. The second
  reported 1,664 native cache-read tokens. That is provider telemetry, not causal
  evidence of local engine reuse.
- Last recorded checks: 46 Python tests on 3.14; prior Go race/build/vet checks.
  This planning session did not rerun implementation tests.

Missing: engine rendering parity, verified cache lifecycle and per-request
metrics, real task traces on the selected stack, pressure experiments, measured
proxy overhead and live intervention evaluation. LMCache is not integrated;
its exploratory checkout was removed. No engine speedup or novel result exists.

### Initial boundaries

| Item | Working choice | Reconsider when |
| --- | --- | --- |
| Engine | vLLM V1 candidate, one worker and no external KV tier | Pinned version cannot expose or support trustworthy measurements |
| Model | Existing pinned Qwen/Qwen3-8B revision | Available hardware cannot support the selected context; document a new pin and rerun parity |
| Attention | Standard full-attention baseline | Hybrid/sliding-window behavior becomes an explicit later question |
| Harness | Inspect mini-swe-agent first as an append-only baseline | Selected version or backend is incompatible; a different harness is needed for a real mutation |
| Task set | One small real repository task, then two held-out tasks | First slice is validated and an extension answers a concrete question |
| Traffic | Sequential first; deterministic interleaving for pressure | Concurrent queueing is a separate, justified follow-up |
| Outputs | Reproducible CLI reports, figures and blog | UI or publication is separately requested |

mini-swe-agent's current README describes a linear append-only history. This
makes it useful as a control, not evidence that it has harmful prompt churn.
Source inspection must confirm behavior at a pinned revision. If it has no
relevant operation, report that result; do not insert an artificial bug and
describe it as naturally occurring. A labeled synthetic mutation can still
validate the mechanism. A second harness is added only to answer a measured gap.

## 3. Measurement flow and ownership

| Component | Input and responsibility | Output and boundary |
| --- | --- | --- |
| Harness | Task, current files, tool results and history; assembles requests | Original requests with explicit task/session/attempt identifiers |
| Suez proxy | Forwards bytes and captures complete/incomplete exchanges | Immutable bodies, integrity metadata and proxy timing; no prompt rewriting |
| Reconstruction | Pinned model/template/options and supported request bodies | Exact IDs and token hashes; reject unsupported content |
| History analysis | Earlier eligible materialized sequences and current prompt | Adjacent LCP plus history-aware structural candidates; no residency claim |
| Engine adapter | Correlated lookup, block lifecycle, scheduling and timing observations | Actual hits, processed prompt work and available eviction evidence |
| Replay controller | Immutable dataset, reset/warm-up rules and intervention manifest | Paired runs with explicit cache state, arrivals and replay strategy |
| Report | Joins by IDs and domain | Per-request explanations, condition effects, uncertainty and unknowns |

Planned modules are provisional: `analysis/reuse.py` for structural history,
`analysis/engine.py` for normalized engine evidence, `analysis/attribution.py`
for explanations, and `experiments/cache-ground-truth/` plus
`experiments/replay/` for run control. Add them one at a time only when their
acceptance criteria require them. Preserve existing capture sources of truth.

### Quantities that must stay separate

- Prompt length: exact IDs under the local experiment's rendering.
- Adjacent LCP: comparison with the preceding prompt; useful for locating
  changes, insufficient for modeling the cache.
- Historical structural match: a candidate prefix from earlier completed,
  materialized sequences in the same engine/cache namespace. Account for block
  boundaries, generated output and engine eligibility. This is potential reuse.
- Engine reuse: actual per-request lookup results under a recorded state.
- Inferred uncached input: prompt length minus reported reuse, when the pinned
  metric permits that arithmetic. It is not automatically measured GPU work.
- Processed prompt tokens: engine scheduling/execution observations, including
  repeated processing after preemption when available; keep retries explicit.
- Timing: proxy first write, first content/tool/reasoning event, engine queue
  time, prefill time and full completion time each have separate definitions.

Missing values are null. Global counter deltas support a request only when the
worker is isolated and metric semantics are verified. Concurrent attribution
requires correlated request telemetry. Do not infer pure GPU time from proxy
timestamps or subtract durations measured at unrelated boundaries.

### Evidence labels

Use labels such as `rendered_prefix_change`, `controlled_residency_effect`,
`engine_eligibility`, `cold_start`, `mixed` and `unknown`, with supporting run
IDs and observations. A mutation label describes an observation; it does not
prove a latency cause. Eviction requires lifecycle evidence or a controlled
warm/flood/probe contrast, with the strength of that conclusion stated.

Never label `adjacent_lcp - cached_tokens` as eviction loss. Actual reuse may
come from older requests, other sessions or generated assistant output.

## 4. Milestones and acceptance criteria

### M0. Freeze direction and maintain the plan

Status: plan prepared. This document owns the focused Suez execution plan;
`.project/STATE.md` owns observed progress and `.project/WORK.md` the checklist.
The older shared Malacca roadmap remains historical cross-project context.
Notion will be the user-facing project page once writing is available. Record
its page URL locally and update the same page rather than creating duplicates.

Done: scope, evidence boundaries, authorization gates and next work are recorded.

### M1. Specify the measurement contract and source paths, on Mac

- Read the existing report/reconstruction/audit contracts and map reusable fields.
- Pin a candidate vLLM revision through read-only source inspection. Trace
  rendering, block identity, hit lookup, allocation, eviction and processed-token
  metrics. Record exact paths/functions, not floating documentation alone.
- Specify `run`, `request`, `rendering`, `cache_event`, `engine_metrics` and
  `intervention` records, their IDs, nullable fields and observation boundaries.
- Select how to obtain exact engine input IDs and correlate engine requests.
  If exact introspection is unavailable, explicitly qualify parity.
- Compare the proposed measurement with TraceLab, CacheWise and The Physical
  Cost of Context. Record overlap and artifact availability; novelty is not a
  gate for completing the blog/project.

Deliverable: measurement contract, source-path note and claim/evidence table.
Done: every proposed output has an observation source or is explicitly inferred
or unavailable. No unexplained schema field promises a measurement we lack.

### M2. Build the CPU explanation slice

Depends on M1. Extend existing reconstruction/audit rather than replacing them.

- Maintain an ordered ledger of exact supported prompt sequences. Include
  generated sequences only when exact materialization and provenance are known.
- Compute adjacent and history-aware matches separately, with engine namespace
  and block-rule configuration. Exclude future, incomplete or unsupported rows.
- Validate repeated input, early/middle/late change, JSON-only change, an older
  non-adjacent match and a generated-history boundary. Engine-rule fixtures are
  tests of the model, not evidence that the actual engine follows it.
- Produce an annotated request report using existing Qwen fixtures. Keep the
  DeepSeek Messages audit separate; do not silently remap it to Qwen.

Deliverable: one regenerable report explaining structural changes and unknowns.
Done: someone can inspect the changed region and token boundary without reading
all raw bodies, and no structural field is presented as an actual cache hit.

### M3. Freeze the harness and task, on Mac

Depends on M1; can proceed alongside M2 without new live calls.

- Inspect mini-swe-agent's exact prompt assembly, history, backend, retries and
  truncation. Pin revision/license and identify the compatible request subset.
- Choose one small existing repository bug with objective failing checks and a
  reference fix. If the bug is injected, label the workload synthetic.
- Freeze task statement, repository state, baseline failures, evaluator, allowed
  files/tools, request/token/time bounds and initial environment.
- Record the intended source of a counterfactual operation. Do not promise a
  harmful operation before observing it. Keep append-only behavior as a control.
- Prepare dry-run integration. New clones, installs and environment changes
  require the user's authorization. No new hosted capture is needed merely to
  repeat the existing compatibility result.

Deliverable: task/harness manifest and evaluable baseline.
Done: the task's success condition and one bounded capture command are
reviewable before execution. Task executions remain pending until authorized.

### M4. Establish engine ground truth, first GPU gate

Depends on M1 and M2; does not need a complete task benchmark.

Before acquiring hardware, prepare a reproducible launch specification with
engine commit, image digest, model revision, tokenizer/template flags, weight
and KV dtype, context, usable KV capacity, output limits and reset procedure.
Estimate memory from the pinned config plus weights/runtime overhead. Do not
promise a context size from nominal VRAM alone. Obtain the spending ceiling and
approval for any new downloads/installation before using paid hardware.

Run short sequential controls:

1. Cold A, repeated A, early-mutated B after A, and independently cold B.
2. Token-position and block-boundary cases using actual engine eligibility.
3. A prior non-adjacent match and a generated-output boundary case.
4. Warm A, controlled unrelated cache-filling traffic, then probe A with no
   competitor still running. Verify pressure occurred; record the cache state.
5. Direct, proxy-without-capture and proxy-with-capture conditions to quantify
   instrumentation overhead under the selected workload.

Separate runtime/JIT warm-up from prefix warm-up. Start with at least five paired
independent repetitions for calibration, increasing only when variability or a
claim requires it. Preserve failures and reset errors.

Deliverable: raw controls, engine correlation and a reconciled calibration report.
Done: exact token parity is established or its remaining limits stated; cold and
warm states are validated; reported hits/processed tokens are explained. If a
discrepancy remains, stop causal attribution and investigate it.

### M5. Capture and explain one real task

Depends on M3 and M4. Prefer the same local model/engine used for validation so
the first research slice does not require a provider-to-local translation.

- Run the frozen task through Suez within explicit bounds and an isolated task
  environment. Preserve the resulting patch, task checks and failed attempts.
- Correlate each request with exact rendered input and engine observations.
- Trace candidate prompt changes to harness source and record mixed/unknown
  cases. Do not infer operation identity from JSON shape alone.
- Choose one observed operation for evaluation. If none damages reuse, finish
  the baseline diagnosis and retain a clearly labeled synthetic mechanism case.

Deliverable: one end-to-end trace with task outcome and request explanations.
Done: all included requests have integrity, lineage, metric coverage and stated
limitations. No claim of general workload frequency from one task.

### M6. Run the paired prompt-by-pressure experiment

Depends on M4 and M5. First establish the mechanism with controlled requests;
then use a task-derived frozen sequence where the operation exists.

| Condition | Prompt behavior | Cache condition |
| --- | --- | --- |
| A0 | Original | Sufficient capacity; absence of eviction is checked |
| B0 | One specified change intended to preserve reuse | Same sufficient-capacity setup |
| A1 | Original | Controlled pressure, with reproducible interleaving |
| B1 | Same change as B0 | Same pressure construction as A1 |

Start with completed competitor requests between target requests, so eviction
can be studied without simultaneous queueing. Concurrency is a later experiment
with its own arrival model and per-request instrumentation. Keep tokenization,
cache namespace, sampling, task material and reset/warm-up rules matched.

For processed prompt work U, report fix effects `U(A0)-U(B0)` and
`U(A1)-U(B1)`, pressure effects within each prompt condition, and the interaction
between those effects. Apply the same paired analysis to latency. Do not turn
these contrasts into universal additive shares of all cache misses.

Replay policy must be explicit. One-token fixed-request replay does not recreate
cached recorded assistant completions. Choose either that limited probe, a
validated recorded-token materialization mechanism, or fresh live execution;
document which question each can answer. No production-residency claim from
one-token replay alone. Sanitized or transformed inputs form separate datasets.

Deliverable: four-condition results with raw records and paired uncertainty.
Done: the direction and practical size of each effect are reported, including
zero/negative results, failures and unexplained cases. A five-percent improvement
is not promised or used as a post-hoc success threshold.

### M7. Check behavior in fresh task executions

Depends on M6 showing a useful mechanism and a reviewable intervention. A
latency-neutral change may still merit a diagnostic write-up without rollout.

- Run original and changed harnesses on the frozen task and two held-out tasks
  using fixed evaluators, independent clean task states and matched budgets.
- Pilot with three paired runs per task if affordable, then size further runs
  from variability and the intended claim. This is a feasibility check, not
  statistical proof of quality non-inferiority.
- Record correctness, calls, generated tokens, timeouts, total completion time
  and resource observations. Count failures in denominators and spending.
- Reordering tools, relocating metadata, removing timestamps and rewriting
  history may alter model behavior. Replay does not establish equivalence.

Deliverable: task-level comparison and documented failure cases.
Done: scope any quality statement to the tested tasks and uncertainty. Reject a
behaviorally harmful intervention even if its frozen replay is faster.

### M8. Finish the project and experimental blog

- Regenerate four core figures: an annotated request/token/block trace; potential
  versus actual reuse; prompt-by-pressure effects; and latency/task tradeoffs.
- Package pins, manifests, run commands, schema, permitted fixtures, raw metric
  records and figure scripts. Link every claim to a run or verified source.
- Explain one failure or no-benefit case and which measurements were unavailable.
- Draft the blog: **Why is my coding agent computing the same context again?**
  Start from a real trace, explain the cache lookup path, test competing causes,
  evaluate a change and state when it fails. Keep the attention primer short.
- Consider upstream work only for an actual observability or correctness issue.
  Publication, external contact and PR submission remain separate authorizations.

Done: a reader can reproduce the claimed behavior and inspect the reasoning.
Assess paper potential only after the result and full closest-work comparison.

## 5. Run record and reporting requirements

Every run records source revision plus dirty-source snapshot/hash; engine/image
and model/tokenizer/template pins; GPU/runtime; cache capacity and granularity;
reset outcome; sampling/output limits; exact input IDs; task and harness pins;
sequence/arrivals; replay mode; competitor traffic; seeds/order; timing
definitions; failures; raw and derived artifact hashes; and analysis commands.

Primary outputs: per-request actual reuse, processed/inferred prompt work,
first-content latency, engine queue/prefill durations when available, and task
completion time. Cache occupancy, eviction events and preemptions support the
mechanism. Report metric coverage. Do not use cache hit rate alone as a speedup.

Use paired session/task-level summaries and uncertainty that respects correlated
steps. Do not bootstrap individual dependent turns as independent experiments.
p99 is outside the first small-sample milestone; add it only with enough request
observations and a documented tail-estimation method.

## 6. Stopping criteria and useful negative results

| Finding or obstacle | Action |
| --- | --- |
| No rendering/metric parity | Stop causal claims; fix or document the observability boundary |
| Harness is already append-only | Keep it as a control; avoid manufacturing an inefficiency |
| Prompt change does not affect rendered IDs | Report a JSON-only change; do not optimize it for caching |
| Prompt fix helps only with ample cache | Report the capacity-dependent benefit and interaction |
| More reuse gives no meaningful latency gain | Publish the diagnosis; do not advertise a speedup |
| Intervention hurts tasks | Reject it as a default; retain the failure analysis |
| Closest work covers the result | Position it as reproduction/tooling; paper novelty is optional |
| Additional tooling does not answer an RQ | Defer it until an observed need justifies it |

## 7. Next three work sessions

These are ordered work units, not calendar commitments.

1. M1: inventory existing fields and pin/read the engine observation paths.
   Finish with the measurement contract and open metric questions.
2. M2: implement the smallest history-aware CPU explanation using saved fixtures,
   with meaningful boundary tests and a regenerable report.
3. M3: inspect the harness source and freeze the task/evaluator and one-operation
   experiment specification. Finish the GPU launch/calibration specification
   before requesting hardware expenditure.

GPU access is first required at M4. New paid API traffic is not a prerequisite
for the Mac work. No clone, dependency install, global setting change, paid
resource, external message, commit or publication is authorized merely by
appearing in this plan. Ask before crossing those boundaries.

## 8. Decisions requiring user input

- Notion destination is optional: use a durable private draft if no parent page
  is supplied, then keep that page identity for future updates. The connection
  currently returns `Unknown tool` on read/search; no Notion save is verified.
- Before M3 environment setup: approve any specifically named external checkout
  and dependency installation with its location and purpose.
- Before M4: GPU access method and a total spending/time ceiling. Offer concrete
  hardware and run estimates after the memory/measurement specification exists.
- Before new task executions: approve the exact API or local compute budget,
  task environment and execution bounds. Do not infer unlimited spend from an
  earlier smoke-test authorization.

No unresolved research preference blocks M1. Current default is depth on this
one question and a useful blog/project; breadth and publication are conditional.

## 9. Sources and evidence status

Read primary sources and pin exact versions before implementing their contracts.
These are overlap and mechanism references, not a claim of completed reproduction.

- [vLLM prefix-cache design](https://docs.vllm.ai/en/latest/design/prefix_caching/):
  block identity/eligibility and lifecycle reference; latest is a moving target.
- [vLLM metrics](https://docs.vllm.ai/en/latest/design/metrics/): metric inventory;
  verify request attribution and semantics on the selected revision.
- [TraceLab v2](https://arxiv.org/html/2606.30560v2): agent workload context;
  sanitized provider traces do not expose full input identity or residency.
- [CacheWise](https://arxiv.org/abs/2606.16824): close scheduling/retention work;
  inspect artifacts before claiming a gap.
- [The Physical Cost of Context v1](https://arxiv.org/html/2610.34763v1): close
  work on context policies, prefix reuse and quality-constrained resource cost.
  Our plan does not claim those relationships are new.
- [mini-swe-agent README](https://github.com/SWE-agent/mini-swe-agent): inspected
  read-only on 2026-11-03; candidate baseline with documented linear history.
- Local evidence: `docs/notes/cpu-prefix-observations.md`,
  `docs/notes/claude-capture-audit.md`, existing experiment manifests and tests.
  Older literature notes contain provisional leads; verify identities before use.

The LMCache load-versus-recompute discussion is a separate deferred direction.
It does not replace this experiment or authorize another checkout.

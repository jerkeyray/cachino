# Prefill measurement contract, version 1

Status: specification grounded in existing Cachino and read-only vLLM source
inspection on 2026-11-03. No vLLM runtime, cache state or performance was tested.
See the [report](../report.md) for results and
[the pinned source paths](vllm-source-paths.md) for the observation mechanisms.

## Supported initial experiment

One full-attention Qwen model, one vLLM worker, one output sequence (`n=1`),
text chat, no multimodal input, LoRA, external KV, speculative decoding or
streaming-session extensions. Tokenizer/template/options and actual cache
geometry are explicit. These exclusions define the first measurement model;
they are not statements about what vLLM generally supports.

Source-inspection pin: vLLM `2173d7121fc6e8d756c884f46691546c61b60c57`.
This is a candidate for calibration, not an installed or recommended production
release. Record any later runtime pin change and review this contract again.

## Existing fields and gaps

| Quantity | Existing source | What it establishes | Required extension |
| --- | --- | --- | --- |
| Cachino request ID / acceptance sequence | `internal/trace/trace.go:Record` | Local identity and admission order | Join to returned engine ID; sequence is not completion order |
| Session / turn / attempt | Explicit caller labels in trace and report | Labeled grouping, including unknown labels | Add task/run identity in a run manifest; do not infer missing labels |
| Body identity and completeness | Stored/observed SHA-256, byte counts and flags | Capture integrity | Preserve raw records unchanged |
| Proxy duration / first response write | Monotonic handler measurements | Handler interval / first nonempty write | First content-event timing needs new instrumentation; neither is engine TTFT |
| Provider prompt/cache counts | `analysis/report.py:response_summary` | Reported usage with source/conflict flags | Keep provider domain; no inference of engine residency |
| Local prompt IDs / hash | `analysis/reconstruct.py:render`, `summary.json` | Local reconstruction and token sequence identity | Compare with returned engine prompt IDs on controlled inputs |
| Adjacent LCP | `analysis/reconstruct.py:lcp` | Prefix of preceding labeled prompt | Add historical observed-prompt comparison; do not call it actual reuse |
| Structural request differences | `analysis/audit.py` | JSON/body changes and evidence links | Source-confirm operation labels; JSON-only changes can preserve IDs |
| Engine prompt/output IDs | Raw body may contain them; no extraction today | None in current derived report | Parse `return_token_ids` response fields without changing captured bodies |
| Actual engine hit count | API `usage.prompt_tokens_details.cached_tokens`, if enabled | Source-defined per-request hit observation | Validate flag/configuration and counts in M4 |
| Engine queue/prefill intervals | Per-request response metrics or finished stats | Source-specific intervals, not kernel durations | Normalize names/boundaries and preserve raw values |
| Repeated processing after preemption | No Cachino source | Unknown | Scheduler-step instrumentation or mark unavailable |
| Cache blocks stored/removed/reset | No Cachino source | Unknown | Subscribe to pinned KV events; verify identity, loss and ordering |

## Identity and record families

This is a derived schema. It does not change trace schema v1 or redefine its
request ID. Never join by coincident timing alone.

| Record | Required identity/provenance | Important fields |
| --- | --- | --- |
| Run | `run_id`, source snapshot/hash, engine/model/rendering pins | Worker, cache namespace, geometry, reset epoch, arrivals, replay mode, intervention ID |
| Request | `(run_id, cachino_request_id)` | Acceptance sequence, session/turn/attempt/task, capture integrity, original path/model |
| Engine correlation | Request key plus returned response ID and worker | Match method, ambiguity, nullable internal request ID |
| Rendering | Request key plus rendering-domain fingerprint | Exact ID reference/hash/count, source/template/library pins, model remap, parity state |
| Structural comparison | Current and predecessor/candidate IDs | Adjacent LCP, historical observed-prompt LCP, candidate coverage, unknown boundaries |
| Engine observation | Request key, engine pin, source path/field | Raw and normalized counts, interval definitions, flags/preemptions, coverage |
| Cache event | `(run_id, worker, reset_epoch, transport_sequence, event_index)` | Kind, group/hash/parent/extra-key identity, observed time, raw event reference |
| Intervention | Immutable ID and original/derived dataset hashes | Exactly changed operation, source location, semantic risk, validation status |

Use a controller's `X-Request-Id` header for planned local runs, preserving the
Cachino-generated ID separately. The inspected server prefixes the returned ID
with `chatcmpl-`; headers take precedence over a body `request_id`. Parse the
actual returned ID rather than assuming identity or manufacturing correlation.
No proxy mutation of arbitrary user request bodies is required. Session labels
in Cachino headers are stripped and do not automatically set an engine session.

## Exact token parity and structural history

Request `return_token_ids=true` in calibration requests. The inspected API
returns `prompt_token_ids` in JSON or the first streaming chunk, and generated
`token_ids` in choices. Validate the entire ID sequence, not only its length or
decoded text. SSE chunk boundaries, finish errors and metadata suppression
matter. Token metadata increases capture volume and may affect overhead.

The local renderer accepts `return_token_ids` as an observational boolean and
the pinned template settings only (see [the M4 calibration plan](m4-calibration-plan.md)).
Other unknown fields remain rejected.
Do not silently strip arbitrary fields such as cache salts, tool choices or
template overrides. `enable_thinking=false` must be configured on both sides;
the local pin alone does not configure the server's renderer.

CPU M2 first compares *observed prior prompt sequences* in the same explicitly
labeled session/model domain. This intentionally excludes generated assistant
IDs, cross-session sharing, unobserved requests and runtime cache residency.
Acceptance sequence provides an analysis order, not proof that one prompt's KV
was materialized before the next lookup. Break comparisons at unsupported or
unverified rows; expose remaining coverage rather than extrapolate across gaps.

Later engine-backed history may include exact processed output sequences only
when processing and chronology are established. A returned final sampled token
may not yet have KV. Decoding text and re-encoding it does not establish the
materialized engine token sequence. Never reconstruct that state by concatenating
all response IDs without validating the processing boundary.

No block-aligned prediction in CPU M2 is required. The inspected full-attention
manager supports fine-grained hash lookup; record allocation block size, hash
granularity and hit alignment separately before predicting engine eligibility.
The final prompt token is bounded out of reuse for logits, with additional
rules possible. Simple `floor(LCP / block_size)` is not a universal oracle.

## Timing names and processed work

| Normalized name | Source and interval | Limit |
| --- | --- | --- |
| `proxy_handler_ms` | Cachino `duration_ms` | Includes forwarding/capture effects |
| `proxy_first_write_ms` | Cachino `first_response_write_ms` | Role-only event can trigger it |
| `proxy_first_content_ms` | Future parsed live event timestamp | Null until implemented; raw completed bodies have no per-event arrival times |
| `engine_schedule_to_first_token_ms` | API `metrics.time_to_first_token_ms` | Source computes first-token minus scheduled timestamp, excluding queue time |
| `engine_queue_ms` | API `metrics.queue_time_ms` | Scheduled minus queued timestamp |
| `engine_first_to_last_token_ms` | API `metrics.generation_time_ms` | Decode interval, excludes queue and prefill |
| `engine_frontend_ttft_ms` | Optional frontend stats | Different boundary; keep separate rather than replace API interval |
| `scheduled_prompt_token_work` | Future sum of actual prompt-token scheduling ranges | Needed for preemption/reprocessing; not supplied by API usage alone |

Finished-request `prefill_time` is scheduled-to-first-token elapsed time and may
include preemption. It is not GPU kernel time. The inspected `PrefillStats.set`
computes prompt minus local/external cached tokens. The scheduler initializes it
for the first scheduled prefill and intentionally excludes post-preemption repeat
prefills. Calling that field actual work does not make it a repeated-execution
counter. Preserve it as first-prefill accounting; record reprocessing separately.

For the sequential, no-preemption gate, validate API hit counts and inferred
uncached prompt counts against engine observations. Do not claim FLOPs, pure
prefill compute duration or energy from token arithmetic.

## Residency evidence and causes

KV events can describe stored hashes, removed hashes and clearing. Removal
does not identify a request or distinguish every reason by itself. Hash keys
can have multiple backing blocks. Cache namespace includes worker/reset epoch,
group and relevant extra identities; token matching alone is insufficient.
Check event transport loss/recovery and ordering before building a resident map.

The initial residency test is warm A, completed unrelated filling traffic, then
probe A with no competitor still active. Keep inputs, renderer, worker and
eligibility fixed, validate the warm hit and pressure, and distinguish reset
events. If lifecycle evidence is incomplete, report a controlled pressure effect
rather than claiming a complete per-block eviction explanation.

Output observations and causes separately. `rendered_prefix_change` is a
structural observation. `controlled_residency_effect` needs a matched experiment.
`mixed` and `unknown` are valid explanations. Global pressure, prompt changes
and preemption can interact; their inferred costs are not additive categories.

## Claim/evidence gates

| Proposed claim | Minimum evidence | Current status |
| --- | --- | --- |
| Requests change at token position k | Verified bodies, explicit domain, exact locally rendered IDs | Available for existing supported CPU fixtures |
| An older prompt matches better than its immediate predecessor | Verified history-aware CPU comparison with coverage | Implemented in `analysis/reuse.py`; saved CPU fixture demonstrates it |
| Local IDs equal engine input | Returned engine IDs and full equality under pinned rendering | Runtime pending |
| N tokens actually hit local KV | Calibrated correlated hit observation, no external tier | Runtime pending |
| Pressure removed useful reuse | Warm/probe controls plus fixed identity and pressure evidence | Runtime pending |
| Total processed prompt work fell | Validated execution counter including reprocessing, or qualified no-preemption scope | Runtime pending |
| A change improves latency | Paired reset-controlled runs, calibrated overhead and uncertainty | Runtime pending |
| Task behavior is preserved | Fresh independent task runs and fixed evaluators | Pending; replay cannot establish it |

M1 is complete when quantities have sources or explicit unknowns, pins and
source locations are recorded, and the next implementation cannot silently
promote structural matches into actual reuse. M1 does not require a GPU run.

## Closest-work check

This is a focused overlap check, not an exhaustive novelty review or reproduction.

| Primary source | What it already addresses | Our boundary and next check |
| --- | --- | --- |
| [TraceLab v2, sections 3 and 9](https://arxiv.org/html/2606.30560v2) | Coding-agent workload and provider-observed cache patterns; internal causes cannot be directly observed | Do not use sanitized provider hit counts as a residency oracle; inspect public artifacts before reuse |
| [CacheWise v1, sections 5 and 6](https://arxiv.org/html/2606.16824v1) | Agent prefix-aware scheduling and predictive eviction in vLLM | No new eviction algorithm claimed; public trace link exists, implementation artifact not reproduced |
| [The Physical Cost of Context v1, sections 3, 7 and appendix A](https://arxiv.org/html/2610.34763v1) | Context policies, reuse, resource accounting and quality gates | Strong overlap; its reported limits include sequential execution and untested pressure/batching. Our controlled pressure interaction is an investigation, not proven novelty |

All three are evidence for grounding and scope. Absence of an experiment in one
paper does not establish that no other work addresses it. Cachino can succeed as
reproducible tooling and an experimental blog even if the finding is established.

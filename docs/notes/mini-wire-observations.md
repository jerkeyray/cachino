# Real harness SDK wire check

Historical LiteLLM-backed check. On 2026-11-05 the active path changed to a
[direct HTTP adapter](direct-harness-observations.md); the current dependency
lock contains 13 packages. This note describes the earlier saved source/lock.

On 2026-11-03, the pinned mini-swe-agent ran its actual `DefaultAgent` and
`LitellmTextbasedModel` against a fixed local OpenAI fixture through Suez.
This establishes transport compatibility for the selected configuration and
reveals one reconstruction boundary. It is not a coding-task or cache experiment.

## Frozen implementation

- mini-swe-agent: `04d809ceab9df28f9adaed044884180159172930`, clean checkout;
  all 15 inspected source blobs verified before and after execution.
- CPython 3.12.13, LiteLLM 1.106.6, OpenAI 2.54.0, python-dotenv 1.2.4.
- All 79 installed dependencies matched the version and distribution-hash
  lock saved in each run's `source/experiments/agent-task/wire-requirements.lock`.
- Two agent steps, one regex text action per reply; the environment records
  the action and returns a fixed observation. It has no shell execution path.
- A fresh Suez instance admits at most two calls, with exact fixture model,
  128 KiB input bound, requested output bound 64 and retries disabled.

## Observed shape

| Wire request | Messages | New content | Strict local renderer |
| --- | --- | --- | --- |
| First SDK call | system, task | Initial strings | Accepted |
| Second SDK call | system, task, assistant, observation | Prior fixed reply and recorded observation append | Rejected: unknown message field |
| Parent's quota probe | Separate single user message | Valid third request | Not analyzed as an agent turn; HTTP 403 |

Both SDK requests contain exactly `model`, `messages`, `max_tokens`, `n` and
`temperature` at the top level. The second request preserves the initial two
message objects. The assistant object additionally contains
`provider_specific_fields: {"refusal": null}`. The harness's `extra` metadata
does not reach the wire. Raw bodies were preserved without normalization and
checked against the bytes received by the fixture and sent back to Suez.

Each run recorded HTTP statuses 200/200/403, exactly two upstream fixture calls,
three complete integrity-verified captures, no unfinished requests and no report
warnings. The agent stopped with `LimitsExceeded` after its two allowed calls.
No usage was included in fixture replies. Reported token/cache metrics remain
null, even if the SDK synthesized defaults internally.

## Import behavior and evidence

The first run, `artifacts/mini-wire-20261103-01/`, blocked two external DNS
attempts made by LiteLLM's cost-map refresh. Installed LiteLLM source documents
`LITELLM_LOCAL_MODEL_COST_MAP=True`; the repeat with that setting,
`artifacts/mini-wire-20261103-02/`, recorded zero blocked network attempts and
an empty worker log. Both reached the same transport/reconstruction outcome.
No external model endpoint was configured or contacted.

The harness loads dotenv at import. Each child used an explicit environment,
`PYTHON_DOTENV_DISABLED=1` and a new empty config directory. Neither used Suez's
dotenv or inherited host credentials/proxy settings. Both config directories
remained free of dotenv files. The Python network guard is not an OS sandbox.

Each run retains the exact runner, manifest, dependency lock and Go/analysis
source snapshots, binary hash, installed package inventory, effective spec,
trajectory, logs, raw capture and derived results. Saved source/binary hashes
were rechecked and all owned ports were closed after both runs. Setup provenance
is separate in `artifacts/mini-wire-setup-20261103/`.

## Consequence for the next step

SDK serialization adds metadata even to a simple text-action history. Our
renderer correctly exposes an unsupported field instead of silently discarding
it. Inspect the pinned template and engine parser, then add narrowly scoped
support only if its treatment is justified and tested. A changed JSON object is
not evidence that the model's token sequence changed or that reusable KV was lost.

This short fixed conversation does not establish harmful harness mutation,
real-task success, tokenizer/engine parity, eviction or latency improvements.
No GPU was used. The first GPU work remains M4 engine/cache calibration after
the remaining CPU compatibility work. The real coding task still requires its
separate command-isolation setup before execution.

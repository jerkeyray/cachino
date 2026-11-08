# First harness: mini-swe-agent source inspection

Inspected read-only on 2026-11-03 at
`04d809ceab9df28f9adaed044884180159172930`. Main was resolved once, then each
file was fetched by that commit. That initial inspection used no checkout or
imports. The [task manifest](../../experiments/agent-task/manifest.json) now
records 18 inspected files and their Git blob identities. License: MIT.
Subsequently, after user approval, the pinned source checkout and hash-locked
dependencies passed the real SDK wire check against
a local fixed-response fixture. No model inference or agent shell action ran.
On 2026-11-05 the active path changed to the
[direct model adapter](direct-harness-observations.md), preserving the pinned
agent loop and text-action helpers. Both resulting requests reconstruct locally.

## Selected scope

Use `DefaultAgent` with Cachino's `DirectTextModel`, upstream `parse_regex_actions`
and `format_observation_messages`. Its transport uses stdlib HTTP, has no retries
and constructs only explicit text message fields. The earlier selected
`LitellmTextbasedModel` is documented as historical source inspection below.
This custom backend is a controlled configuration; do not present its results
as stock default mini-swe-agent behavior. The upstream v2 text-action utility
recommends native tool calls for general use.

The first role is an append-only control. Source inspection does not establish
a harmful early mutation or engine/cache behavior. Local token reconstruction
now exposes a generation-boundary suffix difference despite append-only messages.

## Actual flow

The active flow is `DefaultAgent` -> `DirectTextModel` -> Cachino -> local fixture,
using the pinned helpers in item 4 for observations and their text-action parser.
Items 2, 3, 5 and 6 below describe the earlier LiteLLM-backed path.

1. [`DefaultAgent.run/query/add_messages`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py)
   renders system and first user/task templates once per run. Later queries
   receive `self.messages`; assistant messages and observations are appended.
   Dynamic template variables exist, but their existence alone does not mean
   earlier messages are regenerated each turn. Format errors append feedback;
   step, cost and wall-time checks are separate from hard upstream admission.
2. [`LitellmModel._prepare_messages_for_api`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/litellm_model.py)
   removes `extra` from each message, applies the Anthropic thinking helper,
   then optional cache-control processing. Assistant responses keep the model's
   message dump plus local `extra`; actual wire fields need a pinned LiteLLM
   transport check before claiming compatibility with Cachino reconstruction.
3. [`LitellmTextbasedModel._query`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/litellm_textbased_model.py)
   calls LiteLLM without the parent's `BASH_TOOL` schema. It parses one regex
   action from text and turns command outputs into user observation messages.
   Explicitly select this class; changing only the prompt does not select it.
4. [`actions_text.format_observation_messages`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/actions_text.py)
   renders a fresh observation. Timestamps are stored under `extra`, which the
   API preparation removes. The default configuration clips very long *new*
   outputs to a head and tail; this is not evidence of rewriting prior history.
5. [`set_cache_control`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/cache_control.py)
   returns unchanged messages in mode `None`. `default_end` copies messages,
   removes old markers and sets a final-message marker. That changes API
   structure, but does not by itself establish a model-token change. Select
   `None` for the initial local OpenAI/Qwen baseline.
6. [`_reorder_anthropic_thinking_blocks`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/anthropic_utils.py)
   only reorders assistant list content containing thinking blocks. It does not
   rewrite ordinary string content. Such list content is outside our first
   reconstruction subset; reject it rather than silently claim parity.

## Bounds and environment

- [`retry`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/retry.py)
  defaults to up to ten attempts for the earlier backend. The active direct
  adapter bypasses this utility and sends once. Count actual transport dispatches;
  `n_calls` alone does not generally establish the number of upstream attempts.
- [`GlobalModelStats`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/__init__.py)
  updates after calls; cost limits are not guaranteed pre-dispatch billing caps.
  Cachino now has explicit OpenAI admission via `-session-protocol openai`, tested
  with a synthetic JSON/SSE fixture. Default limited mode remains Messages.
  The current direct adapter leaves cost unavailable rather than inventing it;
  it relies on Cachino's separately verified OpenAI request quota.
- [`LocalEnvironment`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/local.py)
  executes directly on the host and includes host environment variables. A cwd
  setting is not isolation. Do not use it for unrestricted model-generated
  shell actions in the study.
- [`DockerEnvironment`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/environments/docker.py)
  starts a container when constructed and can trigger image retrieval. Configure
  the image digest, mounts, networking and resources before construction and
  obtain setup authorization first. Docker's binary is present on this Mac;
  daemon availability and image readiness have not been tested.
- [`pyproject.toml`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/pyproject.toml)
  requires Python >=3.10 and declares the full package's external dependencies.
  The active source-imported subset uses five direct dependencies and 13 total
  packages, locked in `experiments/agent-task/wire-requirements.lock`. The earlier
  full 79-package resolution remains in private run snapshots.
- [`minisweagent.__init__`](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/__init__.py)
  creates its global config directory and calls `dotenv.load_dotenv` at import.
  The wire-check child must select a new empty `MSWEA_GLOBAL_CONFIG_DIR` and
  disable dotenv loading before this import. Not calling dotenv in Cachino's runner
  alone would not prevent the harness from loading its global credentials.
  This entry point is included in the manifest's blob inventory.

## Next compatibility gate

The approved setup and actual two-step fake-endpoint check are complete. Controller:
[`wire_check.py`](../../experiments/agent-task/wire_check.py). It verified endpoint,
placeholder authorization, no extra transport attempts, append behavior and exact
captured bytes. The third valid request was rejected locally by quota.

LiteLLM 1.106.6 and OpenAI 2.54.0 produce an assistant message containing
`provider_specific_fields: {"refusal": null}` in the historical run. That capture
remains unsupported by our renderer. The active direct adapter avoids importing
provider SDKs and constructs the text-only history explicitly; it retains raw
responses in local metadata. The renderer was not broadened to ignore the field.

The active fixture passes transport and CPU reconstruction checks: 45/91 prompt
tokens with LCP 41, explained by the template's generation cue. Engine token
identity, real task behavior and cache reuse remain M4/later gates.

The agent's task export must exclude `.git`, reference fixes, manifests, evaluator
controls and credentials. The task evaluator is external to the editable tree;
its original upstream test class is fingerprint-checked and sealed separately.

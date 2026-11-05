# Frozen first coding task, specification and evaluator

Status: task preflight validated on 2026-11-03 after explicit user approval.
The isolated repository checkout, baseline/reference exports and evaluator
checks are complete. The active direct HTTP adapter passed the local fixture
and CPU reconstruction checks on 2026-11-05. Both agent requests render with the
pinned tokenizer. Task inference and engine/cache validation remain pending.
This directory is not a claim that a coding agent solved the task.

Transport update: the new OpenAI admission mode passed its synthetic HTTP
check with two upstream JSON/SSE requests and a third locally rejected request.
Capture: `traces/openai-admission-20261103/`. All three body captures verified;
SSE ended with `[DONE]`; cache/token usage remains unknown. This exercises real
HTTP on `net.Pipe`, not the mini-swe-agent SDK or an inference engine.

## Observed task preflight

| Revision | Behavioral cases passed | Targeted failures | Sealed original methods passed |
| --- | --- | --- | --- |
| Baseline | 15 / 21 | 6, exactly the expected bug cases | 4 / 4 |
| Reference | 21 / 21 | 0 | 4 / 4 |

Raw results: `artifacts/task-658-baseline/evaluation.json` and
`artifacts/task-658-reference/evaluation.json`. The execution-time manifest,
evaluator and prompt are preserved in `artifacts/task-658-preflight/`, alongside
complete pre-test export fingerprints. Both exports remained unchanged and
result source hashes matched the preserved snapshots. The current manifest
records the later observed validation; use the saved execution snapshot when
checking the hashes embedded in these results.

The baseline's correctness failure is intentional and verified. It is not
counted as a passed task. No dependencies, model calls or container startup
were needed for this preflight.

## Why this task

[more-itertools issue #658](https://github.com/more-itertools/more-itertools/issues/658)
reports an extra empty group from `split_after` when a positive split limit is
exhausted at the end of the input. The maintainer accepted it and
[PR #659](https://github.com/more-itertools/more-itertools/pull/659) supplies a
small source fix plus regression coverage. This is a real historical bug,
not an injected defect. Its small size makes it suitable for first integration,
not a representative sample of coding difficulty or long-context behavior.
Public history can be memorized by a model; do not infer general capability.

| Item | Frozen selection |
| --- | --- |
| Task ID | `more-itertools-658` |
| Baseline | `6793bd3e4ed15318746ed2511733f12a9932eb64` |
| Reference | `fc1f9acf1b7e11bbc64b54f8385f2e87ecd87452` |
| Relationship | Baseline is the first parent of the reference merge commit |
| Package | `more_itertools`, version 9.0.0 in the inspected baseline |
| License | MIT |
| Agent-editable files | `more_itertools/more.py`, `tests/test_more.py` |
| Evaluator | 21 independent behavioral cases plus four original upstream test methods |
| Interpreter | Existing Python 3.12 planned; no target package installation required |

`manifest.json` records inspected blob identities, boundaries and proposed
capture limits. `prompt.txt` is the agent-visible task statement. Keep the
reference revision, evaluator and manifests outside the agent's task mount.

## Default dry run

This executes only Suez's evaluator code and imports no target repository:

```sh
python3 experiments/agent-task/evaluate.py
```

Expected status is `dry_run`, with `repository_code_executed=false`. Syntax and
dry-run validation do not establish the baseline's or reference's runtime result.

## Authorized task setup and reproduction

The user approved this setup on 2026-11-03. Create one study checkout at
`.project/checkouts/more-itertools-task`, fetch the
two exact revisions and export them to new directories. Refuse to overwrite any
existing checkout/export/control path. These commands have already completed:

```sh
git clone --no-checkout --depth=1 \
  https://github.com/more-itertools/more-itertools.git \
  .project/checkouts/more-itertools-task
git -C .project/checkouts/more-itertools-task fetch --depth=1 origin \
  6793bd3e4ed15318746ed2511733f12a9932eb64 \
  fc1f9acf1b7e11bbc64b54f8385f2e87ecd87452
```

Then export the baseline into
`.project/task-workspaces/more-itertools-658-baseline` and the reference into
`.project/task-controls/more-itertools-658-reference`. `git archive` exports must
omit `.git`; keep any newer repository history in the study checkout, outside
the later agent container. Record the resolved commits and exported file hashes.

Copy the original baseline `tests/test_more.py` to the external control path
`.project/task-controls/more-itertools-658-test_more.py`. Its expected Git blob
identity is `6b78ca53bfef61282a236eaea75fdf2cd78dc733`.
The evaluator checks that identity before importing this sealed test source.
Agent changes to its editable test file do not change the sealed original suite.

No mini-swe-agent clone/install, Docker startup, model request or paid resource
is part of this immediate task-checkout step.

## Baseline and reference checks

These commands ran separately from the Suez root. Use new result paths to repeat:

```sh
/opt/homebrew/bin/python3.12 -B experiments/agent-task/evaluate.py \
  --repo .project/task-workspaces/more-itertools-658-baseline \
  --frozen-tests .project/task-controls/more-itertools-658-test_more.py \
  --expect baseline --run --out artifacts/task-658-baseline/evaluation.json
```

```sh
/opt/homebrew/bin/python3.12 -B experiments/agent-task/evaluate.py \
  --repo .project/task-controls/more-itertools-658-reference \
  --frozen-tests .project/task-controls/more-itertools-658-test_more.py \
  --expect reference --run --out artifacts/task-658-reference/evaluation.json
```

Observed: baseline fails six specified iterator/list regression
cases while its four original upstream methods pass. Reference passes all
21 behavioral cases and the same four sealed methods. Baseline expectation
matching is reported separately from `regressions_passed`: verifying a known
failure must never look like the buggy code passed correctness checks.

The evaluator imports the selected package directly from the export, with a
clean child import path, no bytecode writes and a wall timeout. It preserves
evaluator/manifest/test identities and refuses an existing output file. It checks
listed immutable files, not the whole repository tree. A separate before/after
workspace diff must enforce the editable-path restriction during agent runs.
It is not an OS sandbox. Agent-produced code must be evaluated within the task
isolation boundary, with external orchestration checking the result.

The task/evaluator gate is satisfied. Harness transport integration remains a
separate preflight. In later runs, an import failure, incompatible interpreter
or changed original test count is an unresolved preflight problem, not evidence
of task failure or success.

## Harness and capture specification

Use pinned `DefaultAgent` with Suez's [DirectTextModel](direct_model.py), the
upstream text-action parser and observation formatter. Harness source pin:
`04d809ceab9df28f9adaed044884180159172930`. This is a controlled configuration
with a custom model adapter. Results do not characterize the stock default
harness or prove a prompt optimization. See the
[source note](../../docs/notes/mini-swe-agent-source-paths.md).

The direct adapter uses stdlib HTTP and sends one explicit text-only JSON
request per query. It strips only the harness's local `extra`, rejects unknown
request fields, preserves raw responses in the trajectory, and disables retries,
redirects and environment proxies. It accepts only an explicit loopback `/v1`
endpoint. Refusals, native tool calls, separate reasoning, null content and
truncated responses fail explicitly. No billing cost is invented. SSE, hosted
endpoints and additional message modalities need separate implementation.

Proposed first live bounds remain 12 agent steps, at most 1,024 requested output
tokens per call, 300 seconds total and 20 seconds per shell action. These are
proposed bounds, not authorization for live execution. The Suez proxy must
explicitly enable `-session-protocol openai` with matching model/request/output
bounds; buffering admission affects timing and is not a general billing cap.

## Run the local compatibility check

The [specification](wire-check.json) fixes two synthetic agent steps, loopback
fixtures and a nonexecuting environment. The default is a stdlib-only dry run:

```sh
python3 -B experiments/agent-task/wire_check.py
```

With the approved checkout and isolated dependencies, use a new result path:

```sh
python3 -B experiments/agent-task/wire_check.py --run \
  --harness .project/checkouts/mini-swe-agent \
  --python .project/envs/mini-wire/bin/python \
  --out artifacts/mini-direct-NEW-RUN
```

The saved final run is `artifacts/mini-direct-20261105-05/`. It verified two
upstream requests, HTTP 200/200/403, three complete captures, stable initial
messages and appended history. The quota probe has its own session label, so it
does not join agent history. Zero external network attempts were blocked and
neither LiteLLM nor OpenAI SDK modules were imported. Both agent requests pass
the unchanged strict renderer. Read the
[direct adapter observations](../../docs/notes/direct-harness-observations.md).

The runner checks a clean pinned checkout and 18 inspected blob identities,
snapshots sources, builds Suez, starts owned loopback servers and runs the actual
agent in a child interpreter. It never clones, installs packages or executes an
agent command. Port 18082 must be free; completed prior runs may leave TIME_WAIT
sockets, which the reservation handles with `SO_REUSEADDR`. Owned processes are
stopped on success/failure and capture is drained before integrity checks.

The child has no inherited credentials or proxy settings. Because the harness
loads dotenv at import, the runner selects a new empty global config directory
and sets `PYTHON_DOTENV_DISABLED=1`. A Python audit hook restricts connections to
the local proxy. This is not an OS sandbox for untrusted code. Result directories
retain effective config, source/binary hashes, dependency inventory, trajectory,
private logs, raw captures and derived checks. The fixture emits no token/cache
usage, and missing usage remains null.

The current [requirements](wire-requirements.in) and
[hash lock](wire-requirements.lock) contain five direct harness dependencies and
13 total packages. The stdlib adapter needs no provider SDK. Already installed
versions were retained; offline sync removed 66 unused packages from the isolated
environment, including LiteLLM and OpenAI. No system environment changed. The
older 79-package lock and SDK run sources remain in their private snapshots.

To recreate this environment on the Mac with a new path:

```sh
uv venv --no-config --no-python-downloads \
  --python analysis/tokenizer/.venv/bin/python .project/envs/mini-wire-NEW
uv pip sync --no-config --python .project/envs/mini-wire-NEW/bin/python \
  --require-hashes experiments/agent-task/wire-requirements.lock
```

The base is the existing standalone CPython 3.12.13 used for tokenization.
Homebrew's interpreters previously failed to import `pyexpat`; no system Python
was repaired or changed. The lock was resolved for CPython 3.12/macOS arm64.
Other Python/platform combinations need their own compatibility check. The
agent imports its frozen source directly, without an editable-package install.

Six direct-adapter tests cover explicit payloads, unchanged history, HTTP failure
without retry, response limits, unsupported content, endpoint validation, secret
exclusion and redirect configuration. Ten controller tests cover lifecycle and
isolation boundaries. The complete analysis suite has 76 passing tests on 3.14.

```sh
python3 -B -m unittest discover -s analysis -p test_direct_model.py
python3 -B -m unittest discover -s analysis -p test_wire_check.py
```

## CPU reconstruction and next gate

The final run was reconstructed offline with the already installed pinned Qwen
assets, explicit fixture-model remapping and no model weights or inference:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  analysis/tokenizer/.venv/bin/python -B analysis/reconstruct.py \
  artifacts/mini-direct-NEW-RUN/capture --assets artifacts/qwen3-8b-tokenizer \
  --out artifacts/mini-direct-NEW-RUN/reconstructed --allow-model-remap
python3 -B analysis/reuse.py artifacts/mini-direct-NEW-RUN/reconstructed \
  --trace-dir artifacts/mini-direct-NEW-RUN/capture \
  --out artifacts/mini-direct-NEW-RUN/history
```

Both agent requests render: 45 then 91 tokens, sharing the first 41 IDs. The
first prompt's four-token unshared suffix is its empty thinking generation cue;
the second prompt places the realized assistant text there. The quota probe
renders in its separate session and is excluded by the history analysis because
its HTTP status is 403. These are local structural observations. M4 must establish
engine token identity, cold reset and warm reuse before any cache/latency claims.

The earlier LiteLLM-backed check remains documented in
[its historical note](../../docs/notes/mini-wire-observations.md). Its unknown
metadata rejection was preserved; the renderer was not broadened to ignore it.
The model adapter changed, so these runs alone cannot establish improved reuse.

Use the local model/engine only after M4 calibration. Real model-generated
commands additionally require a pinned isolated task container with bounded
resources and no host credentials, egress, Docker socket or reference/control
mounts. Docker startup and model/GPU spending remain separate authorization
boundaries. The short task is an integration baseline; do not pad it or force a
rewrite to claim natural long-context behavior.

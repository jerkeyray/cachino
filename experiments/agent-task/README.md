# Agent task and sessions

The frozen coding task and the drivers that run a real agent against the
engine through Cachino. Analysis of the resulting captures lives in
[`analysis/`](../../analysis); see [reproduce](../../docs/reproduce.md).

## Task

[more-itertools issue #658](https://github.com/more-itertools/more-itertools/issues/658):
`split_after` returns an extra empty group when a positive split limit is
exhausted at the end of the input. It is a small real bug, fixed upstream in
[PR #659](https://github.com/more-itertools/more-itertools/pull/659), chosen for
integration rather than as a representative coding benchmark.

| Item | Value |
| --- | --- |
| Baseline | `6793bd3e4ed15318746ed2511733f12a9932eb64` |
| Reference fix | `fc1f9acf1b7e11bbc64b54f8385f2e87ecd87452` |
| Editable files | `more_itertools/more.py`, `tests/test_more.py` |
| Evaluator | 21 behavioral cases plus 4 sealed upstream test methods |
| Preflight | Baseline passes 15 of 21 (the 6 expected bug cases fail); reference passes 21 of 21 |

`prompt.txt` is the agent-visible task, `manifest.json` records the pinned blob
identities and `evaluate.py` checks an exported tree (dry run by default; `--run`
executes the target's tests). The study did not run agent edits, so the
evaluator was used only for the preflight.

## Harness

The pinned [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) loop
(`04d809ce`) with its text-based templates, through `direct_model.py`, a
standard-library HTTP adapter that sends exactly the harness's messages.
`wire_check.py` validates the adapter against a local fixture and is reused by
the session driver for its isolated child environment (no inherited
credentials, connections only to the proxy).

## Sessions

`live_cpu.py` runs one session against a running engine through a Cachino proxy
it builds and owns. Commands are never executed: `readonly_env.py` answers
read-only commands (`ls`, `find`, `cat`, `head`, `tail`, `nl`, `sed -n`, `grep`,
`wc`) from the task's baseline tree, read from git objects, and refuses
everything else. `live_concurrent.py` runs several sessions at once on one
engine, each with its own proxy and capture.

| Spec | Session |
| --- | --- |
| `live-cpu.json` | Qwen3-0.6B, 8 turns, one fixed observation |
| `live-cpu-readonly.json` | Qwen3-0.6B, 30 turns, read-only observations |
| `live-cpu-sampled.json` | As above, temperature 0.7 |
| `live-cpu-1.7b.json` | Qwen3-1.7B, 30-turn limit |
| `live-cpu-control.json` | Qwen2.5-0.5B-Instruct control template |
| `live-cpu-concurrent.json` | Base spec for concurrent Qwen3-1.7B sessions |

```sh
curl -s -X POST 127.0.0.1:18000/reset_prefix_cache
python3.14 -B experiments/agent-task/live_cpu.py --run \
  --spec experiments/agent-task/live-cpu-readonly.json --out artifacts/live-NEW
python3.14 -B experiments/agent-task/live_concurrent.py \
  --spec experiments/agent-task/live-cpu-concurrent.json --sessions 3 --out artifacts/concurrent-NEW
```

The pinned harness checkout and its locked environment
(`wire-requirements.lock`) live in ignored `.project/`.

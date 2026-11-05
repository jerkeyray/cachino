# Direct harness capture and local prompt reconstruction

On 2026-11-05, Suez's direct HTTP model adapter completed the pinned
mini-swe-agent loop's two-step fixture and CPU reconstruction checks. It uses
`DefaultAgent`, the upstream text-action parser and observation formatter, and
an environment that records actions without executing them. The model adapter
is custom, so this is a controlled configuration rather than the stock default
harness. No model inference or GPU work occurred.

## Active path and dependencies

[`DirectTextModel`](../../experiments/agent-task/direct_model.py) constructs an
explicit OpenAI-compatible JSON body using stdlib HTTP. Assistant history is
created from role/content, with raw response metadata retained in local `extra`.
The adapter rejects unknown request fields and unsupported response modes;
the strict local renderer was unchanged. It does not import LiteLLM or OpenAI.

The active isolated environment contains 13 hash-locked packages, reduced from
the earlier 79. Five direct dependencies support the pinned harness modules:
Jinja2, Pydantic, Rich, python-dotenv and platformdirs. Installed versions were
retained and offline sync removed the 66 unused packages. No new downloads,
repository clones or system changes were needed in this step. The source pin
remains `04d809ceab9df28f9adaed044884180159172930`; 18 inspected source blobs
were checked before and after execution.

## Verified result

Final evidence: `artifacts/mini-direct-20261105-05/`.

| Observation | Result |
| --- | --- |
| Agent HTTP requests reaching fixture | 2 |
| Agent message counts | 2 then 4 |
| Statuses, including separate parent quota probe | 200, 200, 403 |
| Complete integrity-verified captures | 3 |
| Initial system/task message objects | Unchanged |
| Provider SDK imports / blocked external network attempts | 0 / 0 |
| Strict renderer support for agent requests | Both accepted |
| Actual engine cache or token usage | Unknown |

The third request uses session `mini-wire-quota-probe`; the two agent requests
use `mini-wire`. The history report excludes the 403 probe. All forwarded bodies
matched the fixture's received bytes and replies. The final run retains source
snapshots/hashes, binary, effective spec, installed inventory, trajectory, raw
capture, reconstruction and a prompt-history report. Snapshot/binary hashes were
rechecked, and both owned ports were closed after the run.

## Message append does not imply token append

The existing Qwen3-8B tokenizer snapshot was loaded offline at revision
`b968826d9c46dd6066d109eabc6255188de91218`. Fixture-model remapping was explicit.
Both template tokenization and text-then-encode paths agreed, and text decoded
back exactly. The prompts contain 45 and 91 tokens, sharing their first 41 IDs.

The first prompt's remaining four tokens decode to:

```text
<think>

</think>

```

This is the empty thinking generation cue selected by the pinned template with
`enable_thinking=false`. In the next prompt, the realized assistant content
starts with the fixed action block at that position. A fresh generation cue is
then appended after the new observation. The system and task are unchanged.

`cpu-evidence.json` records the exact unshared IDs and decoded suffix;
`reconstructed/summary.json` records source/token hashes, template switches and
library versions. `history/report.md` reports adjacent and historical LCP 41.

This establishes a local serialization boundary. It does not establish cache
loss: actual generated KV, engine rendering, eligibility, alignment and residency
remain unmeasured. Changing the adapter also means these runs cannot establish
that removing SDK metadata improves reuse or task behavior.

## Next step

Prepare M4 calibration on one pinned engine: prove exact input-ID parity, then
validate a cold reset and a warm repeated hit. Keep the generated-history boundary
explicit when interpreting the 41-token structural prefix. First GPU access is
needed for these engine checks. Real task execution later needs its separate
command-isolation setup; the more-itertools task has not been solved by an agent.

Validation: 76 analysis tests pass on Python 3.14, including six direct-adapter
tests and ten controller tests. The final direct run used the 13-package lock.
Earlier direct runs remain separate; one repeat stopped before child dispatch
because the port reservation did not yet handle TIME_WAIT, which was corrected.

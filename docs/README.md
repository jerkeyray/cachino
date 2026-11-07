# Cachino documentation

Notes are dated where their findings depend on a run. A later result
supersedes an earlier plan; each plan's status line says when it ran.

## Results

| Note | What it shows |
| --- | --- |
| [M4 CPU calibration](notes/m4-cpu-results-20261107.md) | Exact token parity, working cache reset, and reuse equal to the block rule on vLLM's CPU backend |
| [E4 eviction](notes/e4-cpu-results-20261107.md) | A free-queue model of the pinned build predicts reuse under a capped cache exactly; eviction is tail first |
| [Live agent session](notes/live-cpu-results-20261107.md) | Per-turn attribution of a real mini-swe-agent session; reuse stops at the template's thinking cue |
| [30-turn session](notes/live-readonly-results-20261107.md) | Read-only observations; cache served 95% of prompt tokens; recompute split 68% new content, 16% reply, 16% rounding |
| [Cue counterfactual](notes/cue-counterfactual-20261107.md) | Keeping the cue lets the engine reuse each reply's KV, but changed the model's behavior |
| [Proxy overhead](notes/proxy-overhead-20261107.md) | Forwarding adds under 0.5 ms; capture adds about 15 ms per request from durability syncs |

## Plans and procedures

- [Research plan](notes/research-plan.md): question, milestones and gates.
- [Prefill measurement contract](notes/prefill-measurement-contract.md): which numbers mean what.
- [M4 calibration package](notes/m4-calibration-plan.md): what calibration must establish, with source-checked switches.
- [CPU build plan](notes/m4-cpu-build-plan.md): building pinned vLLM for the Mac CPU and launching it.
- [E4 plan](notes/e4-eviction-plan.md): eviction rules from the pinned source and the conditions tested.
- [RunPod runbook](notes/m4-runpod-runbook.md): the deferred GPU path.

## References

- [Trace format](notes/trace-format.md): capture layout and failure policy.
- [vLLM source paths](notes/vllm-source-paths.md) and [mini-swe-agent source paths](notes/mini-swe-agent-source-paths.md): pinned code read for each claim.
- [Related work check](notes/related-work-check-20261101.md).

## Earlier observations

These ran before engine validation and describe local structure only:
[CPU prefix observations](notes/cpu-prefix-observations.md),
[prompt history](notes/prompt-history-observations.md),
[Claude capture audit](notes/claude-capture-audit.md),
[direct harness](notes/direct-harness-observations.md) and
[LiteLLM wire check](notes/mini-wire-observations.md).

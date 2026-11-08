# Run records

Dated notes written as each experiment ran. Each names its run directory, spec
or probe hashes and code commit. The [report](../report.md) summarizes them;
these keep the full detail. Raw captures stay private in ignored `artifacts/`
and `traces/`.

| Record | Contents |
| --- | --- |
| [m4-calibration-plan](m4-calibration-plan.md) | What calibration must establish; source-checked vLLM switches |
| [m4-cpu-build-plan](m4-cpu-build-plan.md) | Building the pinned vLLM for the Mac CPU |
| [m4-cpu-results-20261107](m4-cpu-results-20261107.md) | Calibration: parity, reset, block rule |
| [e4-eviction-plan](e4-eviction-plan.md) | Eviction rules read from the pinned source |
| [e4-cpu-results-20261107](e4-cpu-results-20261107.md) | Eviction probes under a capped cache |
| [proxy-overhead-20261107](proxy-overhead-20261107.md) | Forwarding and capture latency |
| [live-cpu-results-20261107](live-cpu-results-20261107.md) | First live agent session |
| [cue-counterfactual-20261107](cue-counterfactual-20261107.md) | Keeping the thinking cue in history |
| [live-readonly-results-20261107](live-readonly-results-20261107.md) | 30-turn sessions, greedy and sampled |
| [live-1.7b-results-20261107](live-1.7b-results-20261107.md) | Varied session with Qwen3-1.7B |
| [concurrent-results-20261107](concurrent-results-20261107.md) | Three concurrent sessions and repetitions |
| [cache-sweep-20261107](cache-sweep-20261107.md) | Fixed trace at six cache sizes |
| [related-work-check-20261101](related-work-check-20261101.md), [-20261107](related-work-check-20261107.md) | Prior-work checks and search records |
| [repetitions-20261108](repetitions-20261108.md) | Seeded repetitions and threshold location |
| [control-template-20261108](control-template-20261108.md) | Qwen2.5 control template |
| [baseline-20261108](baseline-20261108.md) | Naive prefix baseline against the cache predictors |
| [direct-harness-observations](direct-harness-observations.md) | Harness adapter wire check |
| [prefill-measurement-contract](prefill-measurement-contract.md), [vllm-source-paths](vllm-source-paths.md), [mini-swe-agent-source-paths](mini-swe-agent-source-paths.md), [trace-format](trace-format.md) | Measurement contract and source references |

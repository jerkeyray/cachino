# Prediction exactness across all runs, 2026-11-08

Question: on which requests did the free-queue predictor match vLLM's reported
`cached_tokens`? Summarized in
[report 3.1](../report.md#31-the-predictions-are-exact).

| Test | Requests exact | Record |
| --- | --- | --- |
| Calibration probes | 17 / 17 steps | [m4-cpu-results](m4-cpu-results-20261107.md) |
| Capped-cache eviction probes | 27 / 27 | [e4-cpu-results](e4-cpu-results-20261107.md) |
| Sequential agent sessions (5 runs, 3 models) | every turn | [live-readonly](live-readonly-results-20261107.md), [live-1.7b](live-1.7b-results-20261107.md), [control-template](control-template-20261108.md) |
| Fixed trace at 7 cache sizes, CPU | 30 / 30 at each | [cache-sweep](cache-sweep-20261107.md), [repetitions](repetitions-20261108.md) |
| Same trace at 4 cache sizes, Tesla T4 | 30 / 30 at each | `experiments/gpu-confirmation/` |
| smolagents sessions, 4 runs | 23 / 23 | [smolagents](smolagents-20261108.md) |
| Sandboxed sessions, both templates, with reply IDs | 120 / 120 | [sandbox-cue](sandbox-cue-20261108.md) |
| Concurrent sessions, 9 seeded runs | 259 / 265 | [concurrent-results](concurrent-results-20261107.md), [repetitions](repetitions-20261108.md) |
| Session-cap runs, seeds 4 and 5 | 147 / 158 | [session-limits](session-limits-20261108.md) |
| Token-budget runs, both versions, 8 runs | 304 / 315 | [session-limits](session-limits-20261108.md) |
| Timed overlap replay, 3 evicting runs | 93 / 98 | [repetitions](repetitions-20261108.md) |

Engine prompt IDs equalled the local render on every replayed request. Under
overlap, the misses were off by one to a few blocks, except in the two most
evicting cap runs, where they reached 26 blocks.

## Decode-block timing

Replaying three evicting runs with their recorded timing, a predictor that
takes decode blocks at admission was exact on 93 of 98 requests, one that takes
them at completion on 92, and one that ignores them on 88. The engine allocates
as it decodes, between the first two, so decode-block timing is the likely
source of the remaining error. Confirming it needs engine-side allocation
events, such as vLLM's `BlockStored` and `BlockRemoved` KV cache events.

## Modeling choices

Every choice behind the predictions, why it matters and how it was checked is
in [assumptions](assumptions.md).

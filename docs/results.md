# Results

All engine results come from vLLM at commit `2173d712`, built for the Mac CPU,
with Qwen3-0.6B, Qwen3-1.7B or Qwen2.5-0.5B-Instruct and 32-token blocks. They
measure tokens, not latency. Each section links to its dated run record in
[archive](archive/).

**Main claim:** from a raw capture, Cachino predicts how much prefix reuse each
request gets and attributes each request's lost reuse to prompt change, block
rounding, eviction or sharing with other sessions. Both underlying mechanisms
are already reported elsewhere ([related work](related-work.md)); the exact,
engine-validated attribution is the contribution.

## Predictor accuracy

| Test | Requests exact | Record |
| --- | --- | --- |
| Calibration probes | 17 / 17 steps | [M4](archive/m4-cpu-results-20261107.md) |
| Capped-cache eviction probes | 27 / 27 | [E4](archive/e4-cpu-results-20261107.md) |
| Sequential agent sessions (5 runs, 3 models) | every turn | see below |
| Fixed trace at 7 cache sizes | 30 / 30 at each | [sweep](archive/cache-sweep-20261107.md), [stage 2](archive/stage2-results-20261108.md) |
| Concurrent sessions, 9 seeded runs | 259 / 265 (misses only under eviction, 1 to 3 blocks) | [stage 2](archive/stage2-results-20261108.md) |
| 5 scheduling orders | 39 / 39 each | this page |

Engine prompt token IDs equalled the local render on every replayed request.

## Calibration and eviction rules

Reuse equals the exact shared token prefix, capped at prompt length minus 1,
rounded down to whole blocks. Under a capped cache, vLLM evicts each prompt
from its end, so the start of a prompt survives longest. A replay of the
pinned free-queue rules reproduced every hit count. Details:
[M4](archive/m4-cpu-results-20261107.md), [E4](archive/e4-cpu-results-20261107.md).

## Single agent sessions

The pinned mini-swe-agent loop on the frozen `split_after` task, with
observations from a read-only emulator over the task repository. No command
was executed.

| Session | Reused | Recomputed: new content / previous reply / block rounding |
| --- | --- | --- |
| Qwen3-0.6B, 30 turns, greedy | 95% | 68% / 16% / 16% |
| Qwen3-0.6B, 30 turns, sampled | 95% | 64% / 19% / 17% |
| Qwen3-1.7B, 10 turns, varied actions | 79% | 91% / 7% / 2% |

Nothing was evicted. Records: [8 turns](archive/live-cpu-results-20261107.md),
[30 turns](archive/live-readonly-results-20261107.md), [1.7B](archive/live-1.7b-results-20261107.md).

## Template boundary

With Qwen3 and thinking off, the template puts an empty `<think></think>` cue in
the generation prompt but not in history, so every turn re-prefills the
previous reply.

- Keeping the cue in history restored reuse of each reply's KV and cut
  recomputation on turns 1 to 7 from 670 to 362 tokens, but changed the model's
  next action ([counterfactual](archive/cue-counterfactual-20261107.md)).
- With Qwen2.5, whose template renders history like the generation prompt, the
  next prompt continued the previous one token for token, and the engine reused
  a 442-token reply's KV: 1,280 tokens cached against 832 without it
  ([control](archive/control-template-20261108.md)).
- With either template, a reply the harness rejects is dropped, and the next
  prompt diverges at the role marker.

## Concurrent sessions and cache size

Three or four sessions shared one 14,016-token cache. Eviction appeared only
when their combined working set exceeded the cache: in 2 of 9 seeded runs, at
17% to 18% of recomputed tokens, and 25% in an earlier run. Session count alone
did not predict it. The capture-based threshold did, for all 9 runs.

One fixed three-session trace, replayed at each size:

| Cache (blocks) | Reused | Lost to eviction | Share of recomputed |
| --- | --- | --- | --- |
| 384 | 73.0% | 12,832 | 43% |
| 438 | 80.6% | 4,512 | 21% |
| 487 | 84.6% | 32 | 0.2% |
| 488 and up | 84.6% | 0 | 0% |

Records: [concurrent](archive/concurrent-results-20261107.md),
[sweep](archive/cache-sweep-20261107.md), [stage 2](archive/stage2-results-20261108.md).

## Scheduling (stage 4)

Run `schedule-replay-20261108-01`, `experiments/agent-task/schedule_replay.py`.
The four-session run that evicted (seed 2, 39 requests) was replayed at its own
cache size, 438 blocks, in five orders. Each session's turns kept their order,
so prompts and model behavior were unchanged. Delay is how many positions later
than its recorded position each request was served, a proxy for queueing.

| Order | Recomputed | Lost to eviction | Mean delay | Max delay | Engine exact |
| --- | --- | --- | --- | --- | --- |
| Recorded interleaving | 26,363 | 4,352 | 0 | 0 | 39 / 39 |
| At most 3 sessions at once | 26,171 | 4,160 | 3.95 | 26 | 39 / 39 |
| At most 2 sessions at once | 22,011 | 0 | 4.62 | 18 | 39 / 39 |
| Greedy by predicted reuse | 22,011 | 0 | 4.33 | 23 | 39 / 39 |
| One session at a time | 22,011 | 0 | 5.67 | 26 | 39 / 39 |

Limiting concurrency to two sessions, or ordering greedily by predicted reuse,
removed eviction and cut recomputation by 16.5%, at a mean cost of 4 to 5
positions of delay. Every order matched its prediction exactly. This evaluates
capture-guided scheduling on one trace; CacheWise's prefix-aware scheduling is
the closest published approach.

## Proxy overhead

Forwarding adds about 0.24 ms to a JSON exchange and 0.4 ms to the first SSE
event. Capture adds about 15 ms per request on macOS, from a durability sync
after each write, so latency passes run with capture off
([overhead](archive/proxy-overhead-20261107.md)).

## Limits

- One task, one harness, small models, one machine. Shares are not typical of
  production traffic.
- Token counts on CPU only; no latency claims.
- The scheduling test replays sequentially, without overlap or generated tokens,
  and measures delay in positions, not time.

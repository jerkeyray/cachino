# Cachino: where coding agents lose prefix reuse

## Question

Coding agents resend their whole conversation every turn. A server with a
prefix cache can skip the shared part, but only if it is still cached and the
new prompt matches it token for token. When a turn is slow, how much of the
redone prefill came from the prompt changing, from block granularity, or from
the cache evicting what the agent needed?

## Approach

Cachino captures agent traffic byte for byte, rebuilds the exact token IDs the
model sees, and replays the pinned vLLM build's cache rules on them. For every
request it predicts the cached tokens the engine will report, then splits the
rest into new content, the previous reply prefilled again, block rounding,
eviction, and reuse gained from other sessions. Each prediction was checked
against the engine's reported counts. Agents were real (the pinned
mini-swe-agent loop on a frozen task), but no command was executed: a
read-only emulator answered from the task repository. Details: [method](method.md).

## Results

**The predictions are exact.** On every sequential request tested, on the Mac
CPU and on a Tesla T4, predicted and reported cached tokens were equal, and the
engine's prompt token IDs equalled the local render. Under concurrency, 93 of
98 timed-replay requests were exact; the rest were off by a few blocks, from
when decode blocks are allocated.

**In single sessions, most recomputation is new content.** The avoidable part
is the previous reply, prefilled again because Qwen3's template drops the empty
thinking cue from history, plus up to one block of rounding. A template that
keeps history aligned (Qwen2.5) let the engine reuse a 442-token reply.

![Recomputed tokens by cause](figures/recompute-split.svg)

**Shared caches evict, and the threshold is predictable.** Sessions sharing a
cache lost 17% to 25% of their recomputation to eviction when their combined
working set exceeded it, and none otherwise. From the capture alone, the
predicted threshold sorted 9 of 9 independent runs correctly, and the engine
matched the predicted curve to the block on both CPU and GPU.

![Eviction against cache size](figures/eviction-curve.svg)

**Eviction costs time.** On the T4, each recomputed token added about 1.34 ms
to time to first token. Below the threshold, eviction added 31% and 86% to
total time to first token for the same 30 requests.

![Time to first token by cache size](figures/gpu-ttft.svg)

**Scheduling can remove it.** Replaying an evicting four-session run with at
most two sessions in flight, or ordering turns greedily by predicted reuse,
removed eviction and cut recomputation by 16.5%, at a cost of 4 to 5 positions
of delay per request.

![Scheduling orders](figures/scheduling.svg)

## What is new

Both mechanisms are already reported: the Qwen3 template issue upstream, and
eviction under agent load by several serving systems ([related work](related-work.md)).
Cachino's part is exact, engine-validated attribution of each request's lost
reuse from a raw capture, and the measured sizes of each cause.

## Limits

One task, one harness, small models (Qwen3 0.6B and 1.7B), one Mac and one T4.
Timing comes from one sequential GPU replay. The scheduling result is a
sequential replay with delay counted in positions, not wall-clock time.
Commands were never executed, so trajectories differ from a real environment.

## Reproducing

Every number links to a dated record in the [archive](archive/), which names
its run directory, spec hash and code commit. Raw captures stay private;
`experiments/figures/make_figures.py` regenerates these figures from them.
Full tables: [results](results.md).

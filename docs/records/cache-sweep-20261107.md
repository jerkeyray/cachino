# Eviction against cache size, 2026-11-07

Run `cache-sweep-20261107-01`. The 30 captured prompts of the concurrent run
`live-concurrent-20261107-01` (three sessions, see
[concurrent results](concurrent-results-20261107.md)) were replayed at six
cache sizes. Traffic was fixed and only the cache changed.

## Method

`experiments/agent-task/cache_sweep.py` renders each captured prompt to its
exact token IDs and sends them one at a time, in the recorded start order, to
`/v1/completions` with `max_tokens` 1, after a cache reset. For each size, the
server was restarted with `--kv-cache-memory-bytes` set to a whole number of
32-token blocks, and the block count was read back from the startup log.
The free-queue predictor's expected hit counts were computed for that block
count before any request.

The replay engine served Qwen3-0.6B, not the captured Qwen3-1.7B. Both have
byte-identical tokenizer files and the same per-token KV size (28 layers x 8 KV
heads x 128 x 2 x 2 bytes = 114,688 bytes), so cache accounting depends only on
the token IDs, and the smaller model leaves room for larger caches. Each
request's engine prompt IDs were checked against the local render.

The replay leaves out request overlap and generated tokens, which also occupy
blocks in a live run. It therefore shows less eviction than the live run did
at the same size (4,512 against 5,536 tokens at 14,016).

## Findings

All five sizes that started matched the predictor on all 30 requests, with
exact prompt-ID parity on all 30.

| Cache (tokens) | Blocks | Reused | Recomputed | Lost to eviction | Eviction share of recomputed |
| --- | --- | --- | --- | --- | --- |
| 12,288 | 384 | 73.0% | 29,759 | 12,832 | 43% |
| 14,016 | 438 | 80.6% | 21,439 | 4,512 | 21% |
| 16,384 | 512 | 84.6% | 16,927 | 0 | 0% |
| 20,480 | 640 | 84.6% | 16,927 | 0 | 0% |
| 28,672 | 896 | 84.6% | 16,927 | 0 | 0% |

1. **There is a cliff, not a slope.** From 16,384 tokens up, nothing was
   evicted and recomputation sat at its floor of 16,927 tokens: new content,
   re-prefilled replies and block rounding. Shrinking the cache by 15%, to
   14,016, added 4,512 tokens of eviction. Shrinking it by 25%, to 12,288, added
   12,832, which raised total recomputation by 76% over the floor.
2. **The threshold is the combined working set.** Eviction starts once the
   cache is smaller than what the three interleaved sessions need to keep
   resident between their turns. Beyond that, extra cache buys nothing for this
   traffic.
3. **Cross-session reuse was 1,824 tokens at every size.** The shared system and
   task prompt stayed resident, because the sessions touched it first and the
   free queue evicts from the tail of each prompt.
4. **The 10,240-token size could not start.** vLLM requires room for one
   request at the maximum context length on top of the null block, and 320
   blocks left 319 usable, one short.

## Interpretation

For this traffic, the recomputation an agent harness causes is fixed (about
15% of prompt tokens here). Eviction adds to it only when concurrent sessions
outgrow the cache, and then grows quickly. The free-queue model predicts that
cliff exactly for sequential replay, so it can estimate, from a capture alone,
how much cache a given mix of sessions needs before any of their prefixes are
lost.

## Limits

- One captured run of three sessions; one machine; CPU token counts only.
- Sequential replay without overlap or generated tokens. The live run showed a
  little more eviction at 14,016 tokens, and the overlap predictor was exact on
  26 of 30 there.
- Sizes between 14,016 and 16,384 tokens were not tested, so the exact
  threshold is bracketed, not located.

# Result card: 487 versus 488 blocks

[← Documentation](README.md) · [Report 3.5](report.md#35-shared-caches-and-the-eviction-threshold) · [How to read the results](reading-results.md)

## Claim

From a capture alone, Cachino predicted that replaying one three-session agent
trace through a 487-block prefix cache would lose exactly 32 tokens to eviction,
and that 488 blocks would lose none. On 8 November 2026, vLLM matched both
predictions on all 30 requests, with the engine's prompt token IDs equal to the
local render on every request.

| Blocks | Predicted eviction | Engine eviction | Requests exact | Prompt-ID parity |
| --- | --- | --- | --- | --- |
| 487 | 32 | 32 | 30 / 30 | 30 / 30 |
| 488 | 0 | 0 | 30 / 30 | 30 / 30 |

One block of 32 tokens is the smallest change the cache can make, so the
threshold was located at the engine's own resolution.

## Conditions

| Item | Value |
| --- | --- |
| Trace | `live-concurrent-20261107-01`: three concurrent mini-swe-agent sessions, Qwen3-1.7B, 30 requests, 110,271 prompt tokens |
| Replay run | `cache-sweep-20261108-01`, sequential: prompts in recorded start order, one at a time, `max_tokens` 1, after a cache reset |
| Engine | vLLM `0.33.0rc1.dev595+g2173d7121`, built from source for the Mac CPU (Apple M5); prefix caching, 32-token blocks, SHA-256 block hashes |
| Replay model | Qwen3-0.6B at `c1899de2`, FP16; same tokenizer files and KV size per token as the captured 1.7B model |
| Cache size | Set in whole blocks with `--kv-cache-memory-bytes`; block count read back from the engine log |
| Code | Repository at `d75a09a` when the run started; replay script added in `f880ee6` |

## Evidence

- The [repetitions record](records/repetitions-20261108.md#locating-the-threshold-on-the-engine)
  gives the run and the table above.
- The [cache-sweep record](records/cache-sweep-20261107.md) gives the method and
  the same trace at five more sizes, all exact on 30 of 30.
- The [GPU confirmation](report.md#36-what-eviction-costs-in-time) replayed the
  trace on a Tesla T4 at 384, 438, 488 and 640 blocks: identical cached-token
  totals to the CPU, and 30 of 30 exact at each size. 487 blocks was not run on
  the T4.
- [`results/claims.json`](../results/claims.json), claim `threshold-487-488`, states
  this claim, its runs and scope as data, with the checksum of the table it rests on.
- `figures/data.json` (`reuse_curve.predicted_487_488`) holds the predicted
  values, regenerated from the capture by `experiments/figures/make_figures.py`.

**What can be checked here.** The per-request rows for all seven CPU sizes are
released in [results/cache-sweep](../results/cache-sweep/requests.csv), and
`python3 analysis/validate_results.py` recomputes this table from them: the
predictions, the engine's cached tokens, the bounds and the eviction totals.
The capture itself stays private, because it holds prompt and reply text, so
the rows can be checked for consistency but not traced back to the prompts.
[examples/one-block](../examples/README.md) runs the same predictor on a
synthetic trace with the same shape, 34 versus 35 blocks.

## Scope and limitations

This shows that the predictor locates one trace's eviction threshold exactly,
for sequential replay, on one vLLM build.

It does not show:

- behavior under overlapping requests, where generated tokens also hold blocks
  and the predictor is exact on most but not all requests (3.1);
- a threshold for other traffic: the threshold belongs to this trace's
  combined working set;
- anything about larger models, other engines, other block sizes, or
  production caches;
- an effect on latency: 487 blocks was not timed. By the fit in 3.6, 32
  recomputed tokens add about 0.04 s, less than the 0.3 s between the two T4
  rounds at 488 blocks.

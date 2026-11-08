# Where coding agents lose prefix reuse

*A measurement study with Cachino, November 2026.*

## Abstract

Coding agents resend their whole conversation on every turn, and serving
engines skip the shared part only if it is still in their prefix cache and
matches token for token. We built Cachino, which captures agent traffic byte for
byte, rebuilds the exact token IDs the model sees, and replays a pinned vLLM
build's cache rules to predict each request's reused tokens and attribute the
rest to prompt change, block rounding, eviction or sharing with other sessions.
The predictions matched vLLM's reported counts on every sequential request
tested, on a Mac CPU and on a Tesla T4. Running mini-swe-agent with Qwen3 on a
frozen task, we find that in single sessions most recomputation is new content,
with a fixed extra cost from Qwen3's chat template; that sessions sharing a
cache evict each other past a threshold predictable from the capture; that on
a T4 each recomputed token adds about 1.34 ms to time to first token; and that
limiting concurrency removed eviction on one trace, cutting recomputation 16.5%.
Both mechanisms were already known; the contribution is exact, engine-validated
attribution.

## 1. Question

When an agent turn's prefill is slow, how much of the redone work came from the
prompt changing, from the cache's block granularity, or from the cache evicting
what the agent needed? A server reports only one number per request, the cached
tokens. We split that number into its causes, and check every prediction
against the engine.

## 2. Method

![Pipeline](figures/pipeline.svg)

1. **Capture.** The Go proxy forwards requests unchanged and stores request and
   response bytes with lifecycle events ([trace format](records/trace-format.md)).
2. **Render.** `analysis/reconstruct.py` turns each captured request into the
   exact token IDs the model sees, using a pinned tokenizer and chat template.
3. **Predict.** `analysis/predict.py` replays the pinned vLLM free-queue rules on
   those IDs:
   - block 0 is a placeholder, and the other blocks start in one free queue;
   - new blocks come from the head, and taking a block that still holds a prefix
     hash evicts it;
   - a hit is the longest chain of cached full blocks from the start, capped at
     prompt length minus 1;
   - finished requests free their blocks last first, unhashed blocks to the head,
     hashed blocks to the tail;
   - a reset clears hashes and keeps the queue order.

   Source lines are cited in the [eviction plan](records/e4-eviction-plan.md).
   For overlapping traffic the predictor holds each request's blocks until it
   finishes and queues requests that do not fit.
4. **Attribute.** Each request's recomputed tokens are split into new content,
   the previous reply prefilled again, block rounding (including the final
   prompt token, which is never served from cache), eviction (reuse below the
   session's own prompt bound) and cross-session reuse (reuse above it).
5. **Check.** Replays send the same token IDs to the engine with
   `return_token_ids` after a reset, and compare the engine's prompt IDs and
   cached tokens with the prediction.

Missing cache metrics are recorded as unknown, never zero, and matching token
counts are never taken as matching tokens.

### Setup

| Item | Value |
| --- | --- |
| Engine | vLLM at `2173d712`, built from source for the Mac CPU (Apple M5, 16 GiB); the matching wheel on a Tesla T4 |
| Cache | Prefix caching, 32-token blocks, SHA-256 block hashes, capacity set in whole blocks |
| Models | Qwen3-0.6B (`c1899de2`), Qwen3-1.7B (`70d244cc`), Qwen2.5-0.5B-Instruct (`7ae55760`) as a control; FP16 |
| Agent | mini-swe-agent at `04d809ce` with its text-based templates, through a minimal HTTP adapter |
| Task | Fix `split_after` in more-itertools (issue 658), frozen at its baseline revision |
| Environment | Commands are never executed; a read-only emulator answers `ls`, `cat`, `grep` and similar from the repository's baseline tree and refuses edits |

Qwen3-0.6B and 1.7B have byte-identical tokenizer files and the same KV size per
token, so their captures replay on either. vLLM samples with seed 0 unless told
otherwise, so independent runs used `--seed`.

## 3. Results

### 3.1 The predictions are exact

| Test | Requests exact |
| --- | --- |
| Calibration probes | 17 / 17 steps |
| Capped-cache eviction probes | 27 / 27 |
| Sequential agent sessions (5 runs, 3 models) | every turn |
| Fixed trace at 7 cache sizes, CPU | 30 / 30 at each |
| Same trace at 4 cache sizes, Tesla T4 | 30 / 30 at each |
| Concurrent sessions, 9 seeded runs | 259 / 265 |
| Timed overlap replay, 3 evicting runs | 93 / 98 |

Engine prompt IDs equalled the local render on every replayed request. Under
overlap, the few misses were off by one to a few blocks. Replaying three
evicting runs with their recorded timing, a predictor that takes decode blocks
at admission was exact on 93 of 98 requests, one that takes them at completion
on 92, and one that ignores them on 88. The engine allocates as it decodes,
between the first two, so the remaining error is decode-block timing, not the
eviction rules ([repetitions](records/repetitions-20261108.md)).

### 3.2 Single sessions: mostly new content

![Recomputed tokens by cause](figures/recompute-split.svg)

| Session | Reused | Recomputed: new content / previous reply / block rounding |
| --- | --- | --- |
| Qwen3-0.6B, 30 turns, greedy | 95% | 68% / 16% / 16% |
| Qwen3-0.6B, 30 turns, sampled | 95% | 64% / 19% / 17% |
| Qwen3-1.7B, 10 turns, varied actions | 79% | 91% / 7% / 2% |

Nothing was evicted. The 0.6B model repeated `ls -la`; the 1.7B model read
files and searched, so its observations were larger and the fixed costs a
smaller share ([30 turns](records/live-readonly-results-20261107.md),
[1.7B](records/live-1.7b-results-20261107.md)).

### 3.3 The template boundary

With thinking off, Qwen3's template puts an empty `<think></think>` cue in the
generation prompt but not in history, so each turn diverges from the previous
generation context and re-prefills the reply just generated.

- With the cue kept in history, the engine reused each reply's KV, and
  recomputation on turns 1 to 7 fell from 670 to 362 tokens; but the model's
  next action changed ([counterfactual](records/cue-counterfactual-20261107.md)).
- Qwen2.5's template renders history like the generation prompt. The next prompt
  continued the previous one token for token, and the engine reused a 442-token
  reply: 1,280 cached tokens against 832 without it
  ([control](records/control-template-20261108.md)).
- With either template, a reply the harness rejects for its format is dropped,
  and the next prompt diverges at the role marker.

### 3.4 Shared caches and the eviction threshold

Concurrent sessions on one 14,016-token cache evicted each other in 2 of 9
seeded runs, losing 17% to 18% of recomputed tokens (25% in an earlier run).
Session count did not predict which runs evicted; the threshold computed from
each capture did, for all 9 runs ([concurrent](records/concurrent-results-20261107.md),
[repetitions](records/repetitions-20261108.md)).

![Eviction against cache size](figures/eviction-curve.svg)

One three-session trace replayed at each size: nothing is lost from 488 blocks
up, and eviction grows steadily below. Engine points on the CPU and the T4 sit
on the predicted curve, including 32 tokens lost at 487 blocks and none at 488
([sweep](records/cache-sweep-20261107.md)).

### 3.5 What eviction costs in time

![Time to first token by cache size](figures/gpu-ttft.svg)

On a Kaggle Tesla T4 (pinned wheel, torch 2.14.0 for CUDA 13.0, FP16, capture
off, two rounds per size), the same 30 requests took 23.5 s of total time to
first token without eviction, 30.9 s at 438 blocks (+31%) and 43.9 s at 384
blocks (+86%). A fit over 240 requests gives about 1.34 ms per recomputed token
plus 64 ms per request. Time to first token is vLLM's per-request metric from
scheduling; requests were sent one at a time.

### 3.6 Scheduling to avoid eviction

![Scheduling orders](figures/scheduling.svg)

The four-session run that evicted was replayed at its own cache size in five
orders, keeping each session's turns in order so prompts and behavior were
unchanged. Delay is how many positions later than recorded each request ran.

| Order | Recomputed | Lost to eviction | Mean delay | Max delay |
| --- | --- | --- | --- | --- |
| Recorded interleaving | 26,363 | 4,352 | 0 | 0 |
| At most 3 sessions at once | 26,171 | 4,160 | 3.95 | 26 |
| At most 2 sessions at once | 22,011 | 0 | 4.62 | 18 |
| Greedy by predicted reuse | 22,011 | 0 | 4.33 | 23 |
| One session at a time | 22,011 | 0 | 5.67 | 26 |

Every order matched its prediction on all 39 requests. Two sessions at a time,
or a greedy order, removed eviction and cut recomputation by 16.5%.

### 3.7 A live concurrency limit

Cachino's proxy can cap active sessions (`-max-active-sessions`; a session stays
active between its turns for an idle period, and new sessions wait for a free
slot). Four concurrent Qwen3-1.7B sessions shared one proxy and one 438-block
cache, with the cap off and at 2, over three engine seeds (run
`limit-matrix-20261108`). Runs without the cap reproduced the earlier seeded
runs exactly.

| Seed | Cap | Requests | Lost to eviction | Engine time to first token | Wall clock | Waits |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | off | 40 | 0 | 77.1 s | 201.8 s | none |
| 1 | 2 | 30 | 0 | 55.5 s | 189.3 s | 101 s, 117 s |
| 2 | off | 39 | 4,640 | 337.8 s | 387.5 s | none |
| 2 | 2 | 40 | 0 | 212.2 s | 373.7 s | 197 s, 217 s |
| 3 | off | 40 | 0 | 139.2 s | 241.6 s | none |
| 3 | 2 | 37 | 0 | 191.0 s | 303.6 s | 120 s, 140 s |

- The cap removed eviction in the one seed whose sessions outgrew the cache.
- Where nothing would have been evicted, it only made two sessions wait, and
  total wall clock rose by up to 26%.
- The predictor was exact on 223 of 226 requests, all misses in the evicting run.

Changing the interleaving changes which sampled replies are generated, so
trajectories, request counts and recomputed totals differ between paired runs;
eviction is the comparable measure. A fixed cap pays off only when the combined
working set exceeds the cache, which the capture-based threshold (3.4)
predicts. Timing is on the Mac CPU and compares conditions only.

### 3.8 Proxy overhead

Forwarding adds about 0.24 ms to a JSON exchange and 0.4 ms to the first SSE
event. Capture adds about 15 ms per request on macOS, because each event and
body is synced to disk so that a crash leaves a complete record; timing runs
therefore use capture off ([overhead](records/proxy-overhead-20261107.md)).

## 4. Related work

| Result | Closest work | Overlap |
| --- | --- | --- |
| Agent traffic capture and harness comparison | [TraceLab](https://arxiv.org/html/2606.30560v2), [The Cache Wars](https://agnt.gg/whitepapers/the-cache-wars-prompt-cache-efficiency-llm-agent-harnesses), [Agentic Coding in the Wild](https://arxiv.org/html/2608.00101v1) | Covered |
| Context policy, reuse and quality | [The Physical Cost of Context](https://arxiv.org/html/2610.34763v1) | Covered |
| Qwen3 template boundary | [Qwen3 issue #1826](https://github.com/QwenLM/Qwen3/issues/1826) | Known since March 2026, with timings on mlx-lm |
| Eviction under agent load | [CacheWise](https://arxiv.org/abs/2606.16824), [Continuum](https://arxiv.org/abs/2511.02230), [Penelope](https://arxiv.org/abs/2611.00391) | Known; these build policies on it |
| Predicting reuse from traces | [vLLM native simulator PR](https://github.com/vllm-project/vllm/pull/47922) (open), [PrefixSim](https://github.com/cacheMon/PrefixSim) | Partly; no per-request agreement with an engine shown |

What we did not find elsewhere is per-request agreement with a real engine's
reported counts, and a per-request split of each loss into its causes from a
raw capture. Search records: [first check](records/related-work-check-20261101.md),
[follow-up](records/related-work-check-20261107.md).

## 5. Limitations

- One task, one harness and small models (0.6B to 1.7B). The shares describe
  this setup, not production traffic.
- Commands were never executed, so trajectories differ from a real environment,
  and task success was not measured.
- Timing comes from one sequential replay on one GPU.
- The scheduling replay counts delay in positions; the live cap test measures
  wall-clock time, but only on the Mac CPU, over three seeds, with one task.
- vLLM's native simulator could not run on the Mac (it needs `triton-cpu` on
  x86_64 Linux), so the predictor was not compared with it.

## 6. Future work

A second harness and real tasks with execution; a cap that switches on only
when the predicted working set exceeds the cache; larger models on faster GPUs;
and a comparison with vLLM's native simulator once it is merged.

## 7. Reproducing

Commands are in [reproduce](reproduce.md). Each [record](records/) names its run
directory, spec hash and code commit; raw captures stay private, and
`experiments/figures/make_figures.py` regenerates the figures from them.

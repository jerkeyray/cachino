# Stage 2 results: repeated concurrency and the eviction threshold, 2026-11-08

Stage 2 of the [next stages plan](next-stages-plan.md). It tests whether the
concurrent-session findings hold over independent runs, and whether the
eviction threshold can be predicted from a capture alone.

## Repetition matrix

Run `concurrency-matrix-20261107`: 3 engine seeds x 2, 3 and 4 concurrent
sessions, 9 runs. Each run started a fresh vLLM server with `--seed`, so runs
are independent and each one can be replayed exactly. Other settings matched the
[concurrent run](concurrent-results-20261107.md): Qwen3-1.7B at temperature 0.7,
step limit 10, read-only snapshot observations, a 14,016-token cache (438 blocks
of 32). Analysis used `concurrent_attribution.py` and `matrix_summary.py`.

| Run | Requests | Reused | Eviction (share of recomputed) | Cross-session reuse | Overlap predictor exact |
| --- | --- | --- | --- | --- | --- |
| seed 1, 2 sessions | 20 | 85.9% | 0 | 832 | 20 / 20 |
| seed 2, 2 sessions | 20 | 83.9% | 0 | 832 | 20 / 20 |
| seed 3, 2 sessions | 20 | 84.4% | 0 | 864 | 20 / 20 |
| seed 1, 3 sessions | 29 | 74.2% | 3,680 (18.1%) | 1,824 | 24 / 29 |
| seed 2, 3 sessions | 30 | 84.3% | 0 | 1,824 | 30 / 30 |
| seed 3, 3 sessions | 27 | 79.6% | 0 | 1,696 | 27 / 27 |
| seed 1, 4 sessions | 40 | 87.9% | 0 | 2,656 | 40 / 40 |
| seed 2, 4 sessions | 39 | 73.8% | 4,640 (17.4%) | 2,656 | 38 / 39 |
| seed 3, 4 sessions | 40 | 87.2% | 0 | 2,560 | 40 / 40 |

Some sessions ended before their step limit, at the context limit or the
768-token reply limit, so request counts vary.

1. **Session count does not decide eviction.** Two of nine runs saw eviction,
   one with three sessions and one with four. Four-session runs with shorter
   prompts saw none.
2. **When eviction happened, it was a similar share:** 17% to 18% of
   recomputed tokens here, 25% in the earlier three-session run.
3. **Cross-session reuse grows with session count,** because each added session
   reuses the shared system and task prompt.
4. **The overlap predictor was exact on 259 of 265 requests.** All 6 misses were
   in the two runs with eviction.

## Predicting eviction from the capture

`reuse_curve.py` computes, from a capture alone, the predicted reuse at every
cache size under sequential replay, and the smallest block count with no
eviction. For each matrix run:

| Run | Predicted threshold (blocks) | Above the 438 used? | Eviction observed |
| --- | --- | --- | --- |
| seed 1, 3 sessions | 466 | yes | yes |
| seed 2, 4 sessions | 511 | yes | yes |
| seed 2, 2 sessions | 385 | no | no |
| seed 3, 3 sessions | 367 | no | no |
| seed 2, 3 sessions | 332 | no | no |
| seed 1/3, 2 sessions; seed 1/3, 4 sessions | 300 or less | no | no |

The prediction separated all 9 runs correctly. "300 or less" means the
threshold is at or below the smallest size tested.

## Locating the threshold on the engine

For the earlier run `live-concurrent-20261107-01`, the curve predicts its
threshold at 488 blocks (15,616 tokens), with 487 blocks losing exactly 32
tokens. Run `cache-sweep-20261108-01` replayed its 30 prompts at both sizes, as
in the [cache sweep](cache-sweep-20261107.md):

| Blocks | Predicted eviction | Engine eviction | Requests exact | Prompt-ID parity |
| --- | --- | --- | --- | --- |
| 487 | 32 | 32 | 30 / 30 | 30 / 30 |
| 488 | 0 | 0 | 30 / 30 | 30 / 30 |

The predicted curve also reproduces the earlier engine values at 384 and 438
blocks exactly. Below the threshold, eviction grows roughly steadily as the
cache shrinks (24,544 tokens at 316 blocks). The shape is a knee, flat above
the threshold, rather than a step.

## Not done in this stage

- **vLLM's native simulator** needs `triton-cpu`, which the pinned requirements
  install only on x86_64 Linux, so it cannot run on this Mac. A Linux x86 CPU
  machine, such as a free Kaggle CPU notebook, could run it.
- **PrefixSim** needs a libCacheSim build, a new dependency.
- **Overlap replay** that reproduces recorded interleaving on the engine, to
  explain the 6 misses.
- **A control template** without the thinking cue, which needs a model download.

## Limits

- One task, one harness, one model family, one machine; CPU token counts only.
- The threshold test is exact for sequential replay. Live overlap adds a small
  error, seen only when the cache is short.

# Where coding agents lose prefix reuse

*An engine-validated measurement study with Cachino.* jerkeyray, November 2026.

**Contents:** [Abstract](#abstract) · [1 Introduction](#1-introduction) ·
[2 Method](#2-method) · [3 Results](#3-results) ·
[4 Explaining one cached-token count](#4-explaining-one-cached-token-count) ·
[5 Related work](#5-related-work) · [6 Limitations](#6-limitations) ·
[7 Future work](#7-future-work) · [8 Reproducing](#8-reproducing)

## Abstract

vLLM reports one number for prefix-cache reuse: how many prompt tokens were
cached. That number mixes two questions: what changed in the token IDs, and
what was still in memory. Cachino answers both. It captures agent traffic byte
for byte, rebuilds the exact prompt token IDs, and replays a pinned vLLM
build's prefix cache, free queue included, to predict each request's hit and
attribute every recomputed token to a cause. The predictions matched vLLM on
every sequential request, on a Mac CPU and a Tesla T4, and on 710 of 738
requests under live concurrency; a baseline that ignores residency had 24
times the error. On a frozen coding task, single sessions mostly recompute new
content. Qwen3's chat template and smolagents' tool-call serialization each
make replies unreusable, and sessions sharing a cache evict each other past a
threshold the capture predicts to the block. These mechanisms were known; the
contribution is exact, per-request attribution checked against the engine.

## 1. Introduction

Say a 6,000-token agent prompt comes back with 4,000 tokens cached. The other
2,000 could be new context, a prompt that rendered differently, a match that
ended partway through a block, or KV that was evicted before the turn arrived.
The first three are properties of the token sequence; eviction is a property of
the cache's history, and the prompt can match to the last ID and still miss.

vLLM stores KV in 32-token blocks, each identified by a hash chained over every
token before it, so a block is reusable only when the whole prefix up to its
end is identical. A request reuses the longest run of cached full blocks from
the start of its prompt, never the final prompt token. Freed blocks keep their
hashes until another request takes them.

![Illustrative prompt blocks: 8 reused after an append, 5 after a token change, 3 after eviction](figures/reuse-loss.svg)

*Figure 1. The causes this study separates. A schematic; block counts are illustrative.*

This study contributes a capture-to-token pipeline that reproduces the
engine's prompt IDs exactly (2, 3.4); a predictor of vLLM's prefix-cache hits
built from the pinned source (3.1); per-request attribution of recomputation
and a template check that works from a capture alone (3.2 to 3.4); and
measurements of the eviction threshold, its cost and two ways to schedule
around it (3.5 to 3.8).

## 2. Method

![Live traffic becomes a trace; offline analysis renders, predicts, attributes and verifies reuse](figures/pipeline.svg)

*Figure 2. The trace connects live traffic to offline analysis.*

1. **Capture.** The Go proxy forwards requests unchanged and stores request and
   response bytes with lifecycle events ([trace format](records/trace-format.md)).
2. **Render.** `analysis/reconstruct.py` turns each request into the exact token
   IDs the model sees, from a pinned tokenizer and chat template.
3. **Predict.** `analysis/predict.py` replays vLLM's free-queue rules at
   `2173d712` on those IDs: new blocks come from the queue head, and taking a
   block that still holds a hash evicts it; a hit is the longest chain of cached
   full blocks, capped at prompt length minus 1; finished requests return their
   blocks last first, hashed blocks to the tail. Source lines are in the
   [eviction plan](records/e4-eviction-plan.md).
4. **Attribute.** Each request's recomputed tokens are split into new content,
   a re-prefilled previous reply, block rounding (including the final prompt
   token), eviction (reuse lost below the session's own prompt bound) and
   cross-session reuse (above it).
5. **Check.** Replays send the same token IDs to the engine after a reset and
   compare its prompt IDs and cached tokens with the prediction.

A missing cache metric is unknown, never zero, and equal token counts are
never taken as equal tokens.

| Setup | |
| --- | --- |
| Engine | vLLM `2173d712`, built from source for the Mac CPU (Apple M5); the matching wheel on a Tesla T4 |
| Cache | Prefix caching, 32-token blocks, SHA-256 hashes, capacity set in whole blocks |
| Models | Qwen3-0.6B and 1.7B (byte-identical tokenizers, same KV size per token); Qwen2.5-0.5B-Instruct as a control; FP16; thinking off |
| Agent | mini-swe-agent `04d809ce` through a minimal HTTP adapter; smolagents 1.26.0 in 3.4 |
| Task | Fix `split_after` in more-itertools (issue 658), frozen at its baseline |
| Environment | A read-only emulator of the baseline tree; real commands under macOS `sandbox-exec` in 3.9 |

**Reading the numbers.** Every result is a dated measurement from 7 and 8
November 2026. *Recomputed* means prompt tokens minus vLLM's
`cached_tokens`. *Time to first token* is vLLM's own per-request metric, not a
proxy timestamp. Four protocols appear: *sequential replay* (captured prompts
sent one at a time after a reset), *timed overlap replay* (at their recorded
offsets), *reordered replay* (one trace in a different order) and *live runs*
(agents sampling through the proxy, where any change alters the trajectory, so
paired runs compare eviction rather than totals). Each result's run, cache size
and engine are in its [record](records/); modeling choices and how each was
checked are in [assumptions](records/assumptions.md).

## 3. Results

### 3.1 The predictions are exact

| Test | Requests exact |
| --- | --- |
| Calibration and capped-cache probes | 17 / 17, 27 / 27 |
| Sequential agent sessions, CPU | every turn |
| One trace at 7 cache sizes (CPU) and 4 (T4) | 30 / 30 at each |
| Live concurrent, cap and budget runs | 710 / 738 |

Engine prompt IDs equalled the local render on every replayed request. Under
overlap, most misses were one to a few blocks, and up to 26 in the two most
evicting runs; decode-block timing is the likely cause, a hypothesis that needs engine-side allocation events to confirm
([exactness](records/exactness-20261108.md)).

Residency is what makes the predictions exact. A predictor that assumes the
longest prefix shared with any earlier prompt is still cached scores nearly as
many exact requests, but its every miss is evicted reuse
([baseline](records/baseline-20261108.md)):

| Predictor, 9 concurrent runs (265 requests) | Exact | Total absolute error |
| --- | --- | --- |
| Longest shared prefix, assumed cached | 256 | 8,320 tokens |
| Free queue, overlapping requests | 259 | 352 tokens |

### 3.2 Single sessions: mostly new content

![What a single session recomputes](figures/recompute-split.svg)

*Figure 3. Recomputed tokens by cause in three single sessions with no eviction.*

New content was 68%, 64% and 91% of recomputation; the rest was block rounding
and the re-prefilled previous reply. The 0.6B model repeated `ls -la`, so its
fixed costs were a larger share
([30 turns](records/live-readonly-results-20261107.md),
[1.7B](records/live-1.7b-results-20261107.md)).

### 3.3 The template boundary

With thinking off, Qwen3's template ends the generation prompt with an empty
4-token `<think></think>` cue but leaves it out of history, so each turn
diverges just before the reply it generated, and the reply is prefilled again.
Only the newest reply is affected, so the cost stays small: an average of 16,
19 and 77 tokens per follow-up turn in the three sessions of 3.2.

- Keeping the cue in history let the engine reuse each reply: recomputation on
  turns 1 to 7 fell from 670 to 362 tokens, but the model's next action changed
  ([counterfactual](records/cue-counterfactual-20261107.md)).
- Qwen2.5's template renders history like the generation prompt; the engine
  reused a 442-token reply ([control](records/control-template-20261108.md)).

`analysis/template_check.py` finds this boundary from a capture alone. On every
Qwen3 capture, each turn that kept its reply diverged inside the cue; on the
Qwen2.5 capture none did.

### 3.4 A second harness

smolagents 1.26.0 writes each tool call back into history as Python-style text
with the server's random call ID: 52 to 66 tokens for a call generated in 19 to
36. Its replies cannot be reused under any template, and identical calls never
produce identical history. The renderer reproduced its tool-calling prompts
exactly, and the predictor was exact on all 23 requests
([smolagents](records/smolagents-20261108.md)).

### 3.5 Shared caches and the eviction threshold

Concurrent sessions on one 438-block cache evicted each other in 2 of 9 seeded
runs, losing 17% to 18% of recomputation (25% in an earlier run). Session
count did not predict which runs evicted; the threshold computed from each
capture did, for all 9
([repetitions](records/repetitions-20261108.md)).

![For this trace, 487 cache blocks lost 32 tokens to eviction; 488 blocks lost none](figures/eviction-curve.svg)

*Figure 4. Predicted eviction for one trace at every cache size, with engine
measurements on the CPU and the T4.*

Replaying one three-session trace, nothing is lost from 488 blocks up; at 487,
exactly one block. Engine points on both machines sit on the predicted curve
([result card](result-card-487-488.md), [sweep](records/cache-sweep-20261107.md)).

### 3.6 What eviction costs in time

![What eviction costs on a Tesla T4](figures/gpu-ttft.svg)

*Figure 5. Total time to first token for the same 30 requests on a Tesla T4.*

Total time to first token rose from 23.6 s without eviction to 30.9 s at 438
blocks (+31%) and 43.9 s at 384 (+86%): two rounds per size, capture off,
requests sent one at a time. A fit gives about 1.34 ms per recomputed token;
the totals are the measured quantities (`experiments/gpu-confirmation/`).

### 3.7 Scheduling to avoid eviction

![Scheduling sessions to avoid eviction](figures/scheduling.svg)

*Figure 6. Recomputation against delay for five orders of one evicting trace.*

Replaying an evicting trace with at most two sessions at a time, or in a greedy
order, removed eviction and cut recomputation 16.5%, for a mean delay of 4.3
to 4.6 positions. Every order matched its prediction on all 39 requests
([scheduling](records/scheduling-replay-20261108.md)).

### 3.8 A live concurrency limit

![Session limits pay off only when sessions outgrow the cache](figures/session-cap.svg)

*Figure 7. Wall clock per seed with no limit, a cap of two sessions and a token
budget, with tokens lost to eviction.*

A cap of two active sessions removed eviction in all three seeds that evicted.
In seed 4, wall clock fell from 1,243 s to 299 s; where nothing would have been
evicted, the cap only added waiting (up to +26%). A token budget avoided those
needless waits, but it paused sessions mid-task and their prefixes were evicted
while they waited (3,456 and 3,616 tokens in seeds 4 and 5). A started session
is cheap to keep and costly to pause, so a limit belongs at admission. Timing is
on the Mac CPU and compares conditions only
([session limits](records/session-limits-20261108.md)).

### 3.9 Keeping the cue in a sandboxed task

With commands really executed and the engine serving a cue-kept template, no
reply was re-prefilled in any of three seeds, against 919 to 1,967 tokens per
session with the default. No session solved the task under either template, so
task success could not separate them
([sandbox](records/sandbox-cue-20261108.md)).

## 4. Explaining one cached-token count

| Step | Question | Evidence |
| --- | --- | --- |
| 1 | Do the engine's prompt IDs equal the local render? | `return_token_ids` on replay |
| 2 | Where does the prompt first differ from every earlier prompt and reply? | Exact token IDs (3.2 to 3.4) |
| 3 | How many full blocks does that leave? | Block size and final prompt position |
| 4 | Were those blocks still resident? | A model of the free queue over everything served in between (3.1, 3.5) |

Steps 1 to 3 need only the capture and the pinned tokenizer and template. Step
4 also needs the order of every request the engine served. What carries over
beyond this setup is that reuse ends at the first changed token; block size,
eviction order and the exposed metrics belong to the engine and the commit.

## 5. Related work

| Result | Closest work | Overlap |
| --- | --- | --- |
| Agent traffic and harness comparison | [TraceLab](https://arxiv.org/html/2606.30560v2), [The Cache Wars](https://agnt.gg/whitepapers/the-cache-wars-prompt-cache-efficiency-llm-agent-harnesses), [Agentic Coding in the Wild](https://arxiv.org/html/2608.00101v1) | Covered, through provider accounting or external logs |
| Context policy, reuse and quality | [The Physical Cost of Context](https://arxiv.org/html/2610.34763v1) | Covered |
| Qwen3 template boundary | [Qwen3 issue #1826](https://github.com/QwenLM/Qwen3/issues/1826) | Known since March 2026 |
| Eviction under agent load | [CacheWise](https://arxiv.org/abs/2606.16824), [Continuum](https://arxiv.org/abs/2511.02230), [Penelope](https://arxiv.org/abs/2611.00391) | Known; these build policies on it |
| Predicting reuse from traces | [vLLM simulator PR](https://github.com/vllm-project/vllm/pull/47922) (open), [PrefixSim](https://github.com/cacheMon/PrefixSim) | Partly; no per-request agreement with an engine shown |

What remains is an independent model of the cache checked against the engine
request by request, attribution of every recomputed token from a raw capture,
and the residency baseline. The session limits are probes of a known mechanism,
not a new policy ([search records](records/related-work-check-20261107.md)).

## 6. Limitations

- One task, two harnesses and models of 0.6B to 1.7B: the shares describe this
  setup, not production traffic. No session solved the task, so no result
  measures an effect on task success.
- GPU timing comes from one T4 in sequential replay; Mac CPU timings compare
  conditions only. The scheduling replay counts delay in positions.
- Exactness is for one vLLM commit. The predictor was not compared with vLLM's
  native simulator, which needs `triton-cpu` on x86_64 Linux.

## 7. Future work

Record vLLM's `BlockStored` and `BlockRemoved` events during an overlapping
replay to test the decode-timing hypothesis; use models that can solve the task,
so a template change can be judged by success; build an admission limit that
predicts session growth; and compare with vLLM's native simulator once merged.

## 8. Reproducing

Commands are in [reproduce](reproduce.md). Each [record](records/) names its run
directory, spec hash and code commit; raw captures stay private. The released
per-request tables in [`results/`](../results/README.md) let
`python3 analysis/validate_results.py` recompute the recalculable claims, and
[`results/claims.json`](../results/claims.json) lists each claim's runs and
scope. `experiments/figures/make_figures.py` regenerates the data figures from
the captures, writing every plotted number to `figures/data.json`.
[examples/](../examples/README.md) reproduces small versions of 3.3 and 3.5 on
a fresh checkout.

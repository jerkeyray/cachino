# Where coding agents lose prefix reuse

*An engine-validated measurement study with Cachino.* jerkeyray, November 2026.

**Contents:** [Abstract](#abstract) · [1 Introduction](#1-introduction) ·
[2 Method](#2-method) · [3 Results](#3-results) · [4 Related work](#4-related-work) ·
[5 Limitations](#5-limitations) · [6 Future work](#6-future-work) ·
[7 Reproducing](#7-reproducing)

## Abstract

Coding agents resend their whole conversation on every turn, and a serving
engine skips the repeated part only if it matches an earlier prompt token for
token and is still cached. Servers report how many prompt tokens were cached,
but not why the rest were recomputed. We built Cachino, which captures agent
traffic byte for byte, rebuilds the exact token IDs the model sees, and replays
a pinned vLLM build's prefix-cache rules to predict each request's hit and
attribute every recomputed token to its cause. The predictions matched vLLM's
reported counts on every sequential request, on a Mac CPU and a Tesla T4, and on
710 of 738 requests under live concurrency; a baseline that ignores residency
had 24 times the error. Driving Qwen3 with mini-swe-agent and smolagents on a
frozen coding task, we find that single sessions mostly recompute new content;
that Qwen3's chat template makes every turn re-prefill the reply just
generated, and that fixing it restores reuse but changes the model's next
action; that sessions sharing a cache evict each other past a threshold
predictable from the capture; that on a T4 eviction raised time to first token
by 31% to 86%; and that capping concurrent sessions removes eviction, which
pays off only when the sessions' working set exceeds the cache. Both mechanisms
were known; the contribution is exact, engine-validated, per-request attribution.

## 1. Introduction

When an agent turn's prefill is slow, how much of the redone work came from the
prompt changing, from the cache's block granularity, or from the cache evicting
what the agent needed? A server reports only one number per request, the cached
tokens. We split that number into its causes, and check every prediction
against the engine.

**Background.** vLLM's prefix cache stores KV in fixed-size blocks (32 tokens
here), each identified by a hash chained over every token before it, so a block
is reusable only when the whole prefix up to its end is identical. A request
reuses the longest run of cached full blocks from the start of its prompt, never
the final prompt token. Freed blocks keep their hashes until another request
takes them, so whether a matching prefix is still there depends on everything
else the engine has served since.

![Three ways an agent turn loses prefix reuse](figures/reuse-loss.svg)

*Figure 1. The causes this study separates. A schematic, not data: block counts
are illustrative.*

**Contributions.**

1. A capture-to-token pipeline that reproduces the engine's prompt token IDs
   exactly from raw agent traffic, including tool definitions and tool-call
   history (2, 3.4).
2. A predictor of vLLM's prefix-cache hits, built rule by rule from the pinned
   source, exact on every sequential request on CPU and GPU and on 710 of 738
   concurrent ones (3.1).
3. A per-request attribution of recomputation into new content, a re-prefilled
   reply, block rounding, eviction and cross-session reuse, and a template check
   that finds template boundaries from a capture alone (3.2 to 3.4).
4. Measurements of the eviction threshold, its cost in time to first token, and
   two ways of scheduling around it, offline and live (3.5 to 3.8).

## 2. Method

![How Cachino measures prefix reuse](figures/pipeline.svg)

*Figure 2. Online, the proxy forwards and records traffic; offline, each request is rendered, predicted, attributed and checked against the engine.*

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
| Environment | A read-only emulator answers `ls`, `cat`, `grep` and similar from the repository's baseline tree and refuses edits; in 3.9 commands ran for real under macOS `sandbox-exec` |

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
| smolagents sessions, 4 runs | 23 / 23 |
| Sandboxed sessions, both templates, with reply IDs (3.9) | 120 / 120 |
| Concurrent sessions, 9 seeded runs | 259 / 265 |
| Session-cap runs, seeds 4 and 5 (3.8) | 147 / 158 |
| Token-budget runs, both versions, 8 runs (3.8) | 304 / 315 |
| Timed overlap replay, 3 evicting runs | 93 / 98 |

Engine prompt IDs equalled the local render on every replayed request. Under
overlap, the misses were off by one to a few blocks, except in the two most
evicting cap runs, where they reached 26 blocks. Replaying three
evicting runs with their recorded timing, a predictor that takes decode blocks
at admission was exact on 93 of 98 requests, one that takes them at completion
on 92, and one that ignores them on 88. The engine allocates as it decodes,
between the first two, so decode-block timing is the likely source of the
remaining error; confirming it would need engine-side allocation events
([repetitions](records/repetitions-20261108.md)).

A naive predictor that takes the longest prefix shared with any earlier
prompt and assumes it is still cached was exact on 256 of the 265 concurrent
requests, against 259 for the overlap predictor; total absolute error fell
from 8,320 tokens to 352. Every miss was in the two evicting runs, where the
naive error equals the reuse lost to eviction. On the ten session-cap runs the
error fell from 74,752 to 5,184 tokens. Without eviction the two agree; the
cache model matters where residency does ([baseline](records/baseline-20261108.md)).

| Predictor, 9 concurrent runs (265 requests) | Exact | Total absolute error |
| --- | --- | --- |
| Longest prefix with any earlier prompt, assumed cached | 256 | 8,320 tokens |
| Same, own session only | 226 | 24,064 tokens |
| Free queue, requests in sequence | 259 | 1,504 tokens |
| Free queue, overlapping requests | 259 | 352 tokens |

### 3.2 Single sessions: mostly new content

![What a single session recomputes](figures/recompute-split.svg)

*Figure 3. Recomputed tokens by cause in three single sessions, from turn 1 on.*

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

`analysis/template_check.py` detects this from a capture by rendering each
prompt with and without its generation prompt. On every Qwen3 capture, each
turn that kept its reply diverged inside the 4-token cue (28, 9 and 34
transitions in three captures); on the Qwen2.5 capture, none did, and the only
divergences were dropped replies.

### 3.4 A second harness

smolagents 1.26.0 (`ToolCallingAgent`) ran the same task with Qwen3-1.7B and
read-only tools (list a directory, read lines, search) backed by the same
snapshot, through Cachino, with vLLM parsing Qwen3's tool calls (run
`smol-cpu-20261108-01`, `experiments/agent-task/smol_cpu.py`). The agent made
five tool calls in six steps and ended with a final answer.

- The renderer reproduced the engine's prompt IDs exactly for all five
  tool-calling requests (tool definitions, `tool_choice: required`, tool-call
  history), and the predictor was exact on every request.
- Its fixed opening, a system prompt plus tool definitions, is about 1,700
  tokens, twice mini-swe-agent's.
- Every turn again diverged at Qwen3's empty thinking cue (4 of 4 transitions).
- smolagents does not replay the model's tool call. It writes each call into
  history as a Python-style text dump that includes the server's random call
  ID: 52 to 66 tokens for a call the model generated in 19 to 36. So even with
  a template that kept the cue, this harness's replies could not be reused, and
  identical calls never produce identical history.

Three more sessions sampled at temperature 0.7 under engine seeds 1 to 3 (run
`smol-matrix-20261108`) made 8, 5 and 5 requests. The predictor was exact on
all 18, and all 15 of their transitions dropped the cue. Recomputation
was 2,543 to 2,618 tokens per session, almost all new content (block rounding
43 to 97 tokens, no eviction), so a longer session added little: the long
fixed opening was reused and each turn's new text was small.

### 3.5 Shared caches and the eviction threshold

Concurrent sessions on one 14,016-token cache evicted each other in 2 of 9
seeded runs, losing 17% to 18% of recomputed tokens (25% in an earlier run).
Session count did not predict which runs evicted; the threshold computed from
each capture did, for all 9 runs ([concurrent](records/concurrent-results-20261107.md),
[repetitions](records/repetitions-20261108.md)).

![Eviction against cache size](figures/eviction-curve.svg)

*Figure 4. Predicted eviction for one captured trace at every cache size, with engine measurements on the CPU and the T4.*

One three-session trace replayed at each size: nothing is lost from 488 blocks
up, and eviction grows steadily below. Engine points on the CPU and the T4 sit
on the predicted curve, including 32 tokens lost at 487 blocks and none at 488
([sweep](records/cache-sweep-20261107.md)).

### 3.6 What eviction costs in time

![What eviction costs on a Tesla T4](figures/gpu-ttft.svg)

*Figure 5. Total time to first token for the same 30 requests at four cache sizes on a Tesla T4.*

On a Kaggle Tesla T4 (pinned wheel, torch 2.14.0 for CUDA 13.0, FP16, capture
off, two rounds per size), the same 30 requests took 23.6 s of total time to
first token without eviction, 30.9 s at 438 blocks (+31%) and 43.9 s at 384
blocks (+86%). A fit over 240 requests gives about 1.34 ms per recomputed token
plus 64 ms per request. Time to first token is vLLM's per-request metric from
scheduling; requests were sent one at a time.

### 3.7 Scheduling to avoid eviction

![Scheduling sessions to avoid eviction](figures/scheduling.svg)

*Figure 6. Recomputation against delay for five orders of one evicting trace, on the engine.*

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

### 3.8 A live concurrency limit

Cachino's proxy can cap active sessions (`-max-active-sessions`; a session stays
active between its turns for an idle period, and new sessions wait for a free
slot). Four concurrent Qwen3-1.7B sessions shared one proxy and one 438-block
cache, with the cap off and at 2, over five engine seeds (run
`limit-matrix-20261108`). For seeds 1 to 3, runs without the cap reproduced
the earlier seeded runs exactly.

| Seed | Cap | Requests | Lost to eviction | Engine time to first token | Wall clock | Waits |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | off | 40 | 0 | 77.1 s | 201.8 s | none |
| 1 | 2 | 30 | 0 | 55.5 s | 189.3 s | 101 s, 117 s |
| 2 | off | 39 | 4,640 | 337.8 s | 387.5 s | none |
| 2 | 2 | 40 | 0 | 212.2 s | 373.7 s | 197 s, 217 s |
| 3 | off | 40 | 0 | 139.2 s | 241.6 s | none |
| 3 | 2 | 37 | 0 | 191.0 s | 303.6 s | 120 s, 140 s |
| 4 | off | 38 | 66,528 | 1,137.7 s | 1,242.8 s | none |
| 4 | 2 | 40 | 0 | 146.3 s | 299.0 s | 182 s, 186 s |
| 5 | off | 40 | 256 | 202.6 s | 306.0 s | none |
| 5 | 2 | 40 | 0 | 93.8 s | 233.4 s | 72 s, 146 s |

![Session limits pay off only when sessions outgrow the cache](figures/session-cap.svg)

*Figure 7. Wall clock for the four sessions per seed with no limit, the two-session cap and the token budget described below; tokens lost to eviction at right.*

- The cap removed eviction in all three seeds whose sessions outgrew the cache.
- In seed 4 the uncapped sessions grew long: eviction caused 70% of all
  recomputation, reuse fell to 43%, and one session was rejected when its
  prompt passed the 12,288-token context. With the cap, wall clock fell from
  1,243 s to 299 s.
- Where nothing would have been evicted, the cap only made two sessions wait,
  and total wall clock rose by up to 26%.
- The predictor was exact on 370 of 384 requests, all misses in evicting runs.

Changing the interleaving changes which sampled replies are generated, so
trajectories, request counts and recomputed totals differ between paired runs;
eviction is the comparable measure. A fixed cap pays off only when the combined
working set exceeds the cache, which the capture-based threshold (3.5)
predicts. Timing is on the Mac CPU and compares conditions only.

A size-aware limit was tried next (`-session-token-budget`, set to the 14,016
tokens of the cache). The proxy estimates each session's footprint from its
latest request's size (0.25 tokens per byte, measured on these captures). A
turn proceeds if it fits after the sessions admitted before it; otherwise the
session is suspended between turns. The first version let an older turn start
beside younger requests already in flight, and on seed 2 it evicted as much as
no limit (4,576 tokens): the oldest session's prompt jumped to 10,492 tokens
while three younger requests were running. The second version makes such a
turn wait for those requests to finish; the table shows that version.

| Seed | Off: lost | Off: wall clock | Cap 2: wait | Cap 2: wall clock | Budget: lost | Budget: wait | Budget: wall clock |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | 201.8 s | 218 s | 189.3 s | 0 | none | 197.7 s |
| 2 | 4,640 | 387.5 s | 414 s | 373.7 s | 0 | 125 s | 339.5 s |
| 3 | 0 | 241.6 s | 259 s | 303.6 s | 0 | none | 228.2 s |
| 4 | 66,528 | 1,242.8 s | 368 s | 299.0 s | 3,456 | 258 s | 579.6 s |
| 5 | 256 | 306.0 s | 217 s | 233.4 s | 3,616 | 136 s | 376.8 s |

- Where nothing would have been evicted (seeds 1 and 3) the budget never made
  a session wait, unlike the fixed cap.
- On seed 2 it removed eviction with a third of the cap's waiting.
- On seeds 4 and 5 it lost 3,456 and 3,616 tokens, all from sessions it had
  suspended: a suspended session's prefix is free to evict while it waits. On
  seed 5 that was more than no limit lost.
- The fixed cap holds sessions before they start, so it never pays that cost.
  A started session is cheap to keep and costly to pause; a budget limit would
  need to predict growth and act at admission, which request size alone cannot.

The predictor was exact on 304 of 315 requests in these budget runs.

### 3.9 Keeping the cue in a sandboxed task

The counterfactual in 3.3 changed the template after the fact. Here the engine
served a cue-kept copy of the pinned Qwen3 template (`--chat-template`, made by
`experiments/agent-task/make_keep_cue_template.py`) and was compared with the
default template, over three engine seeds each (run `exec-matrix-20261108`).
mini-swe-agent drove Qwen3-1.7B at temperature 0.7 for up to 20 steps, and
its commands really ran: under macOS `sandbox-exec`, in a throwaway copy of the
task with no network and no writes outside the copy. The frozen evaluator then
scored the copy.

| Template | Seed | Requests | Prompt tokens | Reused | Reply re-prefilled | New content |
| --- | --- | --- | --- | --- | --- | --- |
| Default | 1 | 20 | 60,715 | 91.2% | 1,532 | 3,556 |
| Default | 2 | 20 | 71,946 | 90.8% | 919 | 5,381 |
| Default | 3 | 20 | 109,014 | 92.3% | 1,967 | 6,068 |
| Cue kept | 1 | 20 | 82,227 | 91.5% | 0 | 6,820 |
| Cue kept | 2 | 20 | 116,375 | 93.8% | 0 | 6,906 |
| Cue kept | 3 | 20 | 39,413 | 94.9% | 0 | 1,718 |

- With the cue kept, no reply was ever re-prefilled. Each next prompt continued
  the previous generation token for token, and vLLM served the reply from the
  KV it wrote while decoding.
- That reuse is invisible to the default predictor, which gives generated tokens
  unique IDs: it was exact on only 4 of the 60 cue-kept requests, always below
  the engine. With `--reply-ids`, which models each reply as its re-tokenized
  text, it was exact on all 120 requests in both arms.
- No session solved the task, or edited any file. All six used their 20 steps
  reading the repository, and each copy scored 15 of 21, as the unedited
  baseline does. Task success therefore could not separate the templates.

Sampled trajectories differ between arms, so totals differ; the re-prefilled
reply is the comparable measure. In the sandbox, `ls -la` at the task root
exits with status 1, because the profile denies reading the parent directory;
its listing is otherwise complete.

### 3.10 Proxy overhead

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

- One task, two harnesses (the second for one session) and small models (0.6B
  to 1.7B). The shares describe this setup, not production traffic.
- Commands ran only in the six sandboxed sessions (3.9); elsewhere observations
  came from a read-only snapshot. The 1.7B model never solved the task, so
  task success did not separate any conditions.
- Timing comes from one sequential replay on one GPU.
- The scheduling replay counts delay in positions; the live cap test measures
  wall-clock time, but only on the Mac CPU, over five seeds, with one task.
- vLLM's native simulator could not run on the Mac (it needs `triton-cpu` on
  x86_64 Linux), so the predictor was not compared with it.

## 6. Future work

Models large enough to solve the task, so that a template change can be
judged by success as well as reuse; an admission limit that predicts each
session's growth, so it holds sessions before they start instead of pausing
them; larger models on faster GPUs;
and a comparison with vLLM's native simulator once it is merged.

## 7. Reproducing

Commands are in [reproduce](reproduce.md). Each [record](records/) names its run
directory, spec hash and code commit; raw captures stay private, and
`experiments/figures/make_figures.py` regenerates the data figures from them, writing every
plotted number to `figures/data.json`; `experiments/figures/make_diagrams.py` draws
Figures 1 and 2. [examples/](../examples/README.md) reproduces small versions of 3.3 and
3.5 on a fresh checkout.

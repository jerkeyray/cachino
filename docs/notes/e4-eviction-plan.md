# E4 plan: eviction under a capped KV cache

Status: written 2026-11-07 and run the same day; every prediction matched. See the
[E4 results](e4-cpu-results-20261107.md). It follows the
completed [M4 CPU calibration](m4-cpu-results-20261107.md) on the same engine,
model and machine.

## Question

When an identical prompt comes back, does a cache miss come from eviction, and
can we predict exactly how much was evicted? M4 showed the prompt-change side:
reuse equals the shared prefix rounded down to 32-token blocks. E4 holds the
prompt fixed and varies only the traffic in between. Any shortfall from the M4
rule is therefore eviction, and its size should follow the pinned free-queue
policy.

## How the pinned engine evicts (vLLM `2173d712`, read 2026-11-07)

From `vllm/v1/core/block_pool.py` and `single_type_kv_cache_manager.py` in the
local checkout:

1. **Pool.** `BlockPool` creates `num_gpu_blocks` blocks and removes block 0 as
   a null placeholder (`block_pool.py` L189-192), so `N - 1` blocks are usable.
2. **Allocation.** New blocks are popped from the head of one free queue. A
   popped block that still holds a prefix hash has that hash removed: popping a
   cached block evicts it (`get_new_blocks`, L708-731).
3. **Free order.** A finished request's blocks are freed tail first
   (`reversed(...)`, `single_type_kv_cache_manager.py` L599).
4. **Free placement.** Blocks without a hash, such as a partial last block, go to
   the head of the queue for immediate reuse. Hashed blocks go to the tail, in the
   order freed (`free_blocks`, L818-849).
5. **Hits.** A lookup takes the longest chain of cached block hashes from the
   start, capped at prompt length minus 1 (`kv_cache_manager.py` L297-305).
   Block hashes are chained, so a missing block ends the hit.

Together these give an exact prediction. After request A finishes, its hashed
blocks sit at the tail with A's last block nearest the head. Later traffic
first uses never-cached blocks and partial blocks, then evicts A from its end
backwards. The surviving cache is always a prefix of A.

This is a model of the code as read, not a measured fact. E4 tests it.

## Setup

Same build, model and flags as M4, except for capacity:

- `--max-model-len 2048`, so the cap can be small. The cap must still hold one
  full-length request; the server will refuse to start otherwise.
- `--kv-cache-memory-bytes 352321536`: 96 blocks x 32 tokens x 114,688 bytes.
  The startup log should report 3,072 tokens. 95 blocks are usable.

The manifest records the logged token count. If the server rounds the block
count differently, the predictions are recomputed from the logged value before
the run.

## Prompts

- **A:** the M4 `base` probe, 1,091 tokens: 34 full blocks plus a 3-token partial.
- **A-late:** the M4 `late-mutation` probe; its exact-ID LCP with A is 1,080
  tokens, so at most 33 blocks can be reused.
- **Flood prompts F1 to F4:** about 1,000 tokens each. Each starts with its own
  label right after the role marker, so no flood shares a 32-token block with A
  or with another flood. Lengths are chosen with `n mod 32` not 0, so the
  answer of whether the one generated token takes a slot cannot change the block
  count.

The builder computes exact token counts, so the predictions use real counts.

## Conditions

Each condition starts with a reset and runs strictly in sequence.

| Condition | Requests | Predicted reuse on the last request (approximate, about 1,000-token floods) |
| --- | --- | --- |
| C0 control | A, A | 1,088 (34 blocks) |
| C1 | A, F1, A | 1,088: F1 fits in the 60 never-used blocks plus A's partial block |
| C2 | A, F1, F2, A | 1,024: F2 evicts A's last 2 hashed blocks |
| C3 | A, F1, F2, F3, A | 32: only A's first block survives |
| C4 | A, F1 to F4, A | 0: A fully evicted |
| M1 | A, F1, A-late | 1,056: limited only by the prompt change |
| M2 | A, F1, F2, A-late | 1,024: limited by eviction, 32 tokens below the prompt-change bound |

C3 is the sharpest test. Tail-first eviction predicts 32 tokens. An engine that
evicted from the front of a prompt would report 0 there, and 0 in C2 as well.

M1 and M2 split one miss into its two causes. In M2 the prompt change alone
would allow 1,056 tokens; the extra 32-token shortfall is eviction. This is the
attribution Cachino is meant to produce from real traffic.

## Pieces to build

1. **Spec and builder.** `experiments/cache-eviction/e4.json` defines the flood
   text and the conditions. A builder reuses the M4 renderer and writes bodies
   and local IDs, as `build_probes.py` does.
2. **Predictor.** `predict.py` replays the five rules above on block labels: a
   queue of `N - 1` blocks, head allocation, tail-first free, unhashed blocks to
   the head, hashed blocks to the tail. It outputs the expected `cached_tokens`
   and `created_cache_tokens` for every request. Unit tests cover each rule.
3. **Runner.** Extend `calibrate.py` with a `cached_equals_predicted` check that
   reads the predictor's output, or add a thin E4 runner that reuses its
   client, record format and stop rules. Missing cache details still stop the
   run.
4. **Results note.** Predicted against observed per request, with the manifest.

## Interpretation rules

- An exact match on every request supports the free-queue model for sequential
  single-worker traffic on this build. It says nothing about concurrent requests,
  preemption or chunked prefill across steps.
- A mismatch is a finding. Record it, then check the pinned source again before
  changing the predictor. Do not tune the predictor to the observed numbers in
  the same run.
- `created_cache_tokens` gives a second, independent check of the model on every
  request.
- CPU timing is recorded, not interpreted as GPU latency.

## Budget

No downloads or installs: everything is already local. About 30 requests at
roughly 3 s per cold prefill on this Mac is about 2 minutes of engine time. No
GPU.

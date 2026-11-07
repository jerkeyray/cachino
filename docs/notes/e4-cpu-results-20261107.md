# E4 results: eviction under a capped KV cache, 2026-11-07

Run `e4-cpu-20261107-01`, following the [E4 plan](e4-eviction-plan.md). One
sequential pass of 35 steps (7 resets, 27 requests, 1 preflight), no retries.
Raw responses and the manifest are private (`artifacts/e4-cpu-20261107-01/`);
this is the tracked summary.

## Setup

The same engine build, model, dtype, block size and flags as the
[M4 CPU run](m4-cpu-results-20261107.md), except:

- `--max-model-len 2048` and `--kv-cache-memory-bytes 352321536`. The log reported
  a 3,072-token CPU KV cache: 96 blocks of 32, with 95 usable after the null block.
- Spec `experiments/cache-eviction/e4.json` (SHA-256 `8cad1d36…47d7`), probes
  `e4-probes-20261107-01`, code at commit `3f0d1ee9`.
- Prompt A and A-late are byte-identical to the M4 `base` and `late-mutation`
  probes (1,091 tokens each, exact-ID LCP 1,080). The four flood prompts are
  1,001 tokens each and share no full block with A or with each other.
- Every prediction was written by `predict.py` at build time, before the server
  started. The runner refused geometry other than 96 x 32.

## Findings

All 27 requests matched their predictions exactly on both `cached_tokens` and
`created_cache_tokens`. Token parity held on every request, and all 7 resets
succeeded. The run completed with no failed checks.

| Condition | Traffic before the last request | Last request | Predicted reuse | Engine reuse | Engine created |
| --- | --- | --- | --- | --- | --- |
| C0 | none | A | 1,088 | 1,088 | 0 |
| C1 | 1 flood | A | 1,088 | 1,088 | 0 |
| C2 | 2 floods | A | 1,024 | 1,024 | 64 |
| C3 | 3 floods | A | 32 | 32 | 1,056 |
| C4 | 4 floods | A | 0 | 0 | 1,088 |
| M1 | 1 flood | A-late | 1,056 | 1,056 | 32 |
| M2 | 2 floods | A-late | 1,024 | 1,024 | 64 |

Every first request after a reset reported 0 reused and 1,088 created; every
flood reported 0 reused and 992 created (31 full blocks).

1. **Eviction is tail first.** C3 keeps exactly A's first 32-token block. If the
   engine evicted from the front of a prompt, C2 and C3 would both show 0.
2. **The free-queue model holds for this traffic.** Unused blocks and partial
   blocks are consumed before cached ones, and cached blocks are evicted in
   least-recently-freed order. C1 evicts nothing because one flood fits in the 60
   never-used blocks plus A's partial block.
3. **One miss splits into its two causes.** In M2 the prompt change alone allows
   1,056 tokens (M1); eviction removes one more block, giving 1,024. Without E4's
   controls the two causes are indistinguishable in a single `cached_tokens`
   value.
4. **`created_cache_tokens` is an independent check.** It equals full blocks
   minus reused blocks on every request, so the model is tested twice per request.

## Limits

- Sequential, single-worker traffic with one request in flight. The model was not
  tested under concurrency, preemption, or prompts longer than one prefill chunk
  (2,048 batched tokens).
- Prompt lengths were chosen to avoid block multiples, so whether the generated
  token takes a slot was not exercised.
- Floods share no blocks by construction. Real agent traffic shares prefixes
  across requests, which changes which blocks are touched and when.
- CPU timings are recorded but are not GPU latency. C3's last request, with 32
  reused tokens, took about as long as a cold request (3.2 s).

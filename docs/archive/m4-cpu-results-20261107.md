# M4 calibration results: vLLM CPU backend, 2026-11-07

Run `m4-cpu-20261107-01`. One sequential pass of the 17-step
[probe sequence](../../experiments/cache-ground-truth/probes.json), with no
retries. Raw responses, the filled manifest and the Cachino capture are private
(`artifacts/m4-cpu-20261107-01/`, `traces/m4-cpu-20261107-01/`). This note is
the tracked summary.

## Setup

| Item | Value |
| --- | --- |
| Engine | vLLM `0.33.0rc1.dev595+g2173d7121.cpu`, source build of pin `2173d712` on macOS 26.1, Apple M5, 16 GiB |
| Model | `Qwen/Qwen3-0.6B` at `c1899de2`, FP16 (BF16 weights cast at load) |
| Tokenizer | Files byte-identical to the earlier Qwen3-8B pin; probe IDs identical to the 8B build |
| Cache | Prefix caching on, block 32, hash block 32 (source-derived), SHA-256 hashing, 1 GiB = 9,344 tokens |
| Scheduling | One worker, chunked prefill on, max 128 sequences, 2,048 batched tokens, no speculative decoding, no KV connector |
| Probe set | `m4-probes-20261107-01`, spec SHA-256 `64000c16…d35b`, Cachino commit `50f61c28` |

The CPU platform setup is in the [CPU build plan](m4-cpu-build-plan.md).

## Findings

The run completed with no failed checks or stops.

| Step | Prompt tokens | Local exact-ID LCP with base | Block prediction | Engine `cached_tokens` | `created_cache_tokens` |
| --- | --- | --- | --- | --- | --- |
| parity-short (after reset) | 13 | n/a | 0 | 0 | 0 |
| cold-base (after reset) | 1091 | n/a | 0 | 0 | 1088 |
| warm-base | 1091 | 1091 | 1088 | 1088 | 0 |
| early-after-warm | 1091 | 30 | 0 | 0 | 1088 |
| late-warmup (after reset) | 1091 | n/a | 0 | 0 | 1088 |
| late-after-warm | 1091 | 1080 | 1056 | 1056 | 32 |
| history-turn-1 (after reset) | 1091 | n/a | not checked | 0 | 1088 |
| history-turn-2 | 1124 | 1087 | 1056 | 1056 | 64 |
| direct-cold (after reset) | 1091 | n/a | 0 | 0 | 1088 |
| proxy-cold through Cachino (after reset) | 1091 | n/a | 0 | 0 | 1088 |

1. **Token parity.** Every returned `prompt_token_ids` equals the local IDs
   element by element, and `usage.prompt_tokens` equals the count. The Cachino
   route returned the same IDs as the direct route.
2. **Reset.** All six `POST /reset_prefix_cache` calls returned
   `{"success": true}`, and every following probe reported `cached_tokens` 0.
3. **Reuse rule.** Each warm hit equals
   `floor(min(LCP, prompt_length - 1) / 32) * 32`. The early mutation at token 30
   leaves no complete shared block, so nothing is reused. The late mutation and the
   history turn both keep 33 blocks.
4. **Created tokens.** `created_cache_tokens` equals the prompt's full blocks
   minus those served from cache: 1,088 when cold (34 of 34 blocks), 32 after the
   late mutation (34 full blocks minus 33 reused), and 64 for the 1,124-token
   turn 2 (35 full blocks minus 33 reused). A full warm hit creates nothing.
5. **Cachino transparency.** Cachino captured the proxy step with request and response
   integrity verified, and the captured request SHA-256 equals the probe body
   (`a02d3247…01c9e`). `X-Request-Id` came back as the response ID on every step.

## Limits

- One run, sequential, one worker, no competing traffic. It shows the
  accounting rule holds for these probes; it is not a residency or eviction model.
- The block prediction assumes the reference probe stayed fully cached. With
  9,344 tokens of capacity and at most two 1.1k-token prompts live, no eviction
  was expected. Eviction is tested separately (E4).
- `hash_block_size_tokens` is derived from the pinned source, not logged by the
  server. `num_gpu_blocks` is 9,344 / 32 = 292 CPU blocks.
- CPU timings are recorded but are not GPU latency. Cold prefill took about
  3.1 to 3.2 s and a full warm hit about 31 ms; they show the direction of the
  effect on this machine only.
- The proxy step is one request. It shows byte and ID transparency, not proxy
  overhead, which needs repeated pairs.
- The calibration client prefixes the Cachino session label with `m4-` on top of a
  run ID that already starts with `m4-`, so the captured session is
  `m4-m4-cpu-20261107-01`. This is cosmetic.

# Method

How Cachino turns a capture into per-request attribution, and how the engine
experiments were run.

## Pipeline

1. **Capture.** The Go proxy forwards agent requests unchanged and stores the
   exact request and response bytes with lifecycle events
   ([trace format](archive/trace-format.md)).
2. **Render.** `analysis/reconstruct.py` turns each captured request into the
   exact token IDs the model sees, using a pinned tokenizer snapshot and chat
   template (`experiments/tokenizer/*.json`).
3. **Predict.** `experiments/cache-eviction/predict.py` replays the pinned vLLM
   free-queue rules on those IDs (below) and predicts each request's
   `cached_tokens`.
4. **Attribute.** Each request's recomputed tokens are split into new content,
   the previous reply re-prefilled, block rounding, eviction (reuse below the
   session's own prompt bound) and cross-session reuse (reuse above it).
5. **Check.** Replays send the same token IDs to the engine with
   `return_token_ids`, after a cache reset, and compare engine IDs and counts.

Missing cache metrics are recorded as unknown, never zero. A matching token
count alone is never taken as matching tokens.

## vLLM cache rules modelled

Read from the pinned source (`vllm/v1/core/block_pool.py`,
`single_type_kv_cache_manager.py`, `kv_cache_manager.py`); line numbers are in
the [E4 plan](archive/e4-eviction-plan.md).

- Block 0 is a placeholder; the other blocks start in one free queue.
- New blocks come from the queue head; taking a block that still holds a prefix
  hash evicts it.
- A hit is the longest chain of cached full blocks from the start, capped at
  prompt length minus 1.
- A finished request frees its blocks last first. Blocks without a hash go to
  the queue head, hashed blocks to the tail.
- A reset clears every hash and keeps the queue order.

For overlapping requests the predictor holds each request's blocks until it
finishes and queues requests that do not fit. It takes all blocks at the proxy
start time, while the engine allocates as it decodes, which explains the small
errors under eviction.

## Engine setup

vLLM `2173d712` was built from source for the Mac CPU (Apple M5, 16 GiB) in an
ignored virtual environment, with uv's CPython 3.12.13 and torch 2.14.0
([build plan](archive/m4-cpu-build-plan.md)). Every run used loopback only,
`VLLM_SERVER_DEV_MODE=1` for the reset endpoint, and:

```sh
vllm serve <model> --revision <pinned> --dtype float16 --block-size 32 \
  --enable-prefix-caching --prefix-caching-hash-algo sha256 \
  --kv-cache-memory-bytes <bytes> --max-model-len <len> \
  --enable-prompt-tokens-details --enable-per-request-metrics
```

Qwen3 runs also pass `--default-chat-template-kwargs '{"enable_thinking": false}'`.
Cache capacity is set in whole blocks (`blocks x 32 x bytes per token`) and
read back from the startup log. vLLM seeds sampling with 0 by default, so
sampled sessions repeat exactly after a fresh start; independent runs use
`--seed`.

Models (all weights checked against Hugging Face SHA-256 values):

| Model | Revision | Bytes per token of KV | Use |
| --- | --- | --- | --- |
| Qwen3-0.6B | `c1899de2` | 114,688 | calibration, eviction, replays |
| Qwen3-1.7B | `70d244cc` | 114,688 | varied and concurrent sessions |
| Qwen2.5-0.5B-Instruct | `7ae55760` | 12,288 | control template |

Qwen3-0.6B and 1.7B have byte-identical tokenizer files, so their captures
replay on either.

## Agent sessions

`experiments/agent-task/live_cpu.py` runs the pinned mini-swe-agent loop
(`04d809ce`, text-based templates) on the frozen task through a Cachino proxy
it owns. The agent child has no credentials and can connect only to that
proxy. Commands are never executed: `readonly_env.py` answers read-only
commands from the task repository's baseline tree and refuses edits.
`live_concurrent.py` runs several sessions at once against one engine. Each
experiment folder's README has the commands.

## Reproducing

Raw captures, manifests and weights stay in ignored `artifacts/`, `traces/`
and `.project/`. Each archive record names its run directory, spec hash and
code commit. Specs and probe sets are hash-checked, so a run is reproduced by
checking out its recorded commit.

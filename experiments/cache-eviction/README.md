# E4 eviction under a capped KV cache

Holds a prompt fixed, sends unrelated filler prompts in between, and checks
that the engine's reuse matches a free-queue model of the pinned vLLM build.
Design: [E4 plan](../../docs/notes/e4-eviction-plan.md). Results:
[E4 CPU results](../../docs/notes/e4-cpu-results-20261107.md).

## Files

- `predict.py`: replays the pinned eviction rules on block labels. Given each
  prompt's token IDs (and optionally generated IDs), it predicts `cached_tokens`
  and `created_cache_tokens`. Tests: `analysis/test_eviction_predict.py`.
- `e4.json`: the seven conditions (C0 to C4, M1, M2) as a sequence for `calibrate.py`.
- `build_e4.py`: writes request bodies, local IDs and predictions for one stated
  cache geometry, before any request is sent.

## Reproduce

Build for the cache you will launch (96 blocks of 32 here):

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \
  --python 3.12 --no-python-downloads python -B experiments/cache-eviction/build_e4.py \
  --num-blocks 96 --block-size 32 --out artifacts/e4-probes-NEW
```

Launch vLLM as in the [CPU build plan](../../docs/notes/m4-cpu-build-plan.md), but with
`--max-model-len 2048 --kv-cache-memory-bytes 352321536`. The log should report
3,072 tokens. Record 96 as `num_gpu_blocks` in the manifest, then:

```sh
python3 experiments/cache-ground-truth/calibrate.py --spec experiments/cache-eviction/e4.json \
  --probes artifacts/e4-probes-NEW --manifest artifacts/RUN/manifest.json \
  --direct http://127.0.0.1:18000 --proxy http://127.0.0.1:18080 --out artifacts/RUN/out --run
```

The runner refuses a manifest whose cache geometry differs from the build's
prediction parameters. E4 has no proxy steps, so `--proxy` is not contacted.

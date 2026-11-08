# Reproducing the study

Commands run from the repository root. Large or private outputs go to ignored
`artifacts/`, `traces/` and `.project/`; every output directory must be new.

## 1. Environments

- Go 1.26 or newer, and [uv](https://docs.astral.sh/uv/).
- Analysis environment: `uv run --project analysis --offline --no-python-downloads python ...`
  (standard library only).
- Tokenizer environment, for rendering token IDs:

  ```sh
  T="uv run --project analysis/tokenizer --frozen --python 3.12 --no-python-downloads python -B"
  ```

  Commands below that render tokens use `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 $T`.
- vLLM at `2173d712`, built from source for the Mac CPU into `.project/venvs/vllm-cpu`
  ([build record](records/m4-cpu-build-plan.md)).
- Tokenizer snapshots for the pinned models (`experiments/tokenizer/*.json`):

  ```sh
  uv run --project analysis --offline --no-python-downloads python analysis/tokenizer_assets.py \
    --pin experiments/tokenizer/qwen3-0.6b.json --out artifacts/qwen3-0.6b-tokenizer
  ```

  Repeat for `qwen3-1.7b.json` and `qwen2.5-0.5b-instruct.json`.

## 2. Engine

Every engine run serves on loopback with the reset endpoint enabled:

```sh
VLLM_SERVER_DEV_MODE=1 vllm serve Qwen/Qwen3-0.6B --revision c1899de289a04d12100db370d81485cdf75e47ca \
  --tokenizer-revision c1899de289a04d12100db370d81485cdf75e47ca --host 127.0.0.1 --port 18000 \
  --dtype float16 --block-size 32 --enable-prefix-caching --prefix-caching-hash-algo sha256 \
  --kv-cache-memory-bytes <blocks x 32 x 114688> --max-model-len <len> \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-prompt-tokens-details --enable-per-request-metrics
```

Read the block count back from the `KV cache size` line of the log. Add
`--seed N` for independent sampled runs.

## 3. Experiments

| Step | Folder | Notes |
| --- | --- | --- |
| Calibration | [cache-ground-truth](../experiments/cache-ground-truth/README.md) | `build_probes.py`, then `calibrate.py --run` |
| Eviction probes | [cache-eviction](../experiments/cache-eviction/README.md) | `build_e4.py` for a stated block count, then `calibrate.py` |
| Proxy overhead | [proxy-overhead](../experiments/proxy-overhead/README.md) | No engine needed |
| Agent sessions | [agent-task](../experiments/agent-task/README.md) | `live_cpu.py` with a spec; `live_concurrent.py` for several |
| GPU confirmation | [gpu-confirmation](../experiments/gpu-confirmation/README.md) | Token-ID bundle and a Kaggle T4 notebook |

## 4. Analysis

All in `analysis/`, run with the tokenizer environment, directly or through
`cachino_analyze.py <command>`:

| Script | Produces |
| --- | --- |
| `live_attribution.py` | Per-turn split of one session, with optional prompt replay |
| `concurrent_attribution.py` | Cross-session attribution of a concurrent run |
| `matrix_summary.py` | Summary of a seed by session-count matrix |
| `reuse_curve.py` | Predicted reuse and eviction over cache sizes, and the threshold |
| `cache_sweep.py` | Engine replay of one run at one cache size |
| `overlap_replay.py` | Engine replay with recorded timing, against three overlap models |
| `schedule_replay.py` | Scheduling orders, predicted and on the engine |
| `cue_counterfactual.py` | Sessions replayed with and without the thinking cue |
| `template_check.py` | Each turn classified as append-only, generation prompt dropped, reply dropped or history changed |

For example, the threshold of the first concurrent run:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 $T analysis/reuse_curve.py \
  --run artifacts/live-concurrent-20261107-01 --pin experiments/tokenizer/qwen3-1.7b.json \
  --assets artifacts/qwen3-1.7b-tokenizer --min-blocks 300 --max-blocks 700
```

## 5. Figures

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 $T experiments/figures/make_figures.py
```

It reads the run directories named in the [records](records/) and rewrites
`docs/figures/`.

## 6. Tests

```sh
go build ./... && go vet ./... && go test -race ./...
uv run --project analysis --offline --no-python-downloads python -m unittest discover -s analysis
```

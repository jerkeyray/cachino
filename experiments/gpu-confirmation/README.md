# GPU confirmation

Checks that the CPU results hold on a GPU, and turns recomputed tokens into
time. One captured three-session run is
replayed at cache sizes below and above its eviction threshold (488 blocks),
with capture off.

## Files

- `make_bundle.py`: writes a token-ID bundle from a captured run: per request,
  session, turn, start offset, prompt token IDs and completion length, plus
  copies of `predict.py` and `gpu_replay.py` and a hash manifest. It holds no raw
  capture bytes, but the IDs decode back to the prompt text with the public
  Qwen tokenizer, so **uploading a bundle discloses that text**.
- `gpu_replay.py`: standard library plus `predict.py`. For each cache size it
  starts the pinned vLLM, predicts each request's cached tokens, replays the
  prompts after a reset (twice by default), and records cached tokens, parity,
  engine time to first token and client round trip. It ends with a fit of time
  to first token against recomputed tokens.

Build the bundle (tokenizer environment):

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \
  --python 3.12 --no-python-downloads python -B experiments/gpu-confirmation/make_bundle.py \
  --run artifacts/live-concurrent-20261107-01 --pin experiments/tokenizer/qwen3-1.7b.json \
  --assets artifacts/qwen3-1.7b-tokenizer --replay-model Qwen/Qwen3-0.6B \
  --replay-revision c1899de289a04d12100db370d81485cdf75e47ca --out artifacts/gpu-bundle-NEW
```

The replay was tested end to end on the Mac CPU build at 438 and 488 blocks: it
reproduced the earlier engine counts (88,832 and 93,344 cached tokens), with the
predictor exact and prompt-ID parity on all 30 requests at both sizes.

## Running on a free Kaggle GPU

1. Create a Kaggle account and verify a phone number, which Kaggle requires
   for GPU and internet access.
2. Upload the bundle directory as a **private** dataset.
3. Create a notebook. In its settings, choose a GPU accelerator (T4) and turn
   internet on, then add the dataset. It appears under `/kaggle/input/<name>`.
4. Run, one cell each:

```text
!nvidia-smi
```

```text
!pip install 'https://wheels.vllm.ai/2173d7121fc6e8d756c884f46691546c61b60c57/vllm-0.33.0rc1.dev595%2Bg2173d7121-cp38-abi3-manylinux_2_28_x86_64.whl'
```

```text
!python -c "import vllm, torch; print(vllm.__version__, torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_capability())"
```

Stop here if CUDA is unavailable or the vLLM version is not
`0.33.0rc1.dev595+g2173d7121`.

```text
!python /kaggle/input/<name>/gpu_replay.py --bundle /kaggle/input/<name> --sizes 384 438 488 640 --out /kaggle/working/gpu-result
```

5. Download `/kaggle/working/gpu-result/result.json` and the `vllm-*.log` files.

The T4 has no BF16, so the replay uses FP16, as on the CPU. The pinned wheel ran
on the T4 (compute capability 7.5) with torch 2.14.0 for CUDA 13.0 under driver
580.178.04. If the server fails to start on another card, keep its log. The model weights (Qwen3-0.6B, about 1.5 GB) download from Hugging Face
on first start.

Result of the first run, on a Kaggle Tesla T4: [report, section 3.5](../../docs/report.md#35-what-eviction-costs-in-time).

## What to look for

- `predictor_exact` and `parity` at each size: does exactness hold on GPU?
- `eviction_tokens` above and below 488 blocks: does the threshold hold?
- `ttft_fit.ms_per_recomputed_token` and the per-round totals: what the
  eviction costs in time on this card. The CPU test gave 4.0 ms per token;
  that figure describes the Mac, not a GPU.

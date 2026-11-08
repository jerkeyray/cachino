# M4 on CPU: vLLM CPU backend on Apple Silicon

Status: written and executed on 2026-11-07. The build, weights and smoke checks
succeeded; see the [M4 CPU results](m4-cpu-results-20261107.md). One change from
the plan: Homebrew's Python 3.12 failed uv's platform check, so the environment
uses uv's managed CPython 3.12.13. The section on package changes describes work
since done in the retarget commit.

## Why CPU

M4 checks cache accounting: whether the engine sees our exact token IDs, how
many tokens it reports as reused, and why the rest were not. Block hashing,
hit lookup and eviction run in vLLM's engine core, which is shared by the CPU
and GPU platforms. CPU timings say nothing about GPU latency, so latency claims
still need a GPU run later. This note applies the same measurement contract as
the [M4 calibration package](m4-calibration-plan.md); the
RunPod runbook stays as the GPU path.

## Source-checked facts at vLLM `2173d712`

Read-only, 2026-11-07, from
[`vllm/platforms/cpu.py`](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/vllm/platforms/cpu.py),
[`requirements/cpu.txt`](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/requirements/cpu.txt),
[`pyproject.toml`](https://github.com/vllm-project/vllm/blob/2173d7121fc6e8d756c884f46691546c61b60c57/pyproject.toml)
and the CPU install docs.

| Question | Finding | Consequence |
| --- | --- | --- |
| Mac support | Experimental; build from source only; no Apple Silicon wheels. The docs list FP32 and FP16. `VLLM_TARGET_DEVICE` is set to `cpu` automatically on macOS | Source build at the pin |
| Prefix caching on CPU | Forced off only for MLA models without the AMX path (`cpu.py` L530-547). Qwen3 is not MLA | Stays available for Qwen3 |
| Block size | Default 128 on CPU; multiples of 32 preferred (`cpu.py` L311-316, L561-563) | Set `--block-size 32` explicitly. Hits land on 32-token boundaries, and the GPU confirmation run uses the same value |
| KV capacity cap | `--kv-cache-memory-bytes` (bytes). `VLLM_CPU_KVCACHE_SPACE` (whole GiB) is labeled legacy (`cpu.py` L340-344) | Use the byte flag for eviction tests; GiB is too coarse |
| Python and torch | `requires-python >=3.11,<3.15`; `torch==2.14.0` on Darwin | Python 3.12 from Homebrew is present locally |

## Model: Qwen3-0.6B, with identical tokenizer files

`Qwen/Qwen3-0.6B` at revision `c1899de289a04d12100db370d81485cdf75e47ca`
(not gated; one `model.safetensors`, 1,503,300,328 bytes). Compared through the
Hugging Face tree API without downloading files:

| File | 0.6B at `c1899de2` vs 8B at `b968826d` |
| --- | --- |
| `tokenizer.json` | identical (LFS SHA-256 `aeb13307…dae4`, equal to the local pin) |
| `tokenizer_config.json` (holds the chat template) | identical (git blob `417d038a…`) |
| `vocab.json`, `merges.txt`, `generation_config.json` | identical (git blobs) |
| `config.json` | differs, as expected (model shape) |

Because the tokenizer and template files are byte-identical, the existing
probes and their local expected token IDs remain valid. Only the request's
`model` field and the run manifest's model entries change. Engine-returned IDs
still decide parity.

KV size per token: 28 layers x 8 KV heads x 128 x 2 (K and V) x 2 bytes =
114,688 bytes in FP16, about 9,360 tokens per GiB. The 1,124-token largest probe
needs about 123 MiB.

Dtype: FP16. It is on the documented macOS list, halves KV memory compared to
FP32, and matches the T4 confirmation run, which has no BF16.

## Local prerequisites (checked 2026-11-07)

macOS 26.1 on Apple M5 with 16 GB of RAM; Apple Clang 17 with Command Line
Tools; Homebrew `cmake`; `uv`; Homebrew Python 3.12; 139 GiB free disk.
`ninja` is absent and optional.

## Approval boundaries

1. **External checkout:** clone vLLM at the pin into `.project/checkouts/vllm-2173d712`
   (ignored, inside Cachino).
2. **Dependency install:** a new virtual environment at `.project/venvs/vllm-cpu`
   with the pinned CPU requirements (torch 2.14.0 and vLLM's common
   requirements; a few GB), then the vLLM source build.
3. **Weights download:** Qwen3-0.6B at the pinned revision, about 1.5 GB, into
   `.project/hf`.

Nothing outside the Cachino checkout is written; `.project/` is ignored.

## Commands

Step 1, checkout:

```bash
cd path/to/cachino
git clone --filter=blob:none https://github.com/vllm-project/vllm.git .project/checkouts/vllm-2173d712
git -C .project/checkouts/vllm-2173d712 checkout --detach 2173d7121fc6e8d756c884f46691546c61b60c57
git -C .project/checkouts/vllm-2173d712 rev-parse HEAD
```

Step 2, environment and build (expected 10 to 30 minutes):

```bash
uv venv --python /opt/homebrew/bin/python3.12 .project/venvs/vllm-cpu
. .project/venvs/vllm-cpu/bin/activate
cd .project/checkouts/vllm-2173d712
uv pip install -r requirements/cpu.txt
uv pip install -e .
python -c 'import vllm, torch; print(vllm.__version__, torch.__version__)'
uv pip freeze > ../../vllm-cpu-freeze.txt
```

If the build cannot find standard C++ headers, the upstream fix is
reinstalling the Command Line Tools. That changes system software, so it would
be a separate decision for you.

Step 3, weights:

```bash
export HF_HOME=$PWD/.project/hf
hf download Qwen/Qwen3-0.6B --revision c1899de289a04d12100db370d81485cdf75e47ca
```

Step 4, launch on loopback:

```bash
export HF_HOME=$PWD/.project/hf VLLM_SERVER_DEV_MODE=1
vllm serve Qwen/Qwen3-0.6B \
  --revision c1899de289a04d12100db370d81485cdf75e47ca \
  --tokenizer-revision c1899de289a04d12100db370d81485cdf75e47ca \
  --host 127.0.0.1 --port 18000 \
  --dtype float16 --max-model-len 4096 \
  --kv-cache-memory-bytes 1073741824 \
  --enable-prefix-caching --block-size 32 --prefix-caching-hash-algo sha256 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-prompt-tokens-details --enable-per-request-metrics \
  > .project/vllm-cpu.log 2>&1
```

1 GiB of KV is about 9,360 tokens: enough for every M4 probe at once. The
eviction tests later lower it.

## Smoke checks before calibration

Each must pass, or the backend is unsuitable as it stands:

1. `GET /v1/models` lists `Qwen/Qwen3-0.6B`.
2. The startup log reports prefix caching on, block size 32 and the KV capacity.
3. A short chat request with `return_token_ids: true` returns `prompt_token_ids`
   equal to the local IDs for `parity-short`.
4. `usage.prompt_tokens_details.cached_tokens` is present: 0 after
   `POST /reset_prefix_cache` returns `{"success": true}`, and positive on an
   identical repeat of a prompt longer than one block.
5. The response carries per-request metrics.

If prefix caching or cache details do not work on macOS, the fallback is the
same build on Linux, such as a free Kaggle CPU notebook, which uses no GPU quota.

## Package changes needed before calibrate.py can run against 0.6B

These are local code changes, not yet made:

- `probes.json` names `Qwen/Qwen3-8B`. A 0.6B variant changes only the `model`
  field and is rebuilt with `build_probes.py`. The expected IDs should come out
  identical.
- The run manifest and `check_manifest.py` assume the 8B tokenizer revision. They
  need to record the served model's revision and the tokenizer-file equivalence
  above, rather than relabel the model.
- The plan's expectations table gains block-aligned predictions once block
  size 32 is confirmed from the log.

# M4 engine calibration

Checks that a running vLLM server sees exactly the token IDs Suez predicts
locally, that its prefix-cache reset works, and that reported reuse follows
the block rule. Results from the first run are in
[M4 CPU results](../../docs/notes/m4-cpu-results-20261107.md).

## Files

- `probes.json`: probe text, the 17-step sequence, checks and stop rules.
- `build_probes.py`: writes exact request bodies and local expected IDs.
- `run-manifest.template.json`: machine, install, model and launch values to fill per run.
- `check_manifest.py`: lists missing manifest values and conflicts.
- `calibrate.py`: sends the sequence once. Dry run by default; `--run` sends.
- `source-pin.json`: the vLLM source files read for these checks.

## Reproduce on a Mac CPU

Setup (vLLM source build, Qwen3-0.6B weights) is in the
[CPU build plan](../../docs/notes/m4-cpu-build-plan.md). Then:

```sh
uv run --project analysis --offline --no-python-downloads python \
  analysis/tokenizer_assets.py --pin experiments/tokenizer/qwen3-0.6b.json \
  --out artifacts/qwen3-0.6b-tokenizer
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \
  --python 3.12 --no-python-downloads python -B \
  experiments/cache-ground-truth/build_probes.py --out artifacts/m4-probes-NEW
```

Start the server on loopback with the flags in the build plan, start Suez in
front of it, copy and fill the manifest, then:

```sh
python3 experiments/cache-ground-truth/check_manifest.py artifacts/RUN/manifest.json
python3 experiments/cache-ground-truth/calibrate.py --probes artifacts/m4-probes-NEW \
  --manifest artifacts/RUN/manifest.json --direct http://127.0.0.1:18000 \
  --suez http://127.0.0.1:18080 --out artifacts/RUN/out --run
python3 analysis/report.py traces/RUN
```

`--run` refuses to start until the manifest has no open gates and names this
spec, model and tokenizer revision. Raw output stays in ignored `artifacts/`.

## Checks

| Check | Meaning |
| --- | --- |
| `parity` | Engine `prompt_token_ids` equal the local IDs element by element |
| `cached_zero`, `cached_positive` | Reported `cached_tokens` is 0, or above 0 |
| `cached_within_eligible`, `cached_within_lcp:P` | Reuse is at most prompt length - 1, or at most the exact-ID LCP with probe P |
| `block_prediction:P` | Reuse equals `floor(min(LCP, n - 1) / B) * B` |
| `predicted` | Reuse and created tokens equal frozen predictor output (used by E4) |
| `same_prompt_ids_as:S` | Same returned IDs as step S |

A parity mismatch, failed reset, transport failure or missing cache details
stops the run. Other failures are recorded and the run continues.

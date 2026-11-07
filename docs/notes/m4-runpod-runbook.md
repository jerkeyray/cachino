# M4 calibration runbook: RunPod RTX 4090

Status: plan only, written 2026-11-05. Deferred on 2026-11-07 for lack of budget;
M4 ran on the CPU backend instead ([results](m4-cpu-results-20261107.md)). The
probe spec now names Qwen3-0.6B, so an 8B run here needs a spec copy naming
`Qwen/Qwen3-8B`, built with `--pin experiments/tokenizer/model.json --assets
artifacts/qwen3-8b-tokenizer`. Nothing here has been rented, installed
or downloaded. The user chose a RunPod on-demand RTX 4090 with a $10 spend
ceiling and a 3 hour time ceiling. Each paid or installing step still needs a
go-ahead when it is reached. This runbook executes the
[M4 calibration package](m4-calibration-plan.md).

## Resource and ceilings

| Item | Value | Reason |
| --- | --- | --- |
| Provider | RunPod, Secure Cloud, on-demand (not spot or interruptible) | A preempted pod would cut the sequence; per-second billing |
| GPU | 1x RTX 4090, 24 GB | Qwen3-8B BF16 is about 16.4 GB; the remaining KV cache holds far more than the 1,124-token largest probe |
| Image | A RunPod PyTorch template on CUDA 12.8 or newer | The vLLM wheel brings its own torch; the host driver must support that torch's CUDA build |
| Container disk | 50 GB, no network volume | Weights about 16 GB plus torch and vLLM; a network volume bills after the pod ends |
| Exposed ports | SSH (TCP 22) only | The vLLM port stays on the pod's loopback and is reached through an SSH forward |
| Time ceiling | 3 hours from pod start | Expected use is about 1 to 2 hours |
| Spend ceiling | $10 | Prepaid balance of $10 with auto-pay off makes the ceiling hard |

## What the user does

These involve accounts and payment, so the agent cannot do them:

1. Create the RunPod account and add $10 of credit. Leave auto-pay off.
2. Add an SSH public key under account settings.
3. Deploy the pod with the values above and share the SSH command RunPod shows
   (`ssh root@<ip> -p <port> -i <key>`). No passwords or keys go into the chat.
4. Terminate the pod at the end (or approve the agent doing it from the pod
   console), then confirm in billing that charges stopped. A stopped pod still
   bills for its disk; terminate, not stop.

## Phase 0: local preparation (no spend, before deploying)

```bash
cd /Users/jerkeyray/code/suez
go build -o bin/suez ./cmd/suez
RUN=m4-run-$(date -u +%Y%m%d-%H%M)
mkdir artifacts/$RUN
cp experiments/cache-ground-truth/run-manifest.template.json artifacts/$RUN/manifest.json
python3 experiments/cache-ground-truth/calibrate.py \
  --probes artifacts/m4-probes-20261105-02 --manifest artifacts/$RUN/manifest.json \
  --direct http://127.0.0.1:18000 --suez http://127.0.0.1:18080 --out artifacts/$RUN/out
```

The last command is a dry run. It must print the plan and list the manifest's
open gates. Fill `run` (with `probe_spec_sha256` from the dry run's
`spec_sha256`) and `access` now. `model.local_tokenizer_revision` is already set.

## Phase 1: pod checks (spend starts, about 5 minutes)

```bash
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
nvidia-smi | head -5
python3 --version
df -h /root
```

Stop and terminate if the GPU is not one RTX 4090, or the free disk is under
40 GB. Record GPU model, memory, driver and OS in the manifest.

## Phase 2: install the pinned engine (about 10 minutes)

The wheel is the vLLM project's build of the exact source pin, served from its
commit index. Installing it by URL avoids resolving a different vLLM release.

```bash
python3 -m venv /root/m4-venv
. /root/m4-venv/bin/activate
pip install --upgrade pip
pip install 'https://wheels.vllm.ai/2173d7121fc6e8d756c884f46691546c61b60c57/vllm-0.33.0rc1.dev595%2Bg2173d7121-cp38-abi3-manylinux_2_28_x86_64.whl'
python -c 'import vllm, torch; print(vllm.__version__, torch.__version__, torch.version.cuda, torch.cuda.is_available())'
pip freeze > /root/m4-pip-freeze.txt
```

Stop if the version is not `0.33.0rc1.dev595+g2173d7121` or
`torch.cuda.is_available()` is false. A false result usually means the host
driver is older than the torch CUDA build; redeploy on a newer CUDA filter
rather than downgrading torch, which would leave the pinned install.

## Phase 3: weights at the pinned revision (5 to 15 minutes)

Qwen3-8B is not gated, so no Hugging Face token is used.

```bash
export HF_HOME=/root/hf
hf download Qwen/Qwen3-8B --revision b968826d9c46dd6066d109eabc6255188de91218
# if `hf` is absent, the older CLI takes the same arguments:
# huggingface-cli download Qwen/Qwen3-8B --revision b968826d9c46dd6066d109eabc6255188de91218
python - <<'EOF'
import glob, hashlib, json
p = glob.glob("/root/hf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218/tokenizer_config.json")[0]
print(hashlib.sha256(json.load(open(p))["chat_template"].encode()).hexdigest())
EOF
```

The printed chat template hash must equal the local pin's
`a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`
(SHA-256 of `tokenizer_config.json["chat_template"]` as UTF-8). A mismatch stops
the run before launch.

## Phase 4: launch on the pod's loopback (about 3 minutes)

Flags checked against the pinned `arg_utils.py`, `config/cache.py` and
`entrypoints/common/cli_args.py`:

```bash
export HF_HOME=/root/hf VLLM_SERVER_DEV_MODE=1
nohup vllm serve Qwen/Qwen3-8B \
  --revision b968826d9c46dd6066d109eabc6255188de91218 \
  --tokenizer-revision b968826d9c46dd6066d109eabc6255188de91218 \
  --host 127.0.0.1 --port 8000 \
  --dtype bfloat16 --max-model-len 4096 --gpu-memory-utilization 0.90 \
  --tensor-parallel-size 1 --seed 0 \
  --enable-prefix-caching --block-size 16 --prefix-caching-hash-algo sha256 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-prompt-tokens-details --enable-per-request-metrics \
  > /root/m4-vllm.log 2>&1 &
```

Reasons for each choice:

- `--host 127.0.0.1` keeps the dev-mode reset endpoint off every network
  interface. Only the SSH forward reaches it.
- Prefix caching, block size and hash algorithm are stated explicitly rather
  than left to defaults. The source default for prefix caching is on and for the
  hash algorithm is `sha256`, but the run records what the server reports.
- `--enable-per-request-metrics` is rejected together with
  `--disable-log-stats` (cli_args.py L573-579), so stats logging stays on.
- No speculative, KV transfer, KV offloading or KV events flags are passed.
  The manifest records `kv_events_config` as `"disabled"`.

Wait for `/root/m4-vllm.log` to show the server listening, then check from
the pod: `curl -s 127.0.0.1:8000/v1/models`.

The pinned cache config also has `prefix_match_unit`, `hash_block_size` and
`cache_hit_alignment_tokens`. The last two are derived during KV cache setup,
and hits land on `cache_hit_alignment_tokens`. Read them and `num_gpu_blocks`
from the startup log. If the log does not state the hash block size, find where
the installed source logs or exposes it before filling the manifest. Do not
copy the block size into that field.

## Phase 5: tunnel and local proxy

On the Mac, in separate terminals:

```bash
ssh -N -L 18000:127.0.0.1:8000 root@<ip> -p <port> -i <key>
```

```bash
./bin/suez -listen 127.0.0.1:18080 -upstream http://127.0.0.1:18000 \
  -trace-dir traces/$RUN -session m4-$RUN
```

`curl -s 127.0.0.1:18000/v1/models` must list `Qwen/Qwen3-8B`.

## Phase 6: manifest, then the run (under a minute of requests)

Fill the remaining manifest values from the pod: hardware from Phase 1,
install from Phase 2 (`install_method` is the wheel URL, `installed_commit`
the `g2173d7121` suffix expanded to the full pin), model, cache, scheduling and
telemetry from the launch line and startup log. Then:

```bash
python3 experiments/cache-ground-truth/check_manifest.py artifacts/$RUN/manifest.json
python3 experiments/cache-ground-truth/calibrate.py \
  --probes artifacts/m4-probes-20261105-02 --manifest artifacts/$RUN/manifest.json \
  --direct http://127.0.0.1:18000 --suez http://127.0.0.1:18080 \
  --out artifacts/$RUN/out --run
python3 analysis/report.py traces/$RUN
```

`check_manifest.py` must report nothing missing and no conflicts, or `--run`
refuses to start. The client sends each step once, with no retries.

## Phase 7: keep evidence and end spend

```bash
scp -P <port> -i <key> root@<ip>:/root/m4-vllm.log root@<ip>:/root/m4-pip-freeze.txt artifacts/$RUN/
```

Then terminate the pod and confirm billing stopped. `artifacts/` and
`traces/` are ignored; nothing from the run is committed raw. A summary of the
results goes into a tracked note afterwards.

## Abort rules

- Terminate at the time or spend ceiling even if the run is incomplete.
- A parity mismatch or failed reset is a finding, not a reason to retry with
  changed settings in the same session. Keep the bodies and decide afterwards.
- Do not expose port 8000, add a public HTTP port, or run the server on
  `0.0.0.0`.

## Time budget

| Phase | Expected |
| --- | --- |
| Pod start and checks | 5 to 10 min |
| Wheel install | about 10 min |
| Weights download | 5 to 15 min |
| Model load | about 3 min |
| Manifest fill and run | 15 to 30 min |
| Evidence copy and terminate | about 5 min |
| Total | about 45 to 75 min, under $1 at $0.69 to $0.89 per hour |

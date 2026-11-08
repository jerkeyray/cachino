#!/bin/sh
# Capture one two-turn chat through the Cachino proxy and check it against two
# chat templates. The upstream is the bundled fake model, which always replies
# "Hello from the fake model." and reports no cache metrics, so no model or
# engine runs. Only the prompts matter: the template check renders each
# captured request to exact token IDs with the pinned tokenizer and reports
# where turn 2 stops sharing turn 1's prompt.
#
# Needs Go, uv, and the pinned tokenizer snapshots (downloaded once from the
# Hugging Face Hub at pinned revisions and hash-checked):
#   uv run --project analysis --offline --no-python-downloads python analysis/tokenizer_assets.py \
#     --pin experiments/tokenizer/qwen3-0.6b.json --out artifacts/qwen3-0.6b-tokenizer
#   uv run --project analysis --offline --no-python-downloads python analysis/tokenizer_assets.py \
#     --pin experiments/tokenizer/qwen2.5-0.5b-instruct.json --out artifacts/qwen2.5-0.5b-instruct-tokenizer
#
# Usage: examples/two-turn/run.sh [new output directory]
# Compare the printed summaries with examples/two-turn/expected.txt.
set -eu
cd "$(dirname "$0")/../.."
out="${1:-traces/two-turn-$(date -u +%Y%m%dT%H%M%SZ)}"
fake_port="${FAKE_PORT:-18181}"
proxy_port="${PROXY_PORT:-18182}"
mkdir -p "$out"
go build -o "$out/fake-model" ./cmd/fake-model
go build -o "$out/cachino" ./cmd/cachino

"$out/fake-model" -listen "127.0.0.1:$fake_port" 2>"$out/fake-model.log" &
fake_pid=$!
trap 'kill "$fake_pid" ${proxy_pid:-} 2>/dev/null || true' EXIT

post() {
  # Retries cover the proxy still starting; an HTTP error still fails the script.
  curl --silent --fail-with-body --retry 50 --retry-connrefused --retry-delay 0 \
    "http://127.0.0.1:$proxy_port/v1/chat/completions" -H 'Content-Type: application/json' -d "$1" >/dev/null
}

# One capture per template: the renderer only accepts requests naming its pinned model.
capture() {
  model="$1" name="$2"
  "$out/cachino" -listen "127.0.0.1:$proxy_port" -upstream "http://127.0.0.1:$fake_port" \
    -trace-dir "$out/$name" -session "$name" 2>"$out/$name.log" &
  proxy_pid=$!
  # Turn 2 resends turn 1 with the fake model's reply appended, as an agent would.
  post '{"model":"'"$model"'","messages":[{"role":"user","content":"List the files."}],"stream":false}'
  post '{"model":"'"$model"'","messages":[{"role":"user","content":"List the files."},{"role":"assistant","content":"Hello from the fake model."},{"role":"user","content":"Now read README.md."}],"stream":false}'
  kill "$proxy_pid"
  wait "$proxy_pid" 2>/dev/null || true
  proxy_pid=
}
capture Qwen/Qwen3-0.6B qwen3
capture Qwen/Qwen2.5-0.5B-Instruct qwen2.5

T="uv run --project analysis/tokenizer --frozen --python 3.12 --no-python-downloads python -B"
check() {
  echo "== $1"
  if ! HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 $T analysis/template_check.py --capture "$out/$1" \
      --pin "experiments/tokenizer/$2.json" --assets "artifacts/$2-tokenizer" --rows \
      >"$out/$1-template.json" 2>"$out/$1-template.err"; then
    cat "$out/$1-template.err" >&2
    exit 1
  fi
  python3 - "$out/$1-template.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))["rows"][0]
print("turn 2:", r["category"], "| diverges at token", r["divergence"], "|", r["previous_unshared"], "turn-1 tokens not shared")
print("  turn 1 continues:", repr(r["previous_next"]))
print("  turn 2 continues:", repr(r["current_next"]))
PY
}
check qwen3 qwen3-0.6b
check qwen2.5 qwen2.5-0.5b-instruct

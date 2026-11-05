#!/bin/sh
# CPU-only local reconstruction. No model weights or inference are involved.
set -eu
cd "$(dirname "$0")/../.."
run_dir="${1:-artifacts/tokenizer-$(date -u +%Y%m%dT%H%M%SZ)}"
assets="artifacts/qwen3-8b-tokenizer"
if [ ! -d "$assets" ]; then
  uv run --project analysis --offline --no-python-downloads python \
    analysis/tokenizer_assets.py --out "$assets"
fi
# Validate the actual tokenizer/runtime before producing a new capture. Setup
# failures must not leave a run that appears to have reached reconstruction.
uv run --project analysis/tokenizer --frozen --python 3.12 --no-python-downloads python \
  analysis/reconstruct.py --assets "$assets" --check-setup
mkdir -p "$(dirname "$run_dir")"
mkdir -m 700 "$run_dir"
go run ./cmd/suez-demo -out "$run_dir/capture"
uv run --project analysis/tokenizer --frozen --python 3.12 --no-python-downloads python \
  analysis/reconstruct.py "$run_dir/capture" --assets "$assets" \
  --out "$run_dir/reconstructed" --allow-model-remap
uv run --project analysis --offline --no-python-downloads python - "$run_dir/reconstructed/summary.json" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text())
rows = report['requests']
assert len(rows) == 5 and sum(row['status'] == 'rendered' for row in rows) == 4
assert rows[4]['status'] == 'unsupported'
assert rows[0]['token_ids_sha256'] == rows[1]['token_ids_sha256'] == rows[3]['token_ids_sha256']
assert rows[2]['token_ids_sha256'] != rows[0]['token_ids_sha256']
assert rows[0]['adjacent_lcp_tokens'] is None
assert rows[1]['adjacent_lcp_tokens'] == rows[1]['prompt_tokens']
assert 0 <= rows[2]['adjacent_lcp_tokens'] < min(rows[1]['prompt_tokens'], rows[2]['prompt_tokens'])
assert report['engine_validated'] is False and all(row['model_remapped'] for row in rows[:4])
print('CPU reconstruction checks passed; actual engine/cache reuse remains unvalidated.')
PY
printf 'Reconstruction report: %s/reconstructed/summary.json\n' "$run_dir"

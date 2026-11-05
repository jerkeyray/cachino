#!/bin/sh
# Structural token-prefix experiment only; never sends requests to a model.
set -eu
cd "$(dirname "$0")/../.."
run_dir="${1:-artifacts/prefix-probe-$(date -u +%Y%m%dT%H%M%SZ)}"
uv run --project analysis/tokenizer --frozen --offline --python 3.12 \
  --no-python-downloads python analysis/prefix_probe.py \
  --assets artifacts/qwen3-8b-tokenizer --out "$run_dir"
printf 'JSON: %s/report.json\nCSV: %s/report.csv\n' "$run_dir" "$run_dir"

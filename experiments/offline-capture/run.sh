#!/bin/sh
# Synthetic development check, not a cache or inference performance experiment.
set -eu
cd "$(dirname "$0")/../.."
run_dir="${1:-traces/offline-$(date -u +%Y%m%dT%H%M%SZ)}"
go run ./cmd/cachino-demo -out "$run_dir"
uv run --project analysis --offline --no-python-downloads python analysis/report.py \
  "$run_dir" --sqlite "$run_dir/index.sqlite" > "$run_dir/report.json"
uv run --project analysis --offline --no-python-downloads python - "$run_dir/report.json" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text())
metrics = report['metrics']
assert metrics['requests'] == metrics['transport_completed'] == metrics['fully_verified_captures'] == 5
assert metrics['http_errors'] == 1 and metrics['unfinished'] == 0
assert metrics['known_cache_metrics_requests'] == 0 and metrics['known_cached_tokens'] is None
rows = report['requests']
assert [row['http_status'] for row in rows] == [200, 200, 200, 200, 400]
assert rows[0]['request_sha256'] == rows[1]['request_sha256'] != rows[2]['request_sha256']
assert rows[3]['sse_done'] is True and rows[4]['request_json_valid'] is False
assert not report['warnings'] and all(not row['parse_error'] for row in rows)
print('Offline capture checks passed.')
PY
printf 'Report: %s/report.json\nSQLite: %s/index.sqlite\n' "$run_dir" "$run_dir"

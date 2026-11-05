# Offline capture milestone

Run from the repository root, with Go, Python 3.9+ and uv installed:

```sh
./experiments/offline-capture/run.sh traces/my-offline-run
```

The output path must be new. This sends five ordered requests through real HTTP
servers/transports over `net.Pipe`: JSON A, exact repeat A, mutated JSON B,
streaming A, and invalid JSON. Expected HTTP statuses are 200, 200, 200, 200,
400. The raw traces, JSON report and derived SQLite index stay under the output
path. No TCP ports, model weights, hosted API or GPU are used.

Expected report: five completed HTTP exchanges, five fully verified captures,
one HTTP error, no unfinished requests, and zero requests with known cache
metrics. The streaming request has `sse_done: true`. The first two request
hashes match. Missing cache counts/ratio are null; no cache result is simulated.
The invalid JSON request is deliberately retained and flagged as invalid input.

Inspect the derived SQLite index, for example with Python's sqlite3 module or
a SQLite client:

```sql
SELECT sequence, turn, http_status, state, cached_tokens
FROM requests ORDER BY sequence;
```

This is an integration fixture, not an E1/E2 cache experiment. The repeated and
mutated requests test capture fidelity only. Engine metrics, tokenizer pinning,
GPU timings, cache resets and inference performance are deliberately absent.

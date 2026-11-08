# Cachino

Cachino explains why coding agents make LLM servers redo prefill work. A Go
proxy captures agent traffic byte for byte; Python analysis rebuilds the exact
token IDs the model sees and predicts, request by request, how much of each
prompt a pinned vLLM build serves from its prefix cache, and why the rest was
recomputed.

## Findings

From a raw capture, Cachino attributes each request's lost reuse to prompt
change, block rounding, eviction or sharing with other sessions, and its
predictions matched vLLM's reported counts exactly on every sequential request
tested. On a frozen coding task with mini-swe-agent and Qwen3 on the vLLM CPU
backend:

- In single sessions, 64% to 91% of recomputed tokens are new content. Qwen3's
  template adds re-prefill of the previous reply every turn; a template that
  keeps history aligned removes it.
- Sessions sharing a cache evict each other once their combined working set
  exceeds it. The threshold is predictable from the capture, and limiting
  concurrency removed the eviction on one trace, cutting recomputation 16.5%.

On a Tesla T4 the same trace gave identical counts, and each recomputed token
cost about 1.34 ms of time to first token.

Both mechanisms are known; exact, engine-validated attribution is Cachino's
part. Details: [results](docs/results.md), [method](docs/method.md),
[related work](docs/related-work.md), [plan](docs/plan.md).

## Quick start

Requires Go 1.26 or newer; the proxy uses only the standard library.

```sh
go run ./cmd/fake-model -listen 127.0.0.1:8081
```

```sh
go run ./cmd/cachino -listen 127.0.0.1:8080 -upstream http://127.0.0.1:8081
```

```sh
curl --fail-with-body http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

Add `-trace-dir traces/NEW -session NAME` to capture, then run
`analysis/report.py` on the directory. A full synthetic capture and report,
with no ports or GPU:

```sh
./experiments/offline-capture/run.sh traces/my-offline-run
```

Forwarding, capture and session limits are described in the
[proxy reference](docs/proxy.md).

## Layout

| Path | Contents |
| --- | --- |
| `cmd/cachino`, `internal/proxy`, `internal/trace` | Proxy and capture |
| `cmd/fake-model`, `cmd/cachino-demo` | Test upstream and synthetic demo |
| `analysis/` | Reports, token reconstruction and tests |
| `experiments/cache-ground-truth/` | Engine calibration ([README](experiments/cache-ground-truth/README.md)) |
| `experiments/cache-eviction/` | Cache predictor and eviction probes ([README](experiments/cache-eviction/README.md)) |
| `experiments/agent-task/` | Frozen task, agent sessions, attribution, sweeps and scheduling ([README](experiments/agent-task/README.md)) |
| `experiments/proxy-overhead/` | Paired latency measurement ([README](experiments/proxy-overhead/README.md)) |
| `experiments/gpu-confirmation/` | Token-ID bundle and GPU replay ([README](experiments/gpu-confirmation/README.md)) |
| `docs/` | Results, method, related work, plan and the dated archive |

## Verify

```sh
go build ./... && go vet ./... && go test -race ./...
```

```sh
uv run --project analysis --offline --no-python-downloads python -m unittest discover -s analysis
```

`go test -race -tags=integration ./...` adds loopback TCP parity tests and
needs permission to bind local ports.

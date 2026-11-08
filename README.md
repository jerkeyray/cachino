# Cachino

**Where coding agents lose prefix reuse.** Cachino captures coding-agent traffic
byte for byte, rebuilds the exact token IDs the model sees, and predicts,
request by request, how much of each prompt a pinned vLLM build serves from its
prefix cache. Whatever is recomputed is attributed to its cause: a prompt
change, block rounding, eviction, or reuse from another session.

![Pipeline](docs/figures/pipeline.svg)

## Findings

Measured with mini-swe-agent (and smolagents for comparison) and Qwen3 on a frozen coding task, on vLLM for the
Mac CPU and a Tesla T4. Full study: **[report](docs/report.md)**.

| Finding | Evidence |
| --- | --- |
| Predictions match the engine exactly | Every sequential request on CPU and GPU; 259 of 265 under live concurrency, the rest within a few blocks |
| Single sessions recompute mostly new content | 64% to 91% of recomputed tokens; the rest is block rounding and, with Qwen3, the previous reply |
| Qwen3's template re-prefills each reply | It drops the empty thinking cue from history; a template without the mismatch let vLLM reuse a 442-token reply |
| A second harness loses replies differently | smolagents rewrites each tool call into history as text about twice as long, with a random ID, so replies are never reusable |
| Shared caches evict past a predictable threshold | 17% to 25% of recomputation when sessions outgrew the cache; the capture predicted which runs would, 9 of 9 |
| Eviction costs time | About 1.34 ms of time to first token per recomputed token on a T4; +31% and +86% below the threshold |
| Scheduling can remove it | At most two sessions at a time cut recomputation 16.5% on an evicting trace; live, the proxy's session cap removed eviction where it occurred but cost waiting time elsewhere |

![Eviction against cache size](docs/figures/eviction-curve.svg)

Both mechanisms were already reported elsewhere; Cachino's part is exact,
engine-validated attribution from a raw capture ([related work](docs/report.md#4-related-work)).

## Quick start

Requires Go 1.26 or newer; the proxy uses only the standard library.

```sh
go run ./cmd/fake-model -listen 127.0.0.1:8081
```

```sh
go run ./cmd/cachino -listen 127.0.0.1:8080 -upstream http://127.0.0.1:8081 \
  -trace-dir traces/demo -session demo
```

```sh
curl --fail-with-body http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

Then `analysis/report.py traces/demo` verifies and summarizes the capture. Point
`-upstream` at a vLLM server to capture real traffic. Proxy details:
[reference](docs/proxy.md). Full reproduction: [reproduce](docs/reproduce.md).

## Analyze a capture

`analysis/cachino_analyze.py` runs every analysis command; those that render
tokens need the tokenizer environment ([reproduce](docs/reproduce.md)). For
example, to check whether a capture's history stays aligned with the chat
template:

```sh
python -B analysis/cachino_analyze.py template --capture traces/demo/capture \
  --pin experiments/tokenizer/qwen3-0.6b.json --assets artifacts/qwen3-0.6b-tokenizer
```

It classifies each turn as append-only, generation prompt dropped (the template
renders history differently from how it prompted generation), reply dropped, or
history changed. Other commands: `attribute`, `concurrent`, `curve`, `sweep`,
`schedule`, `overlap`, `summary`.

## Repository

| Path | Contents |
| --- | --- |
| `cmd/`, `internal/` | Proxy, capture, fake upstream and demo (Go) |
| `analysis/` | Rendering, cache predictor, attribution and replays (Python) |
| `experiments/` | Calibration, eviction probes, agent task and sessions, proxy overhead, GPU confirmation, figures |
| `docs/` | [Report](docs/report.md), [reproduce](docs/reproduce.md), [proxy reference](docs/proxy.md), [run records](docs/records/) |

## Tests

```sh
go build ./... && go vet ./... && go test -race ./...
```

```sh
uv run --project analysis --offline --no-python-downloads python -m unittest discover -s analysis
```

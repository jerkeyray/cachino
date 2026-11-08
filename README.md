# Cachino

**Where coding agents lose prefix reuse: an engine-validated measurement study.**

Coding agents resend their whole conversation on every turn. A serving engine
skips the repeated part only if it matches an earlier prompt token for token
and is still in the prefix cache. When it does not, the engine recomputes it,
and the server reports a single number, the cached tokens, with no cause.

Cachino recovers the cause. It captures agent traffic byte for byte, rebuilds
the exact token IDs the model sees, replays a pinned vLLM build's prefix-cache
rules to predict each request's hit, and attributes every recomputed token to
new content, a changed prompt, block rounding, eviction or reuse from another
session. Every prediction is checked against the engine.

**[Read the report](docs/report.md)** · [Reproduce](docs/reproduce.md) ·
[Try it without an engine](examples/README.md) · [Proxy reference](docs/proxy.md) ·
[Run records](docs/records/)

![Three ways an agent turn loses prefix reuse](docs/figures/reuse-loss.svg)

## Results

Measured with mini-swe-agent and smolagents driving Qwen3 (0.6B and 1.7B) on a
frozen coding task, served by vLLM at commit `2173d712` on an Apple M5 CPU and a
Tesla T4.

| # | Finding | Evidence | Report |
| --- | --- | --- | --- |
| 1 | Per-request predictions match the engine | Every sequential request on CPU and T4; 710 of 738 under live concurrency, most misses within a few blocks | [3.1](docs/report.md#31-the-predictions-are-exact) |
| 2 | Modeling residency is what makes them exact | A prefix-matching baseline that assumes blocks stay cached has 24x the error (8,320 vs 352 tokens); its misses are exactly the evicted reuse | [3.1](docs/report.md#31-the-predictions-are-exact) |
| 3 | A single session mostly recomputes new content | 64% to 91% of recomputation; the rest is block rounding and, with Qwen3, the previous reply | [3.2](docs/report.md#32-single-sessions-mostly-new-content) |
| 4 | Qwen3's chat template re-prefills every reply | History omits the 4-token empty thinking cue; served with the cue kept, no reply was re-prefilled, but the model's next action changed | [3.3](docs/report.md#33-the-template-boundary), [3.9](docs/report.md#39-keeping-the-cue-in-a-sandboxed-task) |
| 5 | Harness serialization can make replies unreusable | smolagents rewrites each tool call as text about twice as long, with a random ID | [3.4](docs/report.md#34-a-second-harness) |
| 6 | Shared caches evict past a threshold the capture predicts | 17% to 25% of recomputation in runs that outgrew the cache; the threshold called 9 of 9 runs | [3.5](docs/report.md#35-shared-caches-and-the-eviction-threshold) |
| 7 | Eviction costs time to first token | +31% and +86% on a T4 below the threshold, about 1.34 ms per recomputed token | [3.6](docs/report.md#36-what-eviction-costs-in-time) |
| 8 | Limiting concurrency removes eviction, at a price | Replay: -16.5% recomputation. Live: the cap removed eviction in all three seeds that evicted (1,243 s to 299 s wall clock in one) and added waiting where nothing was evicted; a size-based budget avoided needless waits, but sessions it paused lost their cache | [3.7](docs/report.md#37-scheduling-to-avoid-eviction), [3.8](docs/report.md#38-a-live-concurrency-limit) |

![Eviction against cache size](docs/figures/eviction-curve.svg)

The mechanisms themselves were reported before: the Qwen3 template boundary
upstream, and cache-aware scheduling for agents in CacheWise and Continuum.
What Cachino adds is exact, per-request, engine-validated attribution from a raw
capture ([related work](docs/report.md#4-related-work)).

## How it works

![How Cachino measures prefix reuse](docs/figures/pipeline.svg)

| Stage | Code | What it guarantees |
| --- | --- | --- |
| Capture | [`cmd/cachino`](cmd/cachino), [`internal/proxy`](internal/proxy), [`internal/trace`](internal/trace) | Request and response bytes forwarded unchanged and stored with lifecycle events and hashes, synced before the next event |
| Render | [`analysis/reconstruct.py`](analysis/reconstruct.py) | The exact prompt token IDs, from a pinned tokenizer and chat template; unknown request fields are rejected, not dropped |
| Predict | [`analysis/predict.py`](analysis/predict.py) | vLLM's free-queue prefix cache at `2173d712`, rule by rule, cited to source lines ([eviction plan](docs/records/e4-eviction-plan.md)) |
| Attribute | [`analysis/live_attribution.py`](analysis/live_attribution.py), [`analysis/concurrent_attribution.py`](analysis/concurrent_attribution.py) | Each recomputed token assigned to one cause; a missing engine metric stays unknown |
| Check | [`analysis/cache_sweep.py`](analysis/cache_sweep.py), [`analysis/overlap_replay.py`](analysis/overlap_replay.py) | The same token IDs replayed to the engine after a reset; prompt IDs and cached tokens compared per request |

The measurement contract: missing cache metrics are unknown, never zero; equal
token counts are never taken as equal tokens; adjacent-request prefix length is
a diagnostic, not a cache model; proxy timestamps are not engine time.

## Quick start

Requires Go 1.26 or newer. The proxy uses only the standard library.

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

`python3 analysis/report.py traces/demo` verifies and summarizes the capture.
Point `-upstream` at a vLLM server to capture real traffic; flags and the trace
format are in the [proxy reference](docs/proxy.md).

### Try it without an engine

Two synthetic examples reproduce small versions of the study's results on a
fresh checkout, with committed expected output ([examples](examples/README.md)):

```sh
python3 examples/one-block/run.py
```

finds a one-block eviction threshold in a three-session trace, using the
standard library only, and

```sh
examples/two-turn/run.sh
```

captures a two-turn chat through the proxy and shows Qwen3's template dropping
the 4-token thinking cue where Qwen2.5's stays append-only (needs the pinned
tokenizers).

### Analyze a capture

`analysis/cachino_analyze.py` dispatches every analysis: `attribute`,
`concurrent`, `template`, `curve`, `sweep`, `schedule`, `overlap` and `summary`.
Commands that render tokens run in the tokenizer environment
([reproduce](docs/reproduce.md#1-environments)):

```sh
python -B analysis/cachino_analyze.py template --capture traces/demo \
  --pin experiments/tokenizer/qwen3-0.6b.json --assets artifacts/qwen3-0.6b-tokenizer
```

## Reproducing the study

[docs/reproduce.md](docs/reproduce.md) lists environments, the pinned engine
launch and every experiment in order. Each [run record](docs/records/) names
its run directory, spec hash and code commit. Raw captures stay private;
figures regenerate from them with `experiments/figures/make_figures.py`, and the
two diagrams with `experiments/figures/make_diagrams.py`.

## Scope

One task, two harnesses and models of 0.6B to 1.7B parameters: the shares
describe this setup, not production traffic. GPU timing comes from one T4 and
sequential replay; live concurrency timing is on a Mac CPU and compares
conditions only. Full list: [limitations](docs/report.md#5-limitations).

## Repository

| Path | Contents |
| --- | --- |
| [`cmd/`](cmd), [`internal/`](internal) | Proxy, capture, session limits, fake upstream and demo (Go) |
| [`analysis/`](analysis) | Rendering, cache predictor, attribution, replays and tests (Python) |
| [`experiments/`](experiments) | Calibration, eviction probes, agent sessions, proxy overhead, GPU confirmation, figures |
| [`examples/`](examples/README.md) | Synthetic checks a fresh checkout can run |
| [`docs/`](docs/README.md) | Report, reproduction guide, proxy reference and run records |

## Tests

```sh
go build ./... && go vet ./... && go test -race ./...
```

```sh
uv run --project analysis --offline --no-python-downloads python -m unittest discover -s analysis
```

## Citing

```bibtex
@misc{cachino2026,
  title  = {Cachino: Where Coding Agents Lose Prefix Reuse},
  author = {jerkeyray},
  year   = {2026},
  note   = {Engine-validated measurement study of vLLM prefix caching under coding-agent traffic},
  url    = {https://github.com/jerkeyray/cachino}
}
```

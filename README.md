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

**[Read the report](docs/report.md)** · [How to read the results](docs/reading-results.md) ·
[Result card: 487 vs 488 blocks](docs/result-card-487-488.md) · [Reproduce](docs/reproduce.md) ·
[Try it without an engine](examples/README.md) · [Run records](docs/records/)

> **Status · 8 November 2026.** The study is complete for its setup: one frozen
> task, two harnesses, Qwen3 at 0.6B and 1.7B, vLLM at `2173d712`. Every number
> below is a dated measurement from runs on 7 and 8 November 2026. Read
> [how to read the results](docs/reading-results.md) before quoting one.

![Three ways an agent turn loses prefix reuse](docs/figures/reuse-loss.svg)

## Results

The headline: on one captured three-session trace, Cachino predicted that a
cache of 487 blocks would lose exactly 32 tokens to eviction and 488 none, and
vLLM on a Mac CPU matched all 30 requests at both sizes; a Tesla T4 matched the
same curve at 384, 438, 488 and 640 blocks ([result card](docs/result-card-487-488.md)).
That is one trace from small models, not a model of production caches.

| # | Finding | Evidence | Boundary | Report |
| --- | --- | --- | --- | --- |
| 1 | Per-request predictions match the engine | Every sequential request on CPU and T4; 710 of 738 under live concurrency | Exact only for sequential replay; overlap misses reach 26 blocks in the most evicting runs; one vLLM commit | [3.1](docs/report.md#31-the-predictions-are-exact) |
| 2 | Modeling residency is what makes them exact | A baseline that assumes matching blocks stay cached has 24x the error (8,320 vs 352 tokens); its misses are exactly the evicted reuse | Hit prediction, not cause attribution: under overlap, a shortfall can also be blocks not yet stored | [3.1](docs/report.md#31-the-predictions-are-exact) |
| 3 | A single session mostly recomputes new content | 64% to 91% of recomputation; the rest is block rounding and, with Qwen3, the previous reply | Three sessions on one task, read-only observations, no eviction | [3.2](docs/report.md#32-single-sessions-mostly-new-content) |
| 4 | Qwen3's chat template re-prefills every reply | History omits the 4-token empty thinking cue; with the cue kept, no reply was re-prefilled, but the next action changed | Qwen3 with thinking off; no session solved the task, so its effect on success is unknown | [3.3](docs/report.md#33-the-template-boundary), [3.9](docs/report.md#39-keeping-the-cue-in-a-sandboxed-task) |
| 5 | Harness serialization can make replies unreusable | smolagents rewrites each tool call as text about twice as long, with a random ID | One harness version (1.26.0), four sessions | [3.4](docs/report.md#34-a-second-harness) |
| 6 | Shared caches evict past a threshold the capture predicts | 17% to 25% of recomputation in runs that outgrew the cache; the threshold called 9 of 9 runs | Sessions from one task on one cache size; threshold exact for sequential replay | [3.5](docs/report.md#35-shared-caches-and-the-eviction-threshold) |
| 7 | Eviction costs time to first token | +31% and +86% on a T4 below the threshold | One T4, one trace, requests sent one at a time; the 1.34 ms per token slope is a fit, the totals are measured | [3.6](docs/report.md#36-what-eviction-costs-in-time) |
| 8 | Limiting concurrency removes eviction, at a price | Replay: -16.5% recomputation. Live: a two-session cap removed eviction in all three seeds that evicted and added waiting elsewhere; a size-based budget avoided needless waits, but paused sessions lost their cache | Replay delay counted in positions; live timing on a Mac CPU over five seeds compares conditions only | [3.7](docs/report.md#37-scheduling-to-avoid-eviction), [3.8](docs/report.md#38-a-live-concurrency-limit) |

![Eviction against cache size](docs/figures/eviction-curve.svg)

The mechanisms themselves were reported before: the Qwen3 template boundary
upstream, and cache-aware scheduling for agents in CacheWise and Continuum.
What Cachino adds is exact, per-request, engine-validated attribution from a raw
capture ([related work](docs/report.md#4-related-work)).

## Reading paths

- **Researchers:** [how to read the results](docs/reading-results.md), then report
  [2 Method](docs/report.md#2-method) and [3.1](docs/report.md#31-the-predictions-are-exact),
  then the [run record](docs/records/) behind any number you want to quote.
- **Engineers and reviewers:** the [result card](docs/result-card-487-488.md), then the code path
  [`analysis/reconstruct.py`](analysis/reconstruct.py) → [`analysis/predict.py`](analysis/predict.py)
  → [`analysis/live_attribution.py`](analysis/live_attribution.py), with its tests, then run the
  [examples](examples/README.md). Together they show source-level engine modeling, exact
  tokenization, and checks against a running system.

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

Citation metadata is in [`CITATION.cff`](CITATION.cff); GitHub's *Cite this
repository* button exports it as BibTeX or APA. Cite the commit you used, since
numbers can change between versions.

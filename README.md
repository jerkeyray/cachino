<img src="docs/figures/banner.svg" alt="Cachino: where coding agents lose prefix reuse" width="100%">

A coding agent resends its whole conversation on every turn, and prefix caching
is supposed to skip the part the engine has already seen. When it doesn't, vLLM
reports one number per request: how many prompt tokens were cached. If a
6,000-token prompt comes back with 4,000 cached, that number can't tell you
whether the other 2,000 were new content, a prompt that rendered differently,
or blocks evicted while the agent was away.

Cachino sits between the agent and vLLM and tells those cases apart. It
captures traffic byte for byte, rebuilds the exact prompt token IDs, and
replays a pinned vLLM build's prefix cache, block allocation and free queue
included, to predict each request's hit and attribute every recomputed token
to a cause. Every prediction is checked against the count the engine reports.

**[Read the report](docs/report.md)** · [Result card: 487 vs 488 blocks](docs/result-card-487-488.md) ·
[Reproduce](docs/reproduce.md) · [Released results](results/README.md) ·
[Try it without an engine](examples/README.md) · [Run records](docs/records/) ·
[Write-up](https://www.jerkeyray.com/blog/prefix-caching)

> **Scope.** One frozen task, two harnesses (mini-swe-agent and smolagents),
> Qwen3 at 0.6B and 1.7B, vLLM at `2173d712` on a Mac CPU, timing on one
> Tesla T4. Every number is a dated measurement from 7 and 8 November 2026 and
> describes this setup, not production traffic.

![Illustrative prompt blocks: 8 reused after an append, 5 after a token change, 3 after eviction](docs/figures/reuse-loss.svg)

## Results

| Finding | Evidence | Boundary |
| --- | --- | --- |
| [The predictions are exact](docs/report.md#31-the-predictions-are-exact), and modeling residency is what makes them so | Every sequential request on CPU and T4; 710 of 738 under live concurrency. Assuming any matching prefix is still cached: 8,320 tokens of error against 352 | Overlap misses reach 26 blocks in the most evicting runs; one vLLM commit |
| [A single session mostly recomputes new content](docs/report.md#32-single-sessions-mostly-new-content) | 64% to 91% of recomputation, with no eviction | One task, read-only observations |
| [Qwen3's template re-prefills every reply](docs/report.md#33-the-template-boundary) | History drops the 4-token empty thinking cue; keeping it removed every re-prefill but changed the next action | Thinking off; no session solved the task ([3.9](docs/report.md#39-keeping-the-cue-in-a-sandboxed-task)) |
| [A harness can make replies unreusable](docs/report.md#34-a-second-harness) | smolagents rewrites each tool call with a random ID: 19 to 36 generated tokens come back as 52 to 66 | One version (1.26.0), four sessions |
| [Shared caches evict past a predictable threshold](docs/report.md#35-shared-caches-and-the-eviction-threshold) | 488 blocks lost nothing, 487 lost exactly 32 tokens, as predicted; the capture's threshold called 9 of 9 seeded runs | One trace; exact for sequential replay |
| [Eviction costs time to first token](docs/report.md#36-what-eviction-costs-in-time) | +31% and +86% on a T4 below the threshold | One T4, requests sent one at a time |
| [Limiting concurrency removes eviction, at a price](docs/report.md#38-a-live-concurrency-limit) | A two-session cap removed it in every seed that evicted and slowed the rest; a token budget lost the cache of sessions it paused | Mac CPU timing compares conditions only |

![For this trace, 487 cache blocks lost 32 tokens to eviction; 488 blocks lost none](docs/figures/eviction-curve.svg)

The mechanisms were known before this study: the Qwen3 template boundary was
reported upstream, and CacheWise and Continuum schedule around eviction. What
Cachino adds is exact, per-request, engine-validated attribution from a raw
capture ([related work](docs/report.md#5-related-work)).

## How it works

![Live traffic becomes a trace; offline analysis renders, predicts, attributes and verifies reuse](docs/figures/pipeline.svg)

| Stage | Code | What it guarantees |
| --- | --- | --- |
| Capture | [`cmd/cachino`](cmd/cachino), [`internal/proxy`](internal/proxy), [`internal/trace`](internal/trace) | Request and response bytes forwarded unchanged and stored, synced before the next event |
| Render | [`analysis/reconstruct.py`](analysis/reconstruct.py) | Exact prompt token IDs from a pinned tokenizer and chat template; unknown request fields are rejected |
| Predict | [`analysis/predict.py`](analysis/predict.py) | vLLM's free-queue prefix cache at `2173d712`, rule by rule from source |
| Attribute | [`analysis/live_attribution.py`](analysis/live_attribution.py), [`analysis/concurrent_attribution.py`](analysis/concurrent_attribution.py) | Each recomputed token assigned to one cause; a missing engine metric stays unknown |
| Check | [`analysis/cache_sweep.py`](analysis/cache_sweep.py), [`analysis/overlap_replay.py`](analysis/overlap_replay.py) | The same token IDs replayed to the engine; prompt IDs and cached tokens compared per request |

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

`python3 analysis/report.py traces/demo` verifies and summarizes the capture.
Point `-upstream` at a vLLM server to capture real traffic; flags and the trace
format are in the [proxy reference](docs/proxy.md).

**Without an engine.** `python3 examples/one-block/run.py` finds a one-block
eviction threshold in a synthetic three-session trace, and
`examples/two-turn/run.sh` shows Qwen3's template dropping the thinking cue
where Qwen2.5's stays append-only ([examples](examples/README.md)).

**On your own capture.** `analysis/cachino_analyze.py` runs every analysis
(`attribute`, `concurrent`, `template`, `curve`, `sweep`, `schedule`, `overlap`,
`summary`); commands that render tokens need the tokenizer environment in
[reproduce](docs/reproduce.md#1-environments).

**Check the numbers.** `python3 analysis/validate_results.py` recomputes the
report's headline numbers from the per-request tables in
[`results/`](results/README.md), which hold counts and predictions but no
prompt text.

## Tests

```sh
go build ./... && go vet ./... && go test -race ./...
```

```sh
uv run --project analysis --offline --no-python-downloads python -m unittest discover -s analysis
```

## License and citation

Code, documentation and released results are under the [MIT License](LICENSE).
Citation metadata is in [`CITATION.cff`](CITATION.cff); cite the commit you
used, since numbers can change between versions.

<img src="docs/figures/banner.svg" alt="Cachino: where coding agents lose prefix reuse" width="100%">

A coding agent resends its whole conversation on every turn, so most of what
the serving engine does is prefill over tokens it has already seen. Prefix
caching is supposed to skip that work. When it doesn't, vLLM reports one number
per request: how many prompt tokens were cached. Say a 6,000-token prompt comes
back with 4,000 cached. That number can't tell you whether the other 2,000 were
new content, a prompt that rendered differently, or blocks that were evicted
while the agent was away.

Cachino sits between the agent and vLLM and records what you need to tell those
cases apart. It captures traffic byte for byte, rebuilds the exact prompt token
IDs, and replays a pinned vLLM build's prefix-cache rules, including block
allocation and the free queue, to predict each request's hit. Every prediction
is checked against the count the engine reports.

A missing hit comes down to two questions. **What changed in the token IDs?**
Answering that needs the exact tokens. **What was still in memory when the next
turn arrived?** Answering that needs to know what the engine served in between.
The engine reports the result of both as the same field.

**[Read the report](docs/report.md)** · [How to read the results](docs/reading-results.md) ·
[Result card: 487 vs 488 blocks](docs/result-card-487-488.md) · [Reproduce](docs/reproduce.md) ·
[Released results](results/README.md) · [Try it without an engine](examples/README.md) ·
[Run records](docs/records/)

> **Scope.** One frozen task, two harnesses (mini-swe-agent and smolagents),
> Qwen3 at 0.6B and 1.7B, and vLLM at `2173d712`, run on a Mac CPU with timing
> on one Tesla T4. Every number below is a dated measurement from runs on 7 and
> 8 November 2026. Read [how to read the results](docs/reading-results.md)
> before quoting one.

![Three ways an agent turn loses prefix reuse](docs/figures/reuse-loss.svg)

## Explaining an uncached count

| What happened | What it looks like | Where to look |
| --- | --- | --- |
| The turn added new context | Recomputation sits after the longest prefix shared with any earlier prompt or reply | The latest messages and tool output; `attribute` reports it as new content |
| The match ended partway through a block | Up to 31 tokens short of the shared prefix; a fully cached prompt still recomputes its final token, and with it the last block | Block size and the final prompt position; `attribute` reports block rounding |
| The conversation rendered differently | The first changed token ID sits before content that matches word for word, often right before the last reply | `template` finds the boundary from a capture; `attribute` reports a re-prefilled reply |
| Matching KV was evicted | Token IDs match through a block that the engine reports as uncached | What else used the cache between requests; `concurrent`, `curve` and `sweep` |

Work through the rows in order. The first three come from the tokens alone,
and the fourth needs a model of what the cache held.

## Results

1. **A single session mostly recomputes genuinely new content.** Without
   eviction, 64% to 91% of the recomputed tokens were new context. The rest was
   block rounding and, with Qwen3, the previous reply.
2. **Qwen3's chat template drops a 4-token cue before every reply.** With
   thinking off, the generation prompt ends with an empty `<think>` block that
   history omits. So each turn re-prefills the reply the model just generated.
   That averaged about 16 to 77 tokens a turn, depending on the session.
3. **A harness can make replies unreusable.** smolagents writes each tool call
   back into history as different text with a random call ID. A call generated
   in 19 to 36 tokens comes back as 52 to 66.
4. **Shared caches evict past a threshold the capture predicts.** On one
   three-session trace, 488 blocks lost nothing to eviction. 487 blocks lost
   exactly 32 tokens, as predicted, and vLLM matched all 30 requests at both
   sizes ([result card](docs/result-card-487-488.md)).
5. **Eviction costs time to first token.** Replaying that trace's 30 prompts on
   a T4, total time to first token rose from 23.6 s to 30.9 s at 438 blocks
   (+31%) and 43.9 s at 384 blocks (+86%).
6. **Limiting concurrent sessions removes eviction, at a price.** A two-session
   cap removed eviction in every seed that evicted, and it slowed runs that
   would not have evicted. A size-based budget avoided needless waits, but the
   sessions it paused lost their cache.
7. **The predictions are exact, and modeling residency is what makes them
   exact.** The predictor matched every sequential replay on CPU and T4, and
   710 of 738 requests under live concurrency. A shortcut that assumes any
   matching prefix is still cached had 8,320 tokens of total error against 352.

| # | Evidence | Boundary | Report |
| --- | --- | --- | --- |
| 1 | Three single sessions, no eviction: 68%, 64% and 91% new content | One task, read-only observations | [3.2](docs/report.md#32-single-sessions-mostly-new-content) |
| 2 | Every Qwen3 transition diverged inside the cue. With the cue kept, no reply was re-prefilled, but the next action changed | Qwen3 with thinking off; no session solved the task, so the effect on success is unknown | [3.3](docs/report.md#33-the-template-boundary), [3.9](docs/report.md#39-keeping-the-cue-in-a-sandboxed-task) |
| 3 | Measured on every smolagents tool call | One harness version (1.26.0), four sessions | [3.4](docs/report.md#34-a-second-harness) |
| 4 | Eviction took 17% to 25% of recomputation in runs that outgrew the cache. The capture threshold called 9 of 9 seeded runs | Sessions from one task. The threshold is exact for sequential replay | [3.5](docs/report.md#35-shared-caches-and-the-eviction-threshold) |
| 5 | Two rounds per size, capture off | One T4, one trace, requests sent one at a time | [3.6](docs/report.md#36-what-eviction-costs-in-time) |
| 6 | Replay: -16.5% recomputation. Live: five seeds, cap off, cap 2 and token budget | Live timing on a Mac CPU compares conditions only. Sampled replies differ between arms | [3.7](docs/report.md#37-scheduling-to-avoid-eviction), [3.8](docs/report.md#38-a-live-concurrency-limit) |
| 7 | Engine prompt IDs equalled the local render on every replayed request | Under overlap, misses reach 26 blocks in the most evicting runs. The likely cause is decode-block timing, which is a hypothesis. One vLLM commit | [3.1](docs/report.md#31-the-predictions-are-exact) |

![Eviction against cache size](docs/figures/eviction-curve.svg)

The mechanisms themselves were known before this study: the Qwen3 template
boundary was reported upstream, and CacheWise and Continuum address
cache-aware scheduling for agents. What Cachino adds is exact, per-request,
engine-validated attribution from a raw capture ([what is new here](docs/contribution.md)).

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

## Check the numbers

The per-request rows behind the report are released as CSV in
[`results/`](results/README.md), with token counts, hits and predictions but no
prompt text. One command recomputes the report's headline numbers from them:

```sh
python3 analysis/validate_results.py
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
conditions only. Full list: [limitations](docs/report.md#6-limitations).

## Repository

| Path | Contents |
| --- | --- |
| [`cmd/`](cmd), [`internal/`](internal) | Proxy, capture, session limits, fake upstream and demo (Go) |
| [`analysis/`](analysis) | Rendering, cache predictor, attribution, replays and tests (Python) |
| [`experiments/`](experiments) | Calibration, eviction probes, agent sessions, proxy overhead, GPU confirmation, figures |
| [`results/`](results/README.md) | Released per-request tables, checksums and data dictionary |
| [`examples/`](examples/README.md) | Synthetic checks a fresh checkout can run |
| [`docs/`](docs/README.md) | Report, reproduction guide, proxy reference and run records |

## Tests

```sh
go build ./... && go vet ./... && go test -race ./...
```

```sh
uv run --project analysis --offline --no-python-downloads python -m unittest discover -s analysis
```

## License

Code, documentation and released results are under the [MIT License](LICENSE).

## Citing

Citation metadata is in [`CITATION.cff`](CITATION.cff); GitHub's *Cite this
repository* button exports it as BibTeX or APA. Cite the commit you used, since
numbers can change between versions.

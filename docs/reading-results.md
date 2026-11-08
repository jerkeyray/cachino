# How to read the results

[← Documentation](README.md) · [Report](report.md) · [Result card](result-card-487-488.md)

Read this before quoting a number from the [report](report.md). Every result is
a dated measurement from runs on 7 and 8 November 2026, under the conditions
below. None is a production benchmark.

## Units

| Term | Meaning |
| --- | --- |
| Token | One prompt token ID, from the pinned tokenizer and chat template of the model named in the run |
| Block | 32 tokens of KV cache. A cache of *N* blocks holds *N* − 1 usable blocks; block 0 is vLLM's placeholder |
| Cached tokens | vLLM's `usage.prompt_tokens_details.cached_tokens` for one request: prompt tokens served from the prefix cache |
| Recomputed tokens | Prompt tokens minus cached tokens |
| Lost to eviction | Reuse below the request's own-session bound: tokens that matched an earlier prompt of the same session, in whole blocks, but were not served |
| Cross-session reuse | Reuse above that bound, served from another session's blocks |
| Time to first token | vLLM's per-request `time_to_first_token_ms`, measured by the engine from scheduling, not by the proxy |
| Wall clock | Elapsed time for a whole live run of all its sessions, from the harness |
| Delay (positions) | How many places later than recorded a request ran in a reordered replay; not a time |

A request's hit can never exceed its prompt length minus 1, because vLLM always
computes the final prompt token.

## Protocols

| Protocol | What happens | What it cannot show |
| --- | --- | --- |
| Sequential replay | Captured prompts sent one at a time, after a cache reset, with `max_tokens` 1 | Overlap between requests and blocks held by generated tokens |
| Timed overlap replay | Prompts sent at their recorded offsets, with recorded reply lengths | When the engine allocates decode blocks; the residual error is attributed to that, as a hypothesis |
| Reordered replay | One trace sent in a different order, each session keeping its turn order | Effects on a live agent, whose replies would change with the order |
| Live run | Agents drive the engine through the proxy; replies are sampled | Paired comparisons: changing anything changes the sampled trajectories |

Sequential and reordered replays served Qwen3-0.6B for Qwen3-1.7B captures. The
two models have byte-identical tokenizer files and the same KV size per token,
so cache accounting depends only on the token IDs; timing from those replays
describes the 0.6B model.

## Evidence levels

| Level | Meaning | Results |
| --- | --- | --- |
| Engine-checked | The prediction was compared with vLLM's reported cached tokens and prompt IDs, request by request | Exactness (3.1), eviction threshold (3.5), scheduling orders (3.7), template boundary (3.3, 3.9) |
| Recalculable | Anyone can recompute it from files in this repository: `python3 analysis/validate_results.py` rebuilds it from the [released tables](../results/README.md) | Exactness counts and the baseline (3.1), single-session shares (3.2), the threshold and 487/488 (3.5), T4 totals (3.6), scheduling orders (3.7), session limits (3.8); the [examples](../examples/README.md) |
| Documented | Measured from private captures; the [run record](records/) gives conditions and numbers, but the capture is needed to check them | Template-check transition counts (3.3), smolagents serialization (3.4), sandboxed sessions (3.9), proxy overhead (3.10), and that any run happened as recorded |

A result can be both engine-checked and recalculable: the engine checked the
prediction when the run happened, and the released rows let anyone redo the
arithmetic. Raw captures stay private because they hold prompt and reply text.

## Comparison limits

- **One task.** All sessions work on the same frozen issue in one repository,
  so shares such as "64% to 91% new content" describe this setup.
- **Small models.** 0.6B and 1.7B parameters. The 1.7B model never solved the
  task, so no result measures an effect on task success.
- **Paired live runs differ.** Sampled replies depend on interleaving, so two
  runs under different limits make different requests. Compare eviction, which
  is defined per request, rather than totals.
- **Timing.** GPU latency comes only from the T4, with requests sent one at a
  time. Mac CPU timings compare conditions within a run family only.
- **The 1.34 ms per recomputed token** is a linear fit over two rounds of one
  trace; only the printed summary was retained. The per-size totals in 3.6 are
  the measured quantities.
- **Adjacent-request prefix length** is a structural diagnostic, not a model of
  what the cache holds. Hit predictions come from the free-queue predictor.

## Missing and failed observations

- A cache metric the engine did not report is recorded as unknown, never as
  zero, and totals that need it are unknown too.
- Matching token counts are never taken as matching tokens; parity checks
  compare the IDs themselves.
- Failures stay in the record: the 10,240-token cache that could not start, a
  session rejected at the 12,288-token context in seed 4, the first token-budget
  version that evicted as much as no limit, and every request the predictor
  missed.

The choices behind these definitions, and how each was checked, are in
[assumptions](assumptions.md).

## Before quoting a number

State the model, the protocol, the engine (Mac CPU or T4), the cache size and
the run, which the [records](records/) give for every result.

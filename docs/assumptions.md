# Assumptions

[← Documentation](README.md) · [How to read the results](reading-results.md)

Every place where the method had to choose because the engine, the harness or
the data did not settle the question. Each entry says what the choice was, why
it matters, and how it was checked. Where a choice was revised after seeing a
result, the entry says so.

## Engine model

**1. The predictor follows vLLM's free-queue rules at `2173d712`, read from source.**
Why it matters: every hit prediction depends on it, and the rules change between
versions. Check: calibration and capped-cache probes (17 of 17, 27 of 27), then
every sequential request on CPU and T4. Source lines are in the
[eviction plan](records/e4-eviction-plan.md). Other vLLM versions are not covered.

**2. Under overlap, a request holds its blocks from its proxy start time to its
proxy end time.** Proxy timestamps are not engine scheduling times. Why it
matters: it decides which blocks are free when a later request arrives. Check:
the overlap predictor was exact on 259 of 265 concurrent requests; the misses are
reported, not corrected.

**3. Decode blocks are taken at admission.** The engine allocates them as it
decodes, between admission and completion. Why it matters: blocks held by a
reply can force eviction. Check: a timed overlap replay scored three variants,
at admission 93 of 98 exact, at completion 92, ignoring decode 88 (3.1). The
residual is attributed to decode-block timing as a hypothesis; confirming it
needs engine-side allocation events.

**4. A request whose completion count is missing is modeled as generating one
token.** Why it matters: it would understate the blocks the reply holds. Check:
no request in the [released tables](../results/README.md) is missing its
completion count, so none of their results depends on this default.

## Tokens

**5. The engine's prompt is the local render from a pinned tokenizer and
template.** Why it matters: every prefix comparison uses these IDs. Check: the
engine's `prompt_token_ids` equalled the local render on every replayed request,
and the engine's prompt length equalled the local one on every live request in
[`results/`](../results/README.md). Equal counts alone were never taken as
equal tokens.

**6. A reply's size in history is its `completion_tokens`.** This holds only if
re-tokenizing the reply text gives back the generated IDs. Why it matters: it
sets the "previous reply" share in 3.2. Check: true wherever it was checked;
with `--reply-ids`, re-tokenized text is used only when its length matches the
engine's count.

**7. Replays of Qwen3-1.7B captures served Qwen3-0.6B.** Why it matters: cache
accounting must be the same for both. Check: the two models have byte-identical
tokenizer files and the same KV size per token (114,688 bytes), and prompt-ID
parity held on every replayed request. Timing from those replays describes the
0.6B model.

## Definitions

**8. Eviction is reuse lost below the request's own-session bound.** The bound
is the longest prefix shared with an earlier prompt of the same session, in
whole blocks, capped at prompt length minus 1. Why it matters: a different
bound would move tokens between eviction and the other causes. Reuse above the
bound is counted as cross-session reuse, not subtracted from eviction.

**9. Block rounding includes the final prompt token**, which vLLM never serves
from cache, as well as the partly shared block. Why it matters: it is a fixed
per-request cost that would otherwise be counted as new content.

**10. The T4 cost per recomputed token is a straight-line fit** of time to first
token against recomputed tokens over 240 requests. Why it matters: the slope
is quoted in 3.6. The per-size totals are the measured quantities; the slope is
secondary, and only the printed summary of that run was kept.

## Runs

**11. Independent live runs use `--seed`.** vLLM samples with seed 0 by default,
so runs without it would repeat each other. Runs with the same seed and
settings reproduced exactly (3.8).

**12. Cache size is set in whole blocks with `--kv-cache-memory-bytes`** and the
block count is read back from the engine log rather than assumed. One requested
size, 10,240 tokens, could not start: vLLM needs room for one maximum-length
request plus its placeholder block. It is recorded as a failure.

**13. The token budget estimates a session's footprint at 0.25 tokens per
request byte,** measured on these captures, and a session stays active for 5 s
after its last request (the proxy's defaults). Why it matters: both decide when
a session is paused. The first budget version let an older turn start beside
younger requests in flight and evicted as much as no limit on seed 2; the second
version, chosen after seeing that result, makes such a turn wait. Both versions
are reported.

**14. Thinking is off** (`enable_thinking: false` in the default chat template
arguments) for every Qwen3 run. Why it matters: it is what puts the empty cue
in the generation prompt. The cue-kept runs served a modified copy of the
pinned template, made by `experiments/agent-task/make_keep_cue_template.py`.

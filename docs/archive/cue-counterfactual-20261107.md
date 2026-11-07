# Counterfactual: keeping the thinking cue in assistant history, 2026-11-07

This tests the hypothesis from the [live session results](live-cpu-results-20261107.md):
the reply just generated is prefilled again on the next turn because the Qwen3
template drops `<think>\n\n</think>\n\n` from earlier assistant messages. If
history kept the cue, would the next prompt continue the generation context and
reuse the reply's KV?

## Method

`experiments/agent-task/cue_counterfactual.py` replays the 8 captured prompts of
`live-cpu-20261107-01` in two arms on the same CPU engine and configuration as
the live run (2 GiB KV, 585 blocks of 32). Each arm starts with a cache reset.

- **normal:** each prompt rendered exactly as the pinned template does.
- **cue-kept:** the same messages, with the cue kept before every history reply.

Both are rendered by hand. The script first checks that the hand render equals
the pinned template's text and token IDs on all 8 prompts, and stops otherwise.
Prompts are sent as token IDs to `/v1/completions` with greedy decoding and up
to 768 tokens, so the engine generates each reply and holds its KV, as it would
live. History replies are always the captured text. The free-queue predictor is
given the actual generated IDs, which assumes the engine caches full blocks of
generated tokens like prompt blocks.

## Findings

| Turn | Normal prompt | Normal reused | Normal recomputed | Cue-kept prompt | Cue-kept reused | Cue-kept recomputed |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 860 | 0 | 860 | 860 | 0 | 860 |
| 1 | 883 | 832 | 51 | 883 | 832 | 51 |
| 2 | 968 | 864 | 104 | 972 | 928 | 44 |
| 3 | 1053 | 960 | 93 | 1061 | 960 | 101 |
| 4 | 1138 | 1024 | 114 | 1150 | 1088 | 62 |
| 5 | 1223 | 1120 | 103 | 1239 | 1184 | 55 |
| 6 | 1308 | 1216 | 92 | 1328 | 1280 | 48 |
| 7 | 1393 | 1280 | 113 | 1417 | 1376 | 41 |
| Total | 8,826 | 7,296 | 1,530 | 8,910 | 7,648 | 1,262 |

1. **The predictor matched all 16 requests**, with generated blocks treated as
   cacheable. So the engine does cache full blocks of generated tokens and serves
   them to a later prompt that continues the same token sequence. This was not
   visible in the live run, where no prompt continued a generation context.
2. **Token identity held.** On turns 2 and 4 to 7, each cue-kept prompt
   continues the previous prompt plus the actually generated IDs exactly, for the
   whole generation context (for example 1,377 of 1,377 tokens on turn 7).
   Re-tokenizing these replies reproduced the generated IDs.
3. **Prompt IDs matched.** Engine prompt IDs equalled the hand render on all 16
   requests, and the normal arm reproduced the live session's hit counts and
   replies exactly.
4. **Recomputation fell.** On turns 4 to 7, recomputed tokens dropped from 422 to
   206 (about 51%). Over turns 1 to 7, they dropped from 670 to 362 (about 46%).
   What remains per turn is the new observation and block alignment.
5. **Turn 1 is unchanged** because the rejected turn-0 reply was never in history.
6. **Turn 3 got worse** because of a behavior change, not a cache effect. On
   turn 2 the cue-kept prompt led the model to a different reply (19 tokens:
   the submit command) instead of the captured `ls -la` reply. The history still
   holds the captured reply, so turn 3's prompt departs from that generation
   context after 972 of 991 tokens.

## What this does and does not show

- It shows the mechanism: with the cue kept, the next prompt can extend the
  previous generation context token for token, and vLLM reuses the reply's KV.
- **It changes what the model sees, and the model's behavior changed.** In a real
  session, turn 2's different reply would have ended the episode. Any claim that
  this intervention helps an agent needs task-level comparison under paired
  conditions, not only reuse counts.
- Token identity held for these 49-token replies. It is not guaranteed in
  general, and it needs measuring on longer and more varied replies.
- One session, a 0.6B model, fixed observations and one action, CPU timing only.
  The savings are measured in tokens, not latency.
- `/v1/completions` reported `created_cache_tokens` as null at this build, so it
  is unknown here.

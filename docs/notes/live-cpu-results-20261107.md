# Live agent session on the CPU engine, 2026-11-07

Run `live-cpu-20261107-01`. The pinned mini-swe-agent loop (`04d809ce`, text-based
templates from `mini_textbased.yaml`) ran the frozen `split_after` task against
Qwen3-0.6B on the vLLM CPU backend, through Suez. The environment recorded each
action and returned one fixed observation, so no command was executed. Raw
capture and attribution are private (`artifacts/live-cpu-20261107-01/`); this is
the tracked summary.

## Setup

- Engine: the same build, model and flags as the [E4 run](e4-cpu-results-20261107.md),
  with `--max-model-len 8192` and a 2 GiB KV cache (logged 18,720 tokens = 585
  blocks). A 4 GiB cache did not fit in free memory. The cache was reset before
  the session.
- Agent: `max_tokens` 768, temperature 0, step limit 8. The instance template's
  uname fields were set to stated Linux task-container values, not read from the
  host.
- Driver `experiments/agent-task/live_cpu.py` and spec `live-cpu.json`, with the
  same pinned-checkout check, locked 13-package environment and loopback-only
  network guard as the wire check. Analysis: `live_attribution.py`.

The session ended at its step limit after 8 model calls. Suez captured 8 requests,
all HTTP 200 with request and response integrity verified. No network attempt
was blocked. The model chose `ls -la` on every turn. This run is about the
structure of the traffic, not the task.

## Findings

| Turn | Messages | Prompt tokens | Shared with earlier prompt | Prompt bound | Predicted | Engine reused | Recomputed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2 | 860 | 0 | 0 | 0 | 0 | 860 |
| 1 | 3 | 883 | 854 | 832 | 832 | 832 | 51 |
| 2 | 5 | 968 | 879 | 864 | 864 | 864 | 104 |
| 3 | 7 | 1053 | 964 | 960 | 960 | 960 | 93 |
| 4 | 9 | 1138 | 1049 | 1024 | 1024 | 1024 | 114 |
| 5 | 11 | 1223 | 1134 | 1120 | 1120 | 1120 | 103 |
| 6 | 13 | 1308 | 1219 | 1216 | 1216 | 1216 | 92 |
| 7 | 15 | 1393 | 1304 | 1280 | 1280 | 1280 | 113 |

Over the session: 8,826 prompt tokens, 7,296 reused, 1,530 recomputed.

1. **The predictor matched every live turn.** Engine `cached_tokens` equalled the
   free-queue prediction on all 8 requests. No eviction occurred: the prompt bound
   and the prediction are equal on every turn.
2. **No generated KV was reused.** In no turn did the engine reuse more than the
   shared prompt prefix allowed. The KV computed while generating each reply was
   never hit by the next prompt.
3. **The divergence point is the thinking cue.** On turns 2 to 7, the previous
   prompt continues with the template's generation cue `<think>\n\n</think>\n\n`,
   while the next prompt continues with the reply text `THOUGHT: I need…`. The
   Qwen3 template renders the cue when generating but omits it from assistant
   history. So the reply the model just generated (49 tokens each turn) is
   prefilled again as part of the 89 tokens after the divergence.
4. **Block alignment adds the rest.** The shared prefix is rounded down to 32-token
   blocks, costing 3 to 25 tokens per turn (the difference between the shared and
   bound columns).
5. **A rejected reply leaves no history.** Turn 0's reply failed the harness's
   format check. The harness dropped it and appended a format-error user message,
   so turn 1 diverges right after the `<|im_start|>` role marker, where the old
   prompt had `assistant` and the new one has `user`.
6. **Replay confirms prompt parity.** All 8 captured prompts were resent after a
   reset with `return_token_ids` and `max_tokens` 1. Engine prompt IDs equalled the
   local render on all 8, and `cached_tokens` and `created_cache_tokens` matched
   the prompt-only prediction exactly.

## Hypothesis, not yet tested

If assistant history kept the generation cue, the next prompt might extend the
cached generation context, and each reply's KV could be reused. That requires the
re-rendered reply to tokenize to exactly the generated IDs, which is not
guaranteed. On this session it would save about 49 tokens of prefill per turn
out of roughly 100 recomputed. This is a candidate intervention to test with a
counterfactual render, not a finding.

## Limits

- One short session (8 turns, prompts under 1.4k tokens) with a weak model, fixed
  observations and the same action every turn. Real sessions have longer, varied
  observations, which change the size of each turn's new content but not the
  boundary.
- On live requests, `created_cache_tokens` counted only prompt blocks: for
  example, 832 for turn 0's 860-token prompt, not the 896 the predictor counted
  including reply blocks. Whether full blocks of generated tokens are cached at
  all is not visible in that field. It does not affect the hit counts, which
  matched.
- The live requests did not ask for `return_token_ids`; ID parity comes from the
  replay of the same prompts.
- The driver and spec were not yet committed when the session ran. The recorded
  `suez_commit` (`cbb844d0`) is the parent of the commit that adds them.
- CPU timing only.

# 30-turn agent session with read-only observations, 2026-11-07

Run `live-cpu-readonly-20261107-01`. Same harness, model and engine as the
[first live session](live-cpu-results-20261107.md), with three changes:

- Observations come from `experiments/agent-task/readonly_env.py`, which answers
  read-only commands from the task's baseline tree (`6793bd3e`, read from git
  objects; 33 files, blobs checked against the frozen manifest). Nothing is
  executed, and edits are refused.
- Step limit 30, `--max-model-len 16384`, same 2 GiB cache (585 blocks of 32).
- Spec `experiments/agent-task/live-cpu-readonly.json`. Attribution splits each
  turn's recomputed tokens by cause (`live_attribution.py`).

## Result

The session ran 30 model calls and stopped at its step limit. All 30 captures
were HTTP 200 with request and response integrity verified.

**The model never varied its action.** Every one of its 29 parsed actions was
`ls -la`, which the emulator answered with the real 13-entry listing. After
turn 2, each reply was the bare 14-token command block. So observations were
real but identical every turn. This limits what the session shows; it is a
property of the 0.6B model under greedy decoding, not of the environment.

## Findings

1. **The predictor matched all 30 live turns**, and the replay (after reset,
   `return_token_ids`, `max_tokens` 1) matched on all 30 with exact prompt-ID
   parity. No eviction occurred.
2. **The cache served 95% of prompt tokens.** Over turns 1 to 29, prompts
   totalled 59,536 tokens, of which 56,640 were reused and 2,896 recomputed.
3. **Recomputed tokens split by cause:**

   | Cause | Tokens (turns 1-29) | Share |
   | --- | --- | --- |
   | New content (observation, message wrappers, new cue) | 1,961 | 68% |
   | Previous reply prefilled again (thinking-cue boundary) | 462 | 16% |
   | Block rounding, including the final prompt token | 473 | 16% |
   | Eviction | 0 | 0% |

4. **The avoidable part is bounded.** For an append-only harness with no
   eviction, the per-turn waste beyond new content is the previous reply plus up
   to one block. Here that is 32% of recomputed tokens, but under 2% of all
   prompt tokens, because the cache already serves the long shared prefix.

## Limits

- The model repeated one action, so observations did not vary. Turn sizes were
  small (69 new tokens per turn) and replies were short (14 tokens). Longer
  replies would raise the reply share; larger observations would lower it.
- No eviction: the whole session fit in the cache. Long sessions, larger
  observations or concurrent sessions under a capped cache are where eviction
  would appear.
- One session, one harness, one model, CPU token counts only.

## Sampled rerun

Run `live-cpu-sampled-20261107-01`, spec `live-cpu-sampled.json`: identical
except temperature 0.7, with no seed sent, so it is not reproducible beyond its
capture. The aim was varied actions. It did not achieve that: 27 of 28 parsed
actions were `ls -la`, from the second turn on prefixed with `cd /task &&`.
Once the history holds identical actions, this model copies them.

The measurements repeat the greedy run. The predictor matched all 30 live
turns, and the replay matched all 30 with exact prompt-ID parity. Over turns 1
to 29, 56,448 of 59,419 prompt tokens were reused. The 2,971 recomputed tokens
split into 1,916 new content (64%), 558 previous reply (19%), 497 block rounding
(17%) and no eviction. Reply lengths ranged from 18 to 108 tokens.

Varied actions therefore need a more capable model than Qwen3-0.6B, not
sampling.

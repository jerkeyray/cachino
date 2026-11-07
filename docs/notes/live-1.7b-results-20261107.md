# Varied agent session with Qwen3-1.7B, 2026-11-07

Run `live-cpu-1.7b-20261107-02`. Same harness and read-only snapshot
environment as the [30-turn session](live-readonly-results-20261107.md), but
with Qwen3-1.7B, because Qwen3-0.6B repeated one action with greedy and with
sampled decoding.

## Setup

- Model `Qwen/Qwen3-1.7B` at `70d244cc`, FP16. Both weight files matched the
  Hugging Face SHA-256 values. Its tokenizer and chat template files are
  byte-identical to the earlier pins (`experiments/tokenizer/qwen3-1.7b.json`).
- vLLM CPU build at `2173d712`, `--max-model-len 12288`, 1.5 GiB KV cache
  (logged 14,016 tokens = 438 blocks of 32). Greedy decoding, step limit 30.
- Spec `experiments/agent-task/live-cpu-1.7b.json`. Observations come from the
  read-only emulator over the task's baseline tree. Long outputs are truncated
  by the pinned harness's observation template.

**A first run (`-01`) is not used.** The direct model adapter then used its
minimal wire-check template, which does not truncate. A full `cat` of
`more.py` put 133,381 characters into history and the fifth request exceeded
the context. The adapter now takes the pinned template, which renders that
file as 10,614 characters, as the stock harness does.

## Result

The agent explored. Its actions were `ls -la`, a `grep` for `split_after`, a
`find | xargs grep`, then alternating `cat more_itertools/more.py` and a
`sed -i` edit. The emulator refuses edits, so the agent kept re-reading the file.
The 11th request exceeded the 12,288-token context, and the session ended. The
10 completed requests were HTTP 200 with integrity verified.

## Findings

1. **The predictor matched all 10 live turns**, and the replay (after reset,
   `return_token_ids`, `max_tokens` 1) matched all 10 with exact prompt-ID
   parity. No eviction occurred: the session hit the context limit before
   exhausting the cache.
2. **Over turns 1 to 9, the cache served 79% of prompt tokens** (38,144 of
   48,212).
3. **Recomputed tokens split by cause:**

   | Cause | Tokens (turns 1-9) | Share |
   | --- | --- | --- |
   | New content | 9,170 | 91% |
   | Previous reply prefilled again | 690 | 7% |
   | Block rounding | 208 | 2% |
   | Eviction | 0 | 0% |

4. **The avoidable share depends on observation size.** On the turns after a
   file read, about 2,927 new tokens dominate, and the re-prefilled reply (55 to
   97 tokens) is 2% of the turn. On the turns after a refused edit, new content
   is 64 tokens, and the reply is about half of what is recomputed.

## Across sessions so far

| Session | Reused | Recomputed: new content / reply / rounding |
| --- | --- | --- |
| 0.6B, 30 turns, greedy | 95% | 68% / 16% / 16% |
| 0.6B, 30 turns, sampled | 95% | 64% / 19% / 17% |
| 1.7B, 10 turns, varied | 79% | 91% / 7% / 2% |

For sequential, append-only sessions that fit in the cache, nearly all
recomputation is new content. The thinking-cue boundary adds roughly one reply
per turn, and block rounding adds up to one block. The waste is noticeable only
when observations are small.

## Limits

- One 10-turn session. The agent's actions repeated after turn 4 because edits
  were refused; real execution would change the trajectory.
- No eviction and no concurrency: both need sessions or traffic larger than
  this cache.
- CPU token counts only.

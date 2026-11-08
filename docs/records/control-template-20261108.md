# Control template without the thinking cue, 2026-11-08

Qwen3's
template adds an empty `<think></think>` cue to the generation prompt but
leaves it out of assistant history, so each turn diverges from the previous
generation context ([live session](live-cpu-results-20261107.md),
[cue counterfactual](cue-counterfactual-20261107.md)). This control uses a
template that renders history exactly like the generation prompt.

## Setup

- Model `Qwen/Qwen2.5-0.5B-Instruct` at `7ae55760`, not gated. The weight
  file's SHA-256 (`fdf756fa…b7fe`) matched Hugging Face. Its tokenizer files
  differ from Qwen3's, so it has its own pin
  (`experiments/tokenizer/qwen2.5-0.5b-instruct.json`) and tokenizer snapshot.
- Its template renders the generation prompt as `<|im_start|>assistant\n` and
  each history reply as `<|im_start|>assistant\n` + reply + `<|im_end|>\n`, with
  no cue.
- Same harness, read-only snapshot environment and vLLM CPU build as before.
  1 GiB cache (87,360 tokens), `--max-model-len 16384`, greedy decoding, step
  limit 10. Spec `experiments/agent-task/live-cpu-control.json`.

## Live session

Run `live-cpu-control-20261108-01`. The model was a poor agent: it ended with
the harness's repeated-format-error exit after 5 calls, and its only accepted
action was the template's placeholder `your_command_here`. The 5 captures were
HTTP 200 with integrity verified.

1. **The predictor matched all 5 live turns**, and the replay matched all 5 with
   exact prompt-ID parity.
2. **With a reply kept, the next prompt contains the whole previous prompt.** On
   turn 2, the shared prefix with turn 1 is 879 of 879 tokens. Under Qwen3 it
   stopped 4 tokens short, at the cue. Regenerating turn 1 reproduced the
   captured reply, and turn 2 continues turn 1's prompt with exactly those
   generated IDs.
3. **That reply was 13 tokens, shorter than one block,** so its KV sat in an
   uncached partial block. On this turn the attribution's "previous reply"
   bucket is block rounding, not a template effect.
4. **Rejected replies cause divergence regardless of template.** On turns 1, 3
   and 4 the harness dropped a reply that failed its format check and appended
   a format-error message. Each prompt diverges at the role marker, as it did
   under Qwen3.

## Reply reuse check

To test reuse with a reply longer than a block, the turn-0 prompt was resent
after a cache reset with greedy decoding. It reproduced the captured 442-token
reply exactly. A next prompt was then built with that reply in history,
followed by a short observation (`reply-reuse-check.json` in the run directory).

| Measure | Value |
| --- | --- |
| Generation context (prompt plus reply, excluding the stop token) | 1,297 tokens |
| Shared prefix of the next prompt with that context | 1,297 of 1,297 |
| Engine `cached_tokens` on the next prompt (1,323 tokens) | **1,280** |
| Bound if only prompt tokens could be reused | 832 |
| Bound with the reply's KV reusable | 1,280 |

The engine reused the reply's KV through to the last full block, and prompt-ID
parity held. With Qwen3's default template, that same reply would have been
prefilled again.

## Conclusion

The reply re-prefill measured in the Qwen3 sessions is caused by the template,
not by vLLM or the harness. With a template that renders history the same way
it generates, the engine reuses each reply's KV across turns. The losses that
remain are template-independent: block rounding, and rejected replies the
harness drops.

## Limits

- One session with a weak model, and one constructed reply-reuse check.
- The reuse check used a constructed observation, not an emulated one.
- CPU token counts only.

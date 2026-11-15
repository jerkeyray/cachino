# Keeping the cue in a sandboxed task, 2026-11-08

Question: with commands really executed, does keeping Qwen3's thinking cue in
history remove the re-prefilled reply, and does it change task success?
Summarized in [report 3.9](../report.md#39-keeping-the-cue-in-a-sandboxed-task).

The [cue counterfactual](cue-counterfactual-20261107.md) changed the template
after the fact. Here the engine served a cue-kept copy of the pinned Qwen3
template (`--chat-template`, made by
`experiments/agent-task/make_keep_cue_template.py`) and was compared with the
default template, over three engine seeds each (run `exec-matrix-20261108`).
mini-swe-agent drove Qwen3-1.7B at temperature 0.7 for up to 20 steps, and
its commands really ran: under macOS `sandbox-exec`, in a throwaway copy of the
task with no network and no writes outside the copy. The frozen evaluator then
scored the copy.

| Template | Seed | Requests | Prompt tokens | Reused | Reply re-prefilled | New content |
| --- | --- | --- | --- | --- | --- | --- |
| Default | 1 | 20 | 60,715 | 91.2% | 1,532 | 3,556 |
| Default | 2 | 20 | 71,946 | 90.8% | 919 | 5,381 |
| Default | 3 | 20 | 109,014 | 92.3% | 1,967 | 6,068 |
| Cue kept | 1 | 20 | 82,227 | 91.5% | 0 | 6,820 |
| Cue kept | 2 | 20 | 116,375 | 93.8% | 0 | 6,906 |
| Cue kept | 3 | 20 | 39,413 | 94.9% | 0 | 1,718 |

- With the cue kept, no reply was ever re-prefilled. Each next prompt continued
  the previous generation token for token, and vLLM served the reply from the
  KV it wrote while decoding.
- That reuse is invisible to the default predictor, which gives generated tokens
  unique IDs: it was exact on only 4 of the 60 cue-kept requests, always below
  the engine. With `--reply-ids`, which models each reply as its re-tokenized
  text, it was exact on all 120 requests in both arms.
- No session solved the task, or edited any file. All six used their 20 steps
  reading the repository, and each copy scored 15 of 21, as the unedited
  baseline does. Task success therefore could not separate the templates.

Sampled trajectories differ between arms, so totals differ; the re-prefilled
reply is the comparable measure. In the sandbox, `ls -la` at the task root
exits with status 1, because the profile denies reading the parent directory;
its listing is otherwise complete.

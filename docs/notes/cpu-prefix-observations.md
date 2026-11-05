# CPU tokenizer and prefix observations

Observed 2026-10-27 on the Mac, without inference or GPU use. These are synthetic
prompt-structure checks, not findings about real agent workloads or cache hits.

## Pinned local reconstruction

- Model source: Qwen/Qwen3-8B, revision
  b968826d9c46dd6066d109eabc6255188de91218.
- Template SHA-256:
  a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8.
- Transformers 4.57.6, tokenizers 0.22.2, Jinja2 3.1.6,
  huggingface-hub 0.36.2. Full package resolution is in analysis/tokenizer/uv.lock.
- Default profile: add_generation_prompt=true, enable_thinking=false.
- All files in the tokenizer snapshot passed the recorded asset hashes.
  Direct template token IDs, text encoding without extra special tokens and
  decoded text were checked for agreement.

The user completed the tokenizer setup from a normal network-enabled terminal.
The missing PyTorch/TensorFlow/Flax warning is expected: this environment uses
only the tokenizer, configuration and data utilities.

## Capture-to-token milestone

Source evidence: artifacts/tokenizer-20261027T174615Z/reconstructed/summary.json
and its immutable capture/body sidecars. Four requests rendered successfully;
the deliberately invalid JSON request remained an unsupported row. Re-rendering
independently from the saved capture reproduced the token arrays and hashes.

| Request | Local prompt tokens | LCP with previous accepted request |
| --- | ---: | ---: |
| Hello | 13 | Not applicable |
| Exact repeat | 13 | 13 |
| Changed early prefix | 15 | 3 |
| Hello with stream=true | 13 | 3 |
| Invalid JSON | Unsupported | Not applicable |

The streaming request's full token sequence equals the original Hello request;
its adjacent LCP is 3 because the immediately previous request is the mutation.
This distinguishes baseline equality from adjacency in a recorded session.
These fake-model captures were explicitly remapped into the pinned local Qwen
domain; they are not observations of a Qwen server's prompt or cache metrics.

## Longer controlled prompt

Reproduce with experiments/prefix-mutations/run.sh. Evidence:
artifacts/prefix-probe-verified-20261027/report.json and report.csv, alongside
literal requests, rendered text and exact token IDs. Every variant is compared
to its named baseline, not the previous row.

| Case | Variant tokens | Shared prefix with named baseline |
| --- | ---: | ---: |
| Plain baseline | 2463 | Not applicable |
| Exact repeat | 2463 | 2463 |
| Generation controls | 2463 | 2463 |
| Early system label | 2464 | 6 |
| User text near start | 2463 | 25 |
| User text near middle | 2463 | 1241 |
| User text near end | 2463 | 2438 |
| Request/message JSON key order | 2463 | 2463 |
| Thinking flag enabled | 2459 | 2459 |
| Tool baseline | 2653 | Not applicable |
| Tool definitions reordered | 2653 | 61 |
| First tool schema keys reordered | 2652 | 58 |
| Tool-history baseline | 2691 | Not applicable |
| Tool argument string whitespace | 2695 | 2661 |
| Null assistant content | Unsupported | Not applicable |

The early label change changes token count too; one changed word is not assumed
to be one changed token. Later user mutations preserve progressively more exact
prefix. Ordinary request JSON key order does not affect this plain rendering,
while tool schema key order changes its serialized text under this template.
These are properties of the inspected pinned rendering, not universal rules
for every model, tokenizer, template or serving normalization.

Enabling thinking removes four trailing template tokens from this baseline.
The variant is a strict prefix of the baseline, so its LCP equals its own full
length even though the two sequences differ. Prefix length alone is insufficient
for determining full equality; the report retains explicit ID equality and
both suffix lengths.

## Still unknown

Actual cached tokens, cache block/page size, eligibility, eviction, prompt-token
agreement with an engine, prefill work and inference latency are all unknown.
The experiment sends zero inference requests. A controlled CUDA engine run must
validate the profile/tokenizer/template and cache metrics/reset before linking
these structural measurements to serving behavior.

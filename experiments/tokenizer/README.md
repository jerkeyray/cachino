# Mac / CPU tokenizer milestone

This experiment needs Go, uv and Python 3.12. It renders prompts and tokenizes
text; it does not load model weights, run inference or need CUDA. Python 3.12
was found locally during setup. Run from the repository root:

```sh
./experiments/tokenizer/run.sh
```

The first run needs network access to download only five tokenizer/config files
from the pinned Qwen repository revision and install the tokenizer environment.
Assets stay in ignored `artifacts/qwen3-8b-tokenizer`; subsequent reconstruction
loads only these local files. Each download is limited to 32 MiB, and the
allowlist excludes all model weights. Existing output paths are not overwritten.
The tokenizer environment is separate from the dependency-free base analysis
project. Direct dependencies are fixed to Transformers 4.57.6 and Jinja2 3.1.6;
the full resolved environment is in analysis/tokenizer/uv.lock. Experiment
commands use --frozen to preserve that resolution.

## What it does

1. Fetch and hash the tokenizer assets at the exact revision in model.json.
2. Validate/load the tokenizer and runtime, then produce five synthetic captures
   through the existing Cachino demo. Failed setup does not create a capture run.
3. Explicitly remap fake-model requests into the local pinned Qwen domain.
4. Render supported messages/tools with fixed generation/thinking flags.
5. Compare direct template tokenization with rendered-text encoding using
   add_special_tokens=False, and check decoded text round-trips.
6. Save rendered prompts, exact token ID arrays, ID hashes and profile provenance.
7. Compare adjacent accepted requests within explicitly labeled sessions.

The output is `artifacts/tokenizer-<timestamp>/reconstructed/summary.json`, plus
per-request `.prompt.txt` and `.tokens.json` files. They contain private prompt
content, so the entire artifacts directory is ignored. The source trace remains
unchanged. Request IDs connect each reconstruction to its original capture.

Verified relationships in the 2026-10-27 run: four rendered requests and
one rejected invalid-JSON request; original/repeated/streaming forms have equal
local token sequences; the changed user message differs; the repeat's LCP spans
its whole prompt. The script exits unsuccessfully if these checks do not hold.

## Supported boundary

String-content system/user/assistant/tool messages, function schemas, function
calls, reasoning_content and tool result IDs are accepted. Tool arguments are
preserved as supplied rather than reserialized. The last message must be user
or tool. Null/list content, multimodal input, assistant prefills, developer
roles, unknown fields and prompt controls such as tool_choice, response_format
or chat_template_kwargs are rejected until normalization is defined. Unsupported
requests stay in the summary and break the adjacent-comparison chain.

Sessionless requests are not grouped. Comparisons use capture acceptance order,
not inferred semantic turn order. LCP is a structural diagnostic and does not
predict cache residency or actual cached tokens. No cache granularity is assumed.

The output records model revision, asset hashes, template hash, template flags,
library versions and any explicit model remapping. It always marks
engine_validated=false. Local-library agreement does not prove engine token
identity or equality with provider prompt_tokens. Real serving validation is a
later, separate experiment.

Snapshot verification rejects additional files and symlinks. Optional tokenizer
files such as chat_template.jinja, added_tokens.json or special_tokens_map.json
could otherwise change the loader's behavior outside the recorded hashes.
Keep the downloaded snapshot directory unchanged.

To diagnose setup independently of capture:

```sh
uv run --project analysis/tokenizer --python 3.12 --no-python-downloads python \
  analysis/reconstruct.py --assets artifacts/qwen3-8b-tokenizer --check-setup
```

## Verified status

The user completed setup in a normal terminal on 2026-10-27. The snapshot and
all four supported reconstructions were subsequently revalidated directly from
the saved raw capture. Original/repeated/streaming Hello prompts each contain
13 local tokens with identical ID hashes. The mutation contains 15 tokens and
shares 3 leading tokens with Hello. The invalid JSON request remains unsupported.

Evidence: artifacts/tokenizer-20261027T174615Z/reconstructed/summary.json.
See [the measured CPU observations](../../docs/archive/cpu-prefix-observations.md)
and [the longer controlled mutation experiment](../prefix-mutations/README.md).

The warning about missing PyTorch/TensorFlow/Flax is expected. No weights or
model execution framework are required for this tokenizer-only stage. All
results remain local reconstructions, with engine_validated=false.

## Primary sources

- Pinned source revision, verified through the model API and repository tree:
  https://huggingface.co/Qwen/Qwen3-8B/tree/b968826d9c46dd6066d109eabc6255188de91218
- The source tokenizer configuration contains the supplied chat template:
  https://huggingface.co/Qwen/Qwen3-8B/raw/b968826d9c46dd6066d109eabc6255188de91218/tokenizer_config.json
- Transformers template tokenization/special-token guidance:
  https://huggingface.co/docs/transformers/v4.55.4/chat_templating

This pins source data for CPU reconstruction. It does not select or validate
an inference engine, its template overrides or its cache behavior.

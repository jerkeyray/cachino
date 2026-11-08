# Examples

Two small checks a fresh checkout can run. Neither needs a model, an engine or
a GPU, and both are synthetic, so nothing private is involved. Their expected
output is committed next to them.

## one-block: an eviction threshold

```sh
python3 examples/one-block/run.py
```

Standard library only. A synthetic trace of three interleaved sessions, as
integer token IDs, is replayed through Cachino's free-queue predictor (the
pinned vLLM prefix-cache rules, 32-token blocks) at every cache size. At 35
blocks nothing is evicted; at 34, one request loses exactly one block (32
tokens) of reuse. A naive predictor that assumes every matching prefix is still
cached misses exactly that block. The study found the same shape on a real
trace at 487 and 488 blocks, where the engine matched every prediction
([repetitions](../docs/records/repetitions-20261108.md)). Here there is no
engine, so these are predictions only. Expected output:
[one-block/expected.txt](one-block/expected.txt). `analysis/test_examples.py`
checks it.

## two-turn: a chat-template boundary

```sh
examples/two-turn/run.sh
```

Needs Go, uv and two pinned tokenizer snapshots, fetched once from the Hugging
Face Hub at pinned revisions and hash-checked:

```sh
uv run --project analysis --offline --no-python-downloads python analysis/tokenizer_assets.py \
  --pin experiments/tokenizer/qwen3-0.6b.json --out artifacts/qwen3-0.6b-tokenizer
```

```sh
uv run --project analysis --offline --no-python-downloads python analysis/tokenizer_assets.py \
  --pin experiments/tokenizer/qwen2.5-0.5b-instruct.json --out artifacts/qwen2.5-0.5b-instruct-tokenizer
```

The script runs the Cachino proxy in front of the bundled fake model, sends a
two-turn chat (turn 2 resends turn 1 with the reply appended, as an agent
does) and captures it, once naming Qwen3 and once Qwen2.5. The template check
then renders each captured request to exact token IDs and reports where turn 2
stops sharing turn 1's prompt:

- Qwen3 with thinking off prompts generation with an empty thinking cue that
  history omits, so turn 2 drops those 4 tokens and everything after them.
- Qwen2.5 renders history the way it prompted generation, so turn 2 is
  append-only.

Captures go to a new directory under ignored `traces/`. Expected output:
[two-turn/expected.txt](two-turn/expected.txt).

# Controlled prefix mutations on CPU

Prerequisite: complete experiments/tokenizer/run.sh once so the pinned snapshot
and locked tokenizer environment are installed. This experiment then runs offline
on a Mac/CPU, without PyTorch, weights, an inference engine, or a GPU:

```sh
./experiments/prefix-mutations/run.sh
```

An optional first argument selects a new output directory. Existing outputs are
never overwritten. The experiment builds deterministic 128-line requests and
compares each named variant to its declared baseline, not the preceding variant.
The original Suez demo instead compares adjacent accepted requests in a session.

Each output directory contains literal `.request.json` fixtures, rendered
`.prompt.txt`, exact `.tokens.json`, `report.json` and `report.csv`. The report
records model/revision, tokenizer asset hashes, template hash, library versions,
script hash and the profile for every case. All artifacts remain ignored.
The CSV gives token counts and shared-prefix lengths. It includes suffix lengths
for both baseline and variant so a shorter prefix-of-baseline sequence is not
mistaken for identical input.

## Controls and mutations

- Plain baseline, exact repeat, generation-control changes and request JSON
  key-order changes: control cases must render identical token IDs.
- ALPHA -> BRAVO in the early system label: observe the actual token divergence.
- alpha -> omega at the first, middle and final numbered lines: measured token
  divergence must follow mutation-position order; character offsets are recorded
  separately and are not assumed to be token indices.
- Thinking flag: compare the same request using an explicitly changed profile.
- Tool order and schema key order: preserve their literal order and measure what
  the pinned template actually serializes.
- Tool-history argument whitespace: compare semantically equivalent JSON strings
  without silently canonicalizing the argument string.
- Null assistant content: deliberately reject this unsupported normalization.

For every supported fixture, direct template tokenization must equal encoding
of the rendered text with add_special_tokens=False, and decoding must round-trip.
These are local consistency checks, not validation against an inference engine.

## Interpretation boundary

Shared-prefix lengths and remaining suffix lengths describe token structure.
They are not cached-token counts, uncached work or latency measurements. The
JSON explicitly stores cached_tokens=null, cache_granularity=null,
engine_validated=false and inference_requests_sent=0. It never rounds to an
assumed cache block size.

These request files are inputs to local rendering, not launch-ready engine
probes. A later engine adapter must apply exactly the recorded profile flags,
verify the server's token rendering and discover its actual reuse eligibility,
cache granularity, reset behavior and metric semantics. Only then can cold,
repeated and mutated serving conditions be compared.

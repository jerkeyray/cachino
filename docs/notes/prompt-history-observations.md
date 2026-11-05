# CPU prompt-history observation

Executed on 2026-11-03 using the existing 2026-10-27 raw capture and pinned Qwen
reconstruction. No tokenizer rerun, inference call, engine installation or GPU
execution. The source model in these captures is the fake fixture; the original
reconstruction explicitly remapped it to Qwen. This is not a coding-agent result.

## Observation

| Accepted request sequence | Adjacent prompt LCP | Best earlier prompt LCP | Interpretation |
| --- | --- | --- | --- |
| 1 | Not applicable | Not applicable | First observed prompt in the session |
| 2 | 13 | 13 | Repeat of the first prompt |
| 3 | 3 | 3 | Mutated prompt |
| 4 | 3 | 13 | Returns to the original prompt; an older request matches better |
| 5 | Unknown | Unknown | Intentional invalid JSON remains excluded |

Request 4's selected earlier match is request 2 (ties choose the latest prior
prompt). The actual Qwen token sequence has 13 tokens, so all 13 match that
earlier prompt even though only 3 match the immediately preceding mutation.

This is a concrete reason not to treat adjacent LCP as the complete potential
reuse of a workload. It does not establish that 13 tokens were eligible for or
hit an actual engine cache. Alignment, final-token handling, generated output,
other sessions, processing order and residency remain outside this report.

## Reproduction

From the Suez root, after the named existing artifacts are available:

```sh
python3 analysis/reuse.py \
  artifacts/tokenizer-20261027T174615Z/reconstructed \
  --trace-dir artifacts/tokenizer-20261027T174615Z/capture \
  --out artifacts/prompt-history-20261103-verified
```

Output directories must be new; choose a different output name to repeat the
command. Raw captures and original reconstructions are not changed. The saved
output contains `summary.json` and `report.md`, including analysis source hashes,
with no copied prompt text or
token arrays. Artifacts remain ignored; the command is not a substitute for
distributing the permitted source fixture and its provenance in a later release.

Input reconstruction summary SHA-256:
`74213df54ba784a068f6b0c81f85818cad6c50cc57b24bdeb2c60228597d94cd`.
The reader verifies source request hashes, row coverage/order, identity labels,
token-file lengths and compact-ID hashes. It does not regenerate tokenization
or authenticate a jointly altered manifest and token dataset.

History is restricted to explicit session/source-model lanes and is cleared at
unsupported/unverified exchanges. Earlier accepted prompts are observed inputs,
not proof of earlier engine completion. Missing labels do not create a session.
Returned output IDs are deliberately not appended as processed KV history.

## Next validation

Use the [measurement contract](prefill-measurement-contract.md) to correlate
returned vLLM prompt IDs and hit counts. Only then compare structural candidates
with actual engine behavior under calibrated cold, warm and pressure conditions.

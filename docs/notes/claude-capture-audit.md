# What the first Claude capture establishes

Source: the two-call, read-only compatibility run dated 2026-10-29. An offline
audit was executed on 2026-11-01 with Python 3.14. The private raw capture remains
unchanged. This is one synthetic task, not a representative coding benchmark.

Regenerate into a new private directory:

```sh
python3.14 analysis/audit.py traces/claude-20261029-readonly-01/capture \
  --response-encoding-override zstd --out artifacts/my-claude-audit
```

The override is needed only because this run predates encoding metadata. It is
explicit in the derived report. Future captures use recorded response encoding.

## Exact structural observations

| Request section | Observation from call 1 to call 2 |
| --- | --- |
| `/system/0/text` | A `cch` value inside the billing-header text changed; the other two system blocks stayed identical. This text is inside the request body, not an HTTP header captured separately. |
| `/tools` | Ordered Read tool schema stayed identical. |
| `/messages/0` | Both original text strings stayed identical. A `cache_control` property disappeared from the second text block. |
| `/messages/1` | Added assistant thinking and Read tool-use blocks. |
| `/messages/2` | Added the user-role tool result linked by the preceding tool-use ID. |
| Other top-level fields | Model, metadata, max_tokens, output_config and stream stayed identical. |

Both requests omitted the `thinking` parameter. A thinking block nevertheless
appeared in submitted history. The runner set `MAX_THINKING_TOKENS=0`, but that
environment intent does not establish that reasoning was disabled upstream.

Each transport completed with HTTP 200, intact bodies and decoded message_stop.
Native usage was input1742/cache_read0/output66 followed by input250/cache_read1664/
output328. Those native fields remain separate; the audit invents no cache ratio.

## What the audit does not establish

JSON property order is not necessarily model prompt order. Value fingerprints
are over parsed JSON with declared serialization, not raw byte substrings or
token IDs. Changing system text does not prove a provider fed that text to its
model, discarded every later cached token, or paid a particular prefill cost.
The reported cache reads and the changed first system block coexist; explaining
that requires provider/rendering or controlled engine evidence, not an assumed
normalization mechanism.

The client supplied a session label, but no semantic turn/attempt labels. The
audit uses acceptance sequence and matching tool IDs and does not fabricate those
missing fields. No causal speedup follows from the two observed durations.

## Audit output contract

`audit.json` records integrity, native usage, block shapes, matching history tool
IDs, per-component ordered/value equality and bounded JSON-pointer changes with
before/after fingerprints. `audit.md` provides an inspectable summary. Text,
thinking content and tool results remain in private source sidecars. Unsupported
or incomplete requests remain rows and break adjacent comparisons; unlabeled
sessions are never grouped. Existing output directories are refused.

Future Claude runs also snapshot an explicit allowlist of source files, the
runner and task before dispatch; manifests hash those snapshots and the built
binary. Earlier runs' missing source snapshots are not retroactively invented.

Next: focused literature/artifact review and a frozen real task on an open-source
harness. No GPU or new provider calls were needed for this audit. Actual engine
cache validation remains the later GPU gate.

# Raw capture format, schema 1

A capture run is a newly created directory. `events.jsonl` and body sidecars
are the source of truth; SQLite and reports are regenerable derivatives.
There are no Go dependencies beyond the standard library. The Python importer
uses the standard library and runs in the uv-managed analysis environment.

## Lifecycle and ordering

Each accepted supported model API request gets a random 128-bit `request_id` and an
increasing run-local `sequence`, assigned while its start event is persisted.
That sequence is acceptance order at the capture layer, not inferred agent-turn
order. Concurrent finishes may appear in any order. Each JSONL line is either:

```json
{"event":"started","record":{"schema_version":1,"request_id":"...","sequence":1,"state":"in_progress"}}
```

or `finished` with the final record. The example is abbreviated: actual records
include all the metadata and body descriptors described below. A start event
without a finish remains an unfinished request, never a successful zero-byte
capture. The importer ignores an unterminated final log fragment with a warning;
malformed terminated lines and inconsistent identities fail inspection.

`X-Cachino-Session`, `X-Cachino-Turn`, and `X-Cachino-Attempt` are optional caller-supplied
labels. Turn/attempt must be nonnegative integers. `-session` supplies a default
session; absent labels remain unknown. Cachino strips these three headers before
forwarding to the model. It never infers sessions from API keys, auto-increments
semantic turns, or retries requests. The response adds `X-Cachino-Request-ID` for
correlation. Missing/invalid capture metadata or unavailable initial storage
prevents forwarding and returns 503; no unrecorded model request is sent.

Final transport states are `completed`, `cancelled`, `aborted`, or
`downstream_error`. A completed HTTP exchange may still be HTTP 400/500 or carry
an SSE error. A post-header upstream disconnect triggers the standard proxy's
abort; cancellation takes precedence when the incoming context is cancelled.
States describe what the proxy observed, not inferred root causes. A crash or
forced termination leaves `in_progress` when no finish was persisted.

## Bodies and limits

Each request owns `<request_id>/request.body` and `response.body`, storing exact
observed body bytes up to the configured limit (default 16 MiB each). Request
bytes are observed as the upstream consumes them. Response bytes are observed
as the downstream writer accepts them. These are HTTP payload observations,
not a packet capture; HTTP framing is excluded, and upstream transports may
perform content decoding. Headers other than response content type, query
strings, and upstream credentials are not persisted. Body text is not redacted.

Each final body descriptor includes:

| Field | Meaning |
| --- | --- |
| `path` | Run-relative sidecar path |
| `observed_bytes` | Bytes seen at the observation point |
| `stored_bytes` | Bytes successfully written to the sidecar |
| `sha256_observed` | SHA-256 of all observed bytes, even when storage is capped |
| `sha256_stored` | SHA-256 of stored bytes, allowing prefix integrity checks |
| `complete` | Complete incoming body consumed, or response handler completed |
| `truncated` | Stored byte count is below observed count |
| `capture_error` | Sidecar write, sync, or close failed |

A body can be both transport-complete and storage-truncated. A partial body hash
covers only the bytes observed before interruption. The importer validates
stored length/hash; only complete, untruncated, error-free bodies are parsed
for request metadata and upstream usage. Truncated prefixes remain inspectable
raw evidence but never silently stand in for a complete prompt or response.

Capture writes synchronously without accumulating full bodies in memory. File
writes can add latency, so this is not a performance-neutral instrument. Start
and finish events are synced; bodies are synced before finish persistence.
Run/subdirectories use mode 0700 and files 0600. A new run path must not exist.
A midstream sidecar error is recorded and logged by request ID while HTTP keeps
flowing. A log failure is logged, latches the store as failed, and prevents new
captures; an earlier start without a finish remains visible on recovery. This
supports process-interruption inspection, not a guarantee against every form
of filesystem/power-loss corruption. Orphan sidecars from failed starts are
not considered accepted requests.

SIGINT/SIGTERM allows 10 seconds for graceful server shutdown, then closes
active connections. The store waits for final handler records before closing
its log. SIGKILL cannot run finalizers.

## Reports and metric limits

`analysis/report.py` validates records and sidecars, extracts UTF-8 JSON/SSE
usage, reports error/completion states and creates an optional SQLite index.
Unsupported content encoding or malformed payloads remain visible as parse
errors. SQLite `requests` contains one row per accepted request, ordered by
`sequence`; `summary_json` retains the inspection fields. It refuses to overwrite
an existing database. Raw usage remains in the immutable body sidecar.

`prompt_tokens` and `cached_tokens` are nullable. Zero is a known measurement,
not the replacement for missing/null/malformed usage. The report includes cache
metric coverage and a token-weighted ratio only where both counts are known.
It does not equate repeated request hashes with actual cache reuse.

The report accepts OpenAI's `usage.prompt_tokens_details.cached_tokens` and
DeepSeek's `usage.prompt_cache_hit_tokens` ([provider documentation](https://api-docs.deepseek.com/guides/kv_cache/)).
`cache_metric_source` records the field used. When both fields are present,
they must contain valid, equal nonnegative integers; disagreement produces null
`cached_tokens` and `cache_metric_conflict=true`. Malformed or missing counts
stay unknown. The last usage object replaces earlier usage, including unknowns.
Counts are provider-reported observations, not validation of local Qwen IDs.

For SSE, the report checks `[DONE]`, JSON parse errors and in-band error events
separately from transport completion. A clean HTTP EOF without `[DONE]` is
reported as such. The proxy itself never repairs or invents SSE events.

The captured incoming `path` selects the report's protocol: `openai` for chat
completions, `anthropic` for Messages, and `anthropic_count_tokens` for token
counting. A configured gateway prefix affects only the outgoing route. Messages
streams report `message_stop` independently and leave `sse_done` null. These
markers identify termination events, not full validation of every event's order
or schema. In-band errors and parse errors remain separate.

Messages usage is retained in `anthropic_usage` with native field names. Fields
from `message_start.message.usage` and subsequent cumulative usage deltas are
merged by replacement, never summed. Normalized `prompt_tokens`/`cached_tokens`
remain null for Messages until provider-specific denominator semantics are
validated. Token-count responses never enter cache-ratio calculations. Local
Qwen reconstruction rejects all non-chat-completion routes, even with explicit
model remapping or superficially compatible message bodies.

`response_content_encoding` records the upstream HTTP Content-Encoding header;
raw sidecars and their hashes always cover the original observed payload. The
report can decode one zstd frame with Python 3.14's `compression.zstd`, bounded
to 16 MiB of decoded bytes. Unsupported encodings, unavailable decoders, invalid,
truncated, concatenated or oversized frames produce `response_decode_error`
and unknown usage. The base Python environment still requires no dependencies.
Legacy traces lacking encoding metadata may use explicit
`--response-encoding-override zstd`; reports label the override and do not change
the source. An absent header in an old trace does not establish identity encoding.

Opt-in session limits buffer/validate input before forwarding; trace middleware
wraps that admission layer, so it also captures local 400/403/413 rejections.
Do not count those as upstream model requests. Accepted payload bytes are not
normalized, but admission timing differs from unbuffered proxy mode.

`duration_ms` uses the proxy's monotonic elapsed time from handler entry to
pre-finalization; it includes forwarding and capture overhead up to that point.
`first_response_write_ms` is the first nonempty successful downstream Write.
It can correspond to a role event or headers-related content. Neither field is
model TTFT, engine prefill time, pure network time, or a GPU measurement.

Exact token reconstruction, local chat-template pinning, token LCP, cache
attribution, real-harness parity and capture-overhead calibration remain open.

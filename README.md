# Cachino

Cachino is an early Go proxy for studying how coding-agent requests affect prefix
cache reuse. It forwards `POST /v1/chat/completions`, `/v1/messages`, and
`/v1/messages/count_tokens` to a configured
upstream, captures raw request/response bodies, and produces offline JSON reports
and a derived SQLite index. A deterministic fake endpoint supports development.

## Findings so far

All engine results below come from vLLM at commit `2173d712`, built for the Mac
CPU, serving Qwen3-0.6B with 32-token blocks. They concern cache accounting,
not GPU latency.

- **Token parity.** The engine's prompt token IDs equal Cachino's local render
  exactly, including through the proxy ([M4](docs/notes/m4-cpu-results-20261107.md)).
- **Reuse rule.** Reuse equals the exact shared token prefix, capped at prompt
  length minus 1, rounded down to whole blocks.
- **Eviction is predictable.** Under a capped cache, a model of the pinned free
  queue predicted every hit count, and prompts are evicted from the end
  backwards ([E4](docs/notes/e4-cpu-results-20261107.md)).
- **Real agent traffic.** In a mini-swe-agent session, every turn's reuse stops
  where the template's empty thinking cue is replaced by the previous reply, so
  each reply is prefilled again ([live session](docs/notes/live-cpu-results-20261107.md)).
- **One intervention, with a cost.** Keeping the cue in history let the engine
  reuse each reply and cut recomputation by about half, but changed the model's
  next action ([counterfactual](docs/notes/cue-counterfactual-20261107.md)).

## Engine experiments on CPU

These need the vLLM CPU build and Qwen3-0.6B weights described in the
[CPU build plan](docs/notes/m4-cpu-build-plan.md). No GPU is used.

- [M4 calibration](experiments/cache-ground-truth/README.md): parity, reset and block-rule checks.
- [E4 eviction](experiments/cache-eviction/README.md): predicted versus observed reuse under a capped cache.
- [Live agent session](experiments/agent-task/README.md#live-session-on-the-cpu-engine): capture, per-turn attribution and the cue counterfactual.

## Work on a Mac

Proxy/capture, offline reports and local token reconstruction can run on CPU.
The [CPU tokenizer experiment](experiments/tokenizer/README.md) prepares pinned
Qwen tokenizer assets, exact local token IDs and token-prefix comparisons without
model weights or CUDA:

```sh
./experiments/tokenizer/run.sh
```

First setup needs network access and Python 3.12. The tokenizer experiment has
completed on the Mac and its token IDs/hashes were independently rechecked.
These local reconstructions remain separate from real-engine/cache validation.
Run the longer controlled mutation probe offline after setup:

```sh
./experiments/prefix-mutations/run.sh
```

Read [the measured CPU observations](docs/notes/cpu-prefix-observations.md) for
repeated prompts, mutation positions, tool ordering and template flag effects.

For captured request structure, `analysis/audit.py` compares explicitly labeled
sessions and writes JSON/Markdown evidence with block fingerprints and changed
JSON pointers. It does not interpret JSON differences as model-token divergence.
See [the first Claude capture audit](docs/notes/claude-capture-audit.md) and
[the initial related-work overlap check](docs/notes/related-work-check-20261101.md).

`analysis/reuse.py` now compares reconstructed prompts with both the preceding
prompt and earlier prompts in the same explicit session/model lane. It checks
the derived token hashes and raw capture integrity, clears history at evidence
gaps, and keeps provider usage separate from local tokens. Run it on an existing
reconstruction without loading a tokenizer or calling a model:

```sh
python3 analysis/reuse.py <reconstruction-dir> \
  --trace-dir <original-capture-dir> --out <new-report-dir>
```

The output is structural history, not a resident-cache model. Read the
[saved CPU history result](docs/notes/prompt-history-observations.md),
[measurement contract](docs/notes/prefill-measurement-contract.md) and
[pinned vLLM observation paths](docs/notes/vllm-source-paths.md). The
[focused research plan](docs/notes/research-plan.md) defines the next gates.

The pinned mini-swe-agent loop now uses a small direct HTTP model adapter.
Its two-step fixture run passes capture and exact local token reconstruction,
with a third quota probe rejected separately. Read the
[direct adapter observations](docs/notes/direct-harness-observations.md):
unchanged message history still has a template generation-boundary difference.
That fixture run used no inference; the live CPU session above later confirmed
the boundary on the engine. No agent shell action has run. LiteLLM and OpenAI
SDKs are absent from the active experiment environment.

## Offline milestone

With Go, Python 3.9+ and uv installed, this runs a complete synthetic capture
and verifies the resulting report without binding ports or using a GPU:

```sh
./experiments/offline-capture/run.sh traces/my-offline-run
```

It sends JSON requests, an exact repeat, a mutation, an SSE request and an
invalid request through Cachino. The output directory contains raw body sidecars,
`events.jsonl`, `report.json` and `index.sqlite`. The path must be new.
See [the fixture protocol](experiments/offline-capture/README.md).

## Run locally

Requires Go 1.26 or newer. No dependencies outside the standard library.

In one terminal, start the fake endpoint:

```sh
go run ./cmd/fake-model -listen 127.0.0.1:8081
```

In another, start Cachino:

```sh
go run ./cmd/cachino -listen 127.0.0.1:8080 -upstream http://127.0.0.1:8081
```

Send a request through Cachino:

```sh
curl --fail-with-body http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

The response contains `Hello from the fake model.` Repeat against port 8081
to reach the fake endpoint directly. Stop either process with Ctrl-C.

To see streamed events as they arrive, use `curl -N`:

```sh
curl -N --fail-with-body http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

The fixture emits a role event, two content events, a finish event, then
`data: [DONE]`. Each event is flushed, with an artificial 100 ms pause between
events for inspection. This pacing is unrelated to model latency.

## Structure and behavior

- `cmd/fake-model`: local upstream on port 8081 by default.
- `internal/fakemodel`: fixed JSON or SSE chat-completion response. It requires
  a model and nonempty messages, accepts extra request fields, and limits requests
  to 1 MiB. It is a narrow fixture, not a full API validator. It stops emitting
  on request cancellation or a write/flush error. Usage and cache metrics are
  absent, not zero.
- `cmd/cachino`: proxy on port 8080 by default.
- `internal/trace`: opt-in streaming body capture with bounded disk storage,
  start/finish lifecycle records, body hashes and explicit incomplete states.
- `analysis/report.py`: raw capture validation, per-request/session summaries,
  nullable upstream usage/cache metrics and optional SQLite indexing.
- `cmd/cachino-demo`, `internal/localhttp`: synthetic end-to-end run over real HTTP
  on in-memory connections. These do not validate TCP or GPU behavior.
- `internal/proxy`: Go standard-library reverse proxy. Request body bytes pass
  through without JSON parsing. Upstream status, response body and end-to-end
  headers pass back to the client. HTTP hop-by-hop headers are removed and Host
  targets the upstream. Authorization is forwarded; bodies and credentials are
  not logged. Transport failures before a response return HTTP 502.

For SSE, the standard reverse proxy flushes immediately without accumulating
the full response. Client cancellation or closing the response body cancels
the upstream request. Once HTTP 200 headers have been sent, an upstream
connection failure interrupts the body; Cachino cannot replace the status with
502 and does not invent a finish event or `[DONE]`. An in-band SSE error event
passes through unchanged. A stream ending normally without `[DONE]` also passes
through: Cachino does not yet interpret or validate stream semantics.

`-upstream` takes an HTTP(S) origin without `/v1`, credentials, query or fragment.
Cachino preserves the incoming route and query. Other routes return 404, and other
methods on supported routes return 405. Both commands default to loopback.
`-messages-prefix /anthropic` prepends that gateway path only to Messages and
token-count routes, leaving chat completions unchanged. See
[Claude Code capture preparation](experiments/claude-capture/README.md).

For bounded captures, `-session-request-limit N` enables admission across all
callers of that proxy instance. `-session-protocol messages` is the default and
admits Messages/token-count routes. `-session-protocol openai` admits only chat
completions. Both enforce
`-session-model` (default `deepseek-flash`), `-session-input-bytes` (128 KiB),
and `-session-output-tokens` (512). OpenAI mode requires exactly one of
`max_tokens` or `max_completion_tokens`, permits `n` only when it is 1, and
rejects `best_of`. Missing output limits, duplicate top-level fields and
ambiguous aliases are rejected. Rejected payloads are never rewritten into
eligible ones. This mode buffers request bodies and changes admission timing;
leave it disabled for transparent streaming/overhead measurements. The quota
includes token counting and upstream errors and resets when Cachino restarts.

To exercise two admitted JSON/SSE calls and one local quota rejection without
an SDK, model or TCP listener:

```sh
GOTOOLCHAIN=local go run ./cmd/cachino-demo -limited-openai \
  -out traces/my-openai-admission-check
```

This is a synthetic HTTP/capture check; it does not validate harness SDK behavior
or inference/cache performance. Usage remains unknown in the fixture responses.

Local token reconstruction is available for a documented supported subset.
Engine token equivalence and cache attribution are validated on the vLLM CPU
backend (see Findings so far); GPU latency is not. The fake endpoint itself
does not simulate tokenizer or KV-cache behavior. No GPU is needed for these
HTTP checks.

## Capture and inspect

Start Cachino with capture enabled (in place of the earlier Cachino command):

```sh
go run ./cmd/cachino -upstream http://127.0.0.1:8081 \
  -trace-dir traces/my-session -session my-session
```

Capture is off by default. The capture directory must not exist yet. It records
only supported model API POSTs. Caller headers `X-Cachino-Session`, `X-Cachino-Turn`, and
`X-Cachino-Attempt` supply optional explicit labels, and are stripped before
forwarding. The response has a correlation header `X-Cachino-Request-ID`.

The default body storage cap is 16 MiB per direction, adjustable using
`-trace-body-limit`. Larger bodies continue through the proxy; stored copies
are explicitly truncated. Authorization headers and query strings are excluded
from metadata, but body text remains raw and may contain private content.
Raw traces stay gitignored. Capture writes can add latency and have not been
calibrated for inference performance measurements.

After requests complete, generate a report and a new SQLite index:

```sh
uv run --project analysis --offline --no-python-downloads python analysis/report.py \
  traces/my-session --sqlite traces/my-session/index.sqlite \
  > traces/my-session/report.json
```

The importer verifies stored bytes/hashes, preserves unfinished requests,
detects incomplete SSE termination, and keeps missing usage/cache counts null.
It refuses to overwrite an existing SQLite file. Read
[the trace format and failure policy](docs/notes/trace-format.md) for definitions.

## Verify

```sh
go build ./...
go test -race ./...
go vet ./...
uv run --project analysis --offline --no-python-downloads python -m unittest discover -s analysis -v
```

The default tests cover handler parity and byte preservation. Streaming tests
run real HTTP servers/transports over `net.Pipe`, without binding TCP ports.
A synchronization gate holds the upstream open until the client receives the
first event, establishing incremental delivery without a latency threshold.
Tests also cover client cancellation/body closure, in-band errors and abruptly
truncated responses. These checks do not measure network buffering overhead or
prove real-harness parity.

To also check direct/proxied JSON and SSE parity over loopback TCP:

```sh
go test -race -tags=integration ./...
```

The integration tests require permission to bind local ports.

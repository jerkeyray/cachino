# Proxy reference

Behavior of the `cachino` command and its capture, for anyone running it.

## Forwarding

- Routes: `POST /v1/chat/completions`, `/v1/messages` and
  `/v1/messages/count_tokens`. Other routes return 404; other methods on these
  routes return 405. Both `cachino` and `fake-model` listen on loopback by
  default.
- `-upstream` takes an HTTP(S) origin without `/v1`, credentials, query or
  fragment. The incoming route and query are preserved.
  `-messages-prefix /anthropic` prepends that path only to Messages and
  token-count routes .
- Request bytes pass through unparsed. Status, body and end-to-end headers pass
  back; hop-by-hop headers are removed and Host targets the upstream.
  Authorization is forwarded and never logged. A transport failure before a
  response returns 502.
- SSE is flushed as it arrives. Client cancellation cancels the upstream
  request. After a 200 has been sent, an upstream failure interrupts the body;
  Cachino does not invent a finish event or `[DONE]`, and passes in-band errors
  and streams without `[DONE]` through unchanged.

## Capture

```sh
go run ./cmd/cachino -upstream http://127.0.0.1:8081 \
  -trace-dir traces/my-session -session my-session
```

- Off by default; the capture directory must not exist yet. Only supported model
  POSTs are recorded.
- Optional labels come from `X-Cachino-Session`, `X-Cachino-Turn` and
  `X-Cachino-Attempt`, which are stripped before forwarding. Responses carry
  `X-Cachino-Request-ID`.
- Bodies are stored up to 16 MiB per direction (`-trace-body-limit`); larger
  bodies still pass through and the stored copy is marked truncated.
  Authorization headers and query strings are left out of metadata, but body
  text is raw and may be private. Traces stay gitignored.
- Each event and body is synced to disk, so a crash leaves a complete record up
  to that point. This costs about 15 ms per request on macOS; forwarding alone
  adds under 0.5 ms ([overhead](records/proxy-overhead-20261107.md)). Run
  latency passes with capture off.

Report and SQLite index:

```sh
uv run --project analysis --offline --no-python-downloads python analysis/report.py \
  traces/my-session --sqlite traces/my-session/index.sqlite > traces/my-session/report.json
```

The report verifies stored bytes and hashes, keeps unfinished requests, detects
incomplete SSE and keeps missing usage and cache counts null. Field definitions:
[trace format](records/trace-format.md).

## Bounded sessions

`-session-request-limit N` admits at most N upstream requests per proxy
instance, including retries, errors and token counts; the quota resets on
restart. `-session-protocol messages` (default) admits Messages routes;
`openai` admits only chat completions. Both enforce `-session-model` (default
`deepseek-flash`), `-session-input-bytes` (128 KiB) and `-session-output-tokens`
(512). OpenAI mode requires exactly one of `max_tokens` or
`max_completion_tokens`, allows `n` only when 1 and rejects `best_of`. Rejected
requests are never rewritten. This mode buffers request bodies, so leave it off
for streaming or overhead measurements.

## Active-session limit

`-max-active-sessions N` lets at most N sessions be active at once. A session is
identified by `X-Cachino-Session` (or `-session` when the header is absent) and
stays active while it has a request in flight and for `-session-idle` (default
5s) after its last one, which covers the gap between an agent's turns. A request
from an inactive session waits, first come first served, until a slot frees;
each wait is logged with the session, capture request ID and duration. The
limit sits inside capture, so captured durations include the wait. It keeps
admitted sessions' prefixes resident in a shared cache at the cost of waiting.

`-session-token-budget T` adds a size limit, alone or with the count limit.
Each active session's KV footprint is estimated from its latest request's
`Content-Length` times `-tokens-per-byte` (default 0.25, measured on the
study's captures). A turn proceeds if its footprint plus those of the active
sessions admitted before it fit within T; the oldest session always proceeds.
A younger session whose next turn does not fit is suspended between turns and
waits ahead of sessions that have not started, keeping its age when it resumes. A turn that
fits after its older sessions but would overflow T alongside younger requests
already in flight waits for them to finish, and no younger turn starts
meanwhile, since an in-flight request cannot be paused.

## Fake model and demo

`fake-model` returns a fixed JSON or SSE chat completion. It needs a model and
nonempty messages, limits requests to 1 MiB, pauses 100 ms between SSE events
and reports no usage or cache metrics. `cachino-demo` runs a synthetic capture
over in-memory connections; `-limited-openai` exercises two admitted calls and
one quota rejection.

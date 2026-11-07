# Proxy overhead, 2026-11-07

Run `proxy-overhead-20261107-01` with
[`experiments/proxy-overhead/measure.py`](../../experiments/proxy-overhead/README.md)
at commit `a8f1eeb1`, Go 1.26.1, macOS on Apple M5. 300 measured rounds per mode
after 20 warm-up rounds. Each round sends one request on each of three routes, in
a seeded random order, to the same deterministic fake model on loopback. Each
route reuses one persistent connection. Response bodies were byte-identical
across routes on every request, and the capture held all 640 measured and
warm-up requests.

## Findings

Paired difference from the direct route, median with a 95% bootstrap interval:

| Measure | Direct median | Cachino, no capture | Cachino, capture on |
| --- | --- | --- | --- |
| JSON, full response | 0.35 ms | +0.24 ms (0.23 to 0.26) | +15.5 ms (15.4 to 15.6) |
| SSE, first event | 0.68 ms | +0.40 ms (0.39 to 0.41) | +5.9 ms (5.7 to 6.0) |
| SSE, full stream | 404.9 ms | +0.48 ms (0.40 to 0.54) | +17.9 ms (17.7 to 18.1) |

1. **Forwarding alone costs well under a millisecond.** Without capture, Cachino
   adds about 0.24 ms to a JSON exchange and 0.4 ms to the first SSE event. The
   95th-percentile differences are 0.56 ms and 0.66 ms.
2. **Capture costs milliseconds, from durability syncs.** Each captured request
   appends a started event, writes both body files and appends a finished event,
   and calls `File.Sync` after each (`internal/trace/trace.go`, `append` and
   `finish`). Several of these happen before the response completes. On macOS,
   Go's `File.Sync` issues `F_FULLFSYNC`, a full flush to storage, so a few syncs
   plausibly explain the 15.5 ms. The 5.9 ms on the first SSE event is consistent
   with the one started-event sync made before forwarding. The per-sync
   attribution is inferred from the code, not separately timed.

## Consequences

- Latency measured through Cachino with capture on must not be compared with
  latency measured without it. Engine-reported timing (`metrics`) is unaffected;
  client and proxy timing are not.
- On a GPU server, where prefill of a long prompt can take tens of milliseconds,
  a 15 ms capture cost is not negligible.
- Decision, 2026-11-07: the per-event sync stays, because it keeps an exact,
  verifiable record up to any crash. Latency passes run with capture off and
  pair with a separate capture pass for attribution.

## Limits

- One machine, one run, loopback only, a fake upstream with tiny bodies. Large
  bodies would add write cost.
- The sync cost depends on the OS and disk. Linux `fsync` on a server disk will
  differ from macOS `F_FULLFSYNC`.
- The fake model's 100 ms SSE pauses are identical on every route and cancel in
  the paired differences.

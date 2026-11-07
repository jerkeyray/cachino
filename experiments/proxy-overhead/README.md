# Proxy overhead

Measures how much latency Cachino adds when forwarding, with capture off and
on. The fake model is the upstream, so engine noise does not hide the proxy's
cost. Results: [proxy overhead note](../../docs/notes/proxy-overhead-20261107.md).

```sh
python3 -B experiments/proxy-overhead/measure.py --out artifacts/proxy-overhead-NEW
```

The script builds `cachino` and `fake-model`, starts one fake model and two
proxies on free loopback ports (one writing a capture), and sends paired
rounds of JSON and SSE requests. It writes `results.json` and the capture into
the new directory, then stops everything it started. Options: `--rounds`
(default 300), `--warmup` (20), `--seed` (1).

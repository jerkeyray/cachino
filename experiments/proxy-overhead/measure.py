"""Measure the latency Cachino adds when forwarding, with capture off and on.

Three routes reach the same deterministic fake model on loopback: direct, through
Cachino without capture, and through Cachino writing a capture. Each round sends
one request on every route in a seeded random order, so slow drift on the
machine affects all routes alike, and differences are taken within a round.
Each route keeps one persistent connection, so connection setup is not counted.

JSON requests are timed from send to the full body. SSE requests are timed to
the first event line and to the end of the stream; the fake model pauses 100 ms
between events, which is identical on every route and cancels in the
differences. Response bodies must be identical across routes.

This measures proxy cost on this machine's CPU and loopback stack. It says
nothing about model latency, and it is not a GPU measurement.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import random
import socket
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BODY_JSON = json.dumps({"model": "fake-model", "messages": [{"role": "user", "content": "Hello"}], "stream": False}).encode()
BODY_SSE = json.dumps({"model": "fake-model", "messages": [{"role": "user", "content": "Hello"}], "stream": True}).encode()


def percentile(values, q):
    """Nearest-rank percentile of a nonempty list, q in [0, 100]."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))
    return ordered[int(rank) - 1]


def bootstrap_median_ci(values, resamples=2000, seed=0):
    """Return (median, low, high): a seeded 95% percentile-bootstrap interval."""
    rng = random.Random(seed)
    n = len(values)
    medians = sorted(statistics.median(rng.choices(values, k=n)) for _ in range(resamples))
    return statistics.median(values), medians[int(0.025 * resamples)], medians[int(0.975 * resamples) - 1]


def summarize(samples):
    """samples: {route: [ms per round]}; return per-route stats and paired differences."""
    out = {"routes": {}, "paired_differences_vs_direct": {}}
    for route, values in samples.items():
        out["routes"][route] = {"n": len(values), "median_ms": statistics.median(values),
                                "p95_ms": percentile(values, 95), "mean_ms": statistics.fmean(values)}
    for route in samples:
        if route == "direct":
            continue
        diffs = [a - b for a, b in zip(samples[route], samples["direct"])]
        median, low, high = bootstrap_median_ci(diffs)
        out["paired_differences_vs_direct"][route] = {"median_ms": median, "ci95_ms": [low, high],
                                                      "p95_ms": percentile(diffs, 95)}
    return out


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(port, process, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError("process exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError("not ready")
            time.sleep(0.05)


def timed(conn, body, stream):
    """Send one request; return (elapsed_ms, first_event_ms or None, body bytes)."""
    start = time.perf_counter_ns()
    conn.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    if response.status != 200:
        raise RuntimeError("status %d" % response.status)
    first = None
    chunks = []
    if stream:
        while True:
            line = response.readline()
            if not line:
                break
            if first is None and line.startswith(b"data:"):
                first = (time.perf_counter_ns() - start) / 1e6
            chunks.append(line)
    else:
        chunks.append(response.read())
    return (time.perf_counter_ns() - start) / 1e6, first, b"".join(chunks)


def run(out, rounds, warmup, seed):
    out.mkdir(mode=0o700, parents=True)
    bin_dir = out / "bin"
    bin_dir.mkdir()
    env = dict(os.environ, GOTOOLCHAIN="local")
    for name in ("cachino", "fake-model"):
        subprocess.run(["go", "build", "-o", str(bin_dir / name), "./cmd/" + name], cwd=ROOT, env=env, check=True, timeout=120)
    ports = {name: free_port() for name in ("fake", "proxy", "proxy_capture")}
    upstream = "http://127.0.0.1:%d" % ports["fake"]
    commands = {
        "fake": [str(bin_dir / "fake-model"), "-listen", "127.0.0.1:%d" % ports["fake"]],
        "proxy": [str(bin_dir / "cachino"), "-listen", "127.0.0.1:%d" % ports["proxy"], "-upstream", upstream],
        "proxy_capture": [str(bin_dir / "cachino"), "-listen", "127.0.0.1:%d" % ports["proxy_capture"], "-upstream", upstream,
                          "-trace-dir", str(out / "capture"), "-session", "proxy-overhead"],
    }
    processes = {}
    try:
        for name, command in commands.items():
            log = (out / (name + ".log")).open("x")
            processes[name] = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            wait_ready(ports[name], processes[name])
        route_ports = {"direct": ports["fake"], "proxy": ports["proxy"], "proxy_capture": ports["proxy_capture"]}
        conns = {route: http.client.HTTPConnection("127.0.0.1", port, timeout=10) for route, port in route_ports.items()}
        rng = random.Random(seed)
        result = {"rounds": rounds, "warmup": warmup, "seed": seed, "machine": os.uname().machine,
                  "go": subprocess.check_output(["go", "version"], text=True).strip(),
                  "commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()}
        for mode, body, stream in (("json", BODY_JSON, False), ("sse", BODY_SSE, True)):
            total = {route: [] for route in route_ports}
            first = {route: [] for route in route_ports}
            reference = None
            for index in range(warmup + rounds):
                order = list(route_ports)
                rng.shuffle(order)
                for route in order:
                    elapsed, first_ms, data = timed(conns[route], body, stream)
                    if reference is None:
                        reference = data
                    elif data != reference:
                        raise RuntimeError("%s %s body differs from the first response" % (mode, route))
                    if index >= warmup:
                        total[route].append(elapsed)
                        if stream:
                            first[route].append(first_ms)
            result[mode] = {"total": summarize(total)}
            if stream:
                result[mode]["first_event"] = summarize(first)
        for conn in conns.values():
            conn.close()
    finally:
        for process in processes.values():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="new private directory")
    parser.add_argument("--rounds", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(run(args.out, args.rounds, args.warmup, args.seed), indent=2))


if __name__ == "__main__":
    main()
